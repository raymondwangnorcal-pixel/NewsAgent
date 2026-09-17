"""Digest publishing. No test sends email or touches the network."""
from __future__ import annotations

import json

import pytest

from news_agent.mailer import digest_publish
from news_agent.models import BriefingParagraph, BriefingSection


def paragraph(text: str, sources=('Reuters',), urls=('https://example.com/a',), category='business_tech'):
    return BriefingParagraph(story_id='s1', category=category, paragraph=text, sources=tuple(sources), urls=tuple(urls))


def section(category='business_tech', label='Business + Tech', paragraphs=None, lead_lines=()):
    return BriefingSection(
        category=category,
        label=label,
        paragraphs=tuple(paragraphs if paragraphs is not None else [paragraph('A headline sentence. And the body that follows it.')]),
        lead_lines=tuple(lead_lines),
    )


def test_payload_splits_each_story_the_way_the_email_does():
    payload = digest_publish.build_payload([section()], briefing_date='2026-09-17', subject='Morning Briefing — 2026-09-17')
    story = payload['categories'][0]['stories'][0]
    assert story['headline'] == 'A headline sentence.'
    assert story['body'] == 'And the body that follows it.'
    assert payload['categories'][0]['label'] == 'Business + Tech'
    assert payload['date'] == '2026-09-17'


def test_finance_lead_lines_and_empty_paragraphs_never_travel():
    payload = digest_publish.build_payload(
        [section(category='finance', label='Finance', lead_lines=('S&P 500 6,012.45 (+0.4%)',),
                 paragraphs=[paragraph('Copper closed higher. Traders described positioning as crowded.', category='finance'), paragraph('   ', category='finance')])],
        briefing_date='2026-09-17', subject='s')
    serialized = json.dumps(payload)
    assert '6,012.45' not in serialized
    assert len(payload['categories'][0]['stories']) == 1


def test_only_http_links_travel_and_a_source_without_one_keeps_its_name():
    payload = digest_publish.build_payload(
        [section(paragraphs=[paragraph('A headline. Body.', sources=('Reuters', 'CNBC', 'WSJ'),
                                       urls=('https://example.com/a', 'javascript:alert(1)', 'not a url'))])],
        briefing_date='2026-09-17', subject='s')
    assert payload['categories'][0]['stories'][0]['sources'] == [
        {'name': 'Reuters', 'url': 'https://example.com/a'},
        {'name': 'CNBC', 'url': ''},
        {'name': 'WSJ', 'url': ''},
    ]


def test_mismatched_source_and_url_counts_publish_names_without_links():
    payload = digest_publish.build_payload(
        [section(paragraphs=[paragraph('A headline. Body.', sources=('Reuters', 'CNBC'), urls=('https://example.com/a',))])],
        briefing_date='2026-09-17', subject='s')
    assert [s['url'] for s in payload['categories'][0]['stories'][0]['sources']] == ['', '']


def test_a_briefing_with_nothing_in_it_is_refused():
    with pytest.raises(digest_publish.PublishError):
        digest_publish.build_payload([section(paragraphs=[])], briefing_date='2026-09-17', subject='s')
    with pytest.raises(digest_publish.PublishError):
        digest_publish.build_payload(['not a section'], briefing_date='2026-09-17', subject='s')


def test_publishing_is_off_unless_the_variable_is_set(monkeypatch, capsys):
    monkeypatch.delenv('NEWSAGENT_PUBLISH_DIGEST', raising=False)
    calls = []
    monkeypatch.setattr(digest_publish, '_publish', lambda payload: calls.append(payload))
    assert digest_publish.publish_quietly([section()], briefing_date='2026-09-17', subject='s', edition_kind='production', accepted=1) is False
    assert calls == []


def test_test_revisions_gate_alerts_and_unaccepted_editions_are_never_published(monkeypatch, capsys):
    monkeypatch.setenv('NEWSAGENT_PUBLISH_DIGEST', 'true')
    calls = []
    monkeypatch.setattr(digest_publish, '_publish', lambda payload: calls.append(payload))
    for kind, accepted in [('test', 1), ('gate_alert', 1), ('production', 0)]:
        assert digest_publish.publish_quietly([section()], briefing_date='2026-09-17', subject='s', edition_kind=kind, accepted=accepted) is False
    assert calls == []
    assert 'not published' in capsys.readouterr().err


def test_a_publish_failure_warns_and_never_raises(monkeypatch, capsys):
    monkeypatch.setenv('NEWSAGENT_PUBLISH_DIGEST', 'true')

    def explode(payload):
        raise digest_publish.PublishError('The edition store is unavailable.')

    monkeypatch.setattr(digest_publish, '_publish', explode)
    assert digest_publish.publish_quietly([section()], briefing_date='2026-09-17', subject='s', edition_kind='production', accepted=2) is False
    assert 'not published' in capsys.readouterr().err

    def worse(payload):
        raise RuntimeError('an unexpected defect')

    monkeypatch.setattr(digest_publish, '_publish', worse)
    assert digest_publish.publish_quietly([section()], briefing_date='2026-09-17', subject='s', edition_kind='production', accepted=2) is False
    assert 'an unexpected defect' not in capsys.readouterr().err


def test_a_successful_production_send_publishes_once(monkeypatch, capsys):
    monkeypatch.setenv('NEWSAGENT_PUBLISH_DIGEST', 'true')
    calls = []
    monkeypatch.setattr(digest_publish, '_publish', lambda payload: calls.append(payload))
    assert digest_publish.publish_quietly([section()], briefing_date='2026-09-17', subject='Morning Briefing — 2026-09-17', edition_kind='production', accepted=3) is True
    assert len(calls) == 1 and calls[0]['categories'][0]['key'] == 'business_tech'
    assert 'Published the briefing' in capsys.readouterr().out


def test_missing_or_hostile_supabase_configuration_refuses_to_send_anything(monkeypatch):
    sent = []
    monkeypatch.setattr(digest_publish, 'urlopen', lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError('no request expected')))
    payload = digest_publish.build_payload([section()], briefing_date='2026-09-17', subject='s')
    for url, key in [('', 'k'), ('http://project.supabase.co', 'k'), ('https://evil.example', 'k'),
                     ('https://project.supabase.co/path', 'k'), ('https://project.supabase.co', '')]:
        monkeypatch.setenv('SUPABASE_URL', url)
        monkeypatch.setenv('SUPABASE_SECRET_KEY', key)
        with pytest.raises(digest_publish.PublishError):
            digest_publish._publish(payload)
    assert sent == []


def test_an_oversized_briefing_is_refused_before_the_database_sees_it(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://project.supabase.co')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'k')
    monkeypatch.setattr(digest_publish, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no request expected')))
    huge = [section(paragraphs=[paragraph('A headline. ' + 'body ' * 60000)])]  # ~300KB; a real briefing is ~15KB
    with pytest.raises(digest_publish.PublishError):
        digest_publish._publish(digest_publish.build_payload(huge, briefing_date='2026-09-17', subject='s'))
