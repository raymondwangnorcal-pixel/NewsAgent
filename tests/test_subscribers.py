from dataclasses import replace

import pytest

from news_agent.mailer.models import EmailSettings
from news_agent.mailer import subscribers
from news_agent.mailer.smtp import build_message
from news_agent.notifications.base import ConfigurationError

TOKEN='12345678-1234-4234-8234-123456789abc'
def settings():
    return EmailSettings('smtp.gmail.com',587,'sender@example.com','secret','sender@example.com',('legacy@example.com',))

@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('NEWSAGENT_SUBSCRIBERS_ENABLED','true')
    monkeypatch.setenv('SUPABASE_URL','https://project.supabase.co')
    monkeypatch.setenv('SUPABASE_SECRET_KEY','test-key')


def test_disabled_integration_keeps_legacy_recipients(monkeypatch):
    monkeypatch.delenv('NEWSAGENT_SUBSCRIBERS_ENABLED',raising=False)
    assert subscribers.for_delivery(settings()).recipients==('legacy@example.com',)


def test_reads_all_pages_and_never_falls_back_to_manual_list(configured,monkeypatch):
    pages=[[{'id':'00000000-0000-4000-8000-000000000001','email':'one@example.com','unsubscribe_token':TOKEN}],
           [{'id':'00000000-0000-4000-8000-000000000002','email':'two@example.com','unsubscribe_token':TOKEN}],[]]
    monkeypatch.setattr(subscribers,'PAGE_SIZE',1)
    monkeypatch.setattr(subscribers,'request',lambda *args,**kwargs: pages.pop(0))
    result=subscribers.for_delivery(settings())
    assert result.recipients==('one@example.com','two@example.com')
    assert result.unsubscribe_tokens['one@example.com']==TOKEN


def test_test_editions_and_gate_alerts_only_go_to_administrator(configured,monkeypatch):
    def unexpected(*args,**kwargs):raise AssertionError('must not query public subscribers')
    monkeypatch.setattr(subscribers,'request',unexpected)
    for kind in ['test','gate_alert']:
        result=subscribers.for_delivery(settings(),kind)
        assert result.recipients==('sender@example.com',)
        assert not result.unsubscribe_tokens


def test_unavailable_store_and_invalid_rows_fail_closed(configured,monkeypatch):
    def unavailable(*args,**kwargs):raise ConfigurationError('Subscriber store unavailable')
    monkeypatch.setattr(subscribers,'request',unavailable)
    with pytest.raises(ConfigurationError):subscribers.for_delivery(settings())
    monkeypatch.setattr(subscribers,'request',lambda *args,**kwargs:[{'email':'bad','unsubscribe_token':TOKEN}])
    with pytest.raises(ConfigurationError):subscribers.for_delivery(settings())


def test_unsubscribe_is_personal_and_does_not_mutate_original_content(configured):
    configured_settings=replace(settings(),recipients=('reader@example.com',),unsubscribe_tokens={'reader@example.com':TOKEN})
    message=build_message('Briefing','Plain story','<html><body>Story</body></html>',configured_settings,'reader@example.com')
    url='https://gaplesslabs.com/newsagent#unsubscribe='+TOKEN
    assert url in message.get_body(preferencelist=('plain',)).get_content()
    assert url in message.get_body(preferencelist=('html',)).get_content()
    assert message['To']=='reader@example.com'
    admin=build_message('Test','Plain story','<p>Story</p>',settings(),'sender@example.com')
    assert 'unsubscribe' not in admin.get_body(preferencelist=('plain',)).get_content().lower()


def test_active_check_honors_optout_and_rotated_token(configured,monkeypatch):
    monkeypatch.setattr(subscribers,'request',lambda *args,**kwargs:[])
    assert subscribers.is_active('reader@example.com',TOKEN) is False
    monkeypatch.setattr(subscribers,'request',lambda *args,**kwargs:[{'id':'00000000-0000-4000-8000-000000000001'}])
    assert subscribers.is_active('reader@example.com',TOKEN) is True


def test_import_normalizes_deduplicates_and_ignores_existing_subscribers(configured,monkeypatch):
    calls=[]
    monkeypatch.setattr(subscribers,'request',lambda *args,**kwargs:calls.append((args,kwargs)))
    assert subscribers.import_addresses(('ONE@example.com','one@example.com','two@example.com'))==2
    assert calls[0][1]['prefer']=='resolution=ignore-duplicates,return=minimal'
    assert calls[0][1]['body']==[{'email':'one@example.com','source':'manual_import'},{'email':'two@example.com','source':'manual_import'}]


def test_service_skips_a_subscriber_who_opted_out_before_delivery(configured,monkeypatch,tmp_path):
    from news_agent.mailer import service
    from news_agent.mailer.state import EmailStateStore
    from news_agent.formatting import FormattedMessage
    public_settings=replace(settings(),recipients=('reader@example.com',),unsubscribe_tokens={'reader@example.com':TOKEN})
    monkeypatch.setattr(service,'email_settings_from_env',settings)
    monkeypatch.setattr(subscribers,'for_delivery',lambda *args:public_settings)
    monkeypatch.setattr(subscribers,'is_active',lambda *args:False)
    def unexpected(*args,**kwargs):raise AssertionError('opted-out subscriber must not reach SMTP')
    monkeypatch.setattr(service,'send_email',unexpected)
    store=EmailStateStore(tmp_path/'state.db')
    mailer=service.EmailService(store)
    edition=mailer.prepare_parity_edition([FormattedMessage('Global','Story')],'Briefing')
    assert mailer.send_edition(edition)==[]


def test_service_keeps_individual_links_and_delivery_dedup(configured,monkeypatch,tmp_path):
    from news_agent.mailer import service
    from news_agent.mailer.state import EmailStateStore
    from news_agent.mailer.models import RecipientOutcome
    from news_agent.formatting import FormattedMessage
    public_settings=replace(settings(),recipients=('reader@example.com',),unsubscribe_tokens={'reader@example.com':TOKEN})
    monkeypatch.setattr(service,'email_settings_from_env',settings)
    monkeypatch.setattr(subscribers,'for_delivery',lambda *args:public_settings)
    monkeypatch.setattr(subscribers,'is_active',lambda *args:True)
    monkeypatch.setattr(subscribers,'mark_ready',lambda:None)
    sent=[]
    def send(settings,recipient,*args,**kwargs):
        sent.append((settings,recipient));return RecipientOutcome(recipient,'smtp_accepted')
    monkeypatch.setattr(service,'send_email',send)
    store=EmailStateStore(tmp_path/'state.db')
    mailer=service.EmailService(store)
    edition=mailer.prepare_parity_edition([FormattedMessage('Global','Story')],'Briefing')
    assert mailer.send_edition(edition)[0].state=='smtp_accepted'
    mailer.send_edition(edition)
    assert len(sent)==1
    assert sent[0][0].unsubscribe_tokens['reader@example.com']==TOKEN


def test_network_error_is_sanitized(configured,monkeypatch):
    from urllib.error import URLError
    def fail(*args,**kwargs):raise URLError('private secret in remote error')
    monkeypatch.setattr(subscribers,'urlopen',fail)
    with pytest.raises(ConfigurationError,match='Subscriber storage unavailable') as exc:
        subscribers.request('newsagent_subscribers')
    assert 'private secret' not in str(exc.value)


def test_empty_subscriber_list_skips_generation(configured,monkeypatch,capsys,tmp_path):
    from news_agent import cli
    from news_agent.mailer.state import EmailStateStore
    monkeypatch.setattr(cli,'EmailStateStore',lambda:EmailStateStore(tmp_path/'state.db'))
    monkeypatch.setattr(cli,'email_settings_from_env',settings)
    monkeypatch.setattr(subscribers,'request',lambda *args,**kwargs:[])
    monkeypatch.setattr(subscribers,'mark_ready',lambda:None)
    def unexpected(*args,**kwargs):raise AssertionError('empty list must not generate a briefing')
    monkeypatch.setattr(cli,'build_briefing_result_sync',unexpected)
    cli.main(['--send','--to','email'])
    assert 'No active subscribers' in capsys.readouterr().out


def test_empty_email_list_preserves_telegram_delivery(configured,monkeypatch,capsys,tmp_path):
    from types import SimpleNamespace
    from news_agent import cli
    from news_agent.mailer.state import EmailStateStore
    monkeypatch.setattr(cli,'EmailStateStore',lambda:EmailStateStore(tmp_path/'state.db'))
    monkeypatch.setattr(cli,'email_settings_from_env',settings)
    monkeypatch.setattr(subscribers,'request',lambda *args,**kwargs:[])
    monkeypatch.setattr(subscribers,'mark_ready',lambda:None)
    generated=[];sent=[]
    def build(**kwargs):
        generated.append(kwargs)
        return SimpleNamespace(briefings=[],skipped_stories=[],source_debug_lines=[])
    monkeypatch.setattr(cli,'build_briefing_result_sync',build)
    monkeypatch.setattr(cli,'send_briefing_messages',lambda messages,channel:sent.append(channel) or 1)
    cli.main(['--send','--to','both','--no-openai'])
    assert sent==['telegram']
    assert generated[0]['persist_history'] is True
