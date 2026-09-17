"""Publish a sent briefing for the website's live plate. Never sends mail.

The payload is built from the same BriefingSection objects the email was
formatted from, so the plate shows what subscribers received without parsing
HTML or plain text back apart. Two exclusions are structural rather than
filtered: watchlist content is assembled on a different path and is never
passed in, and a section's ``lead_lines`` (finance's live quote preamble) are
left out because they are end-of-day figures that read as stale on a page
somebody opens at three in the afternoon.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from news_agent.fetch import _ssl_context
from news_agent.mailer.render import CATEGORY_LABELS, _extract_headline
from news_agent.models import BriefingSection

MAX_SOURCES = 6
MAX_CATEGORIES = 8
# The editions table refuses anything larger; refuse it here with a readable
# reason instead of letting the database reject the row.
MAX_PAYLOAD_BYTES = 200_000


class PublishError(RuntimeError):
    """The edition could not be published. Never carries upstream detail."""


def enabled() -> bool:
    return os.getenv('NEWSAGENT_PUBLISH_DIGEST', '').lower() == 'true'


def _sources(paragraph) -> list[dict[str, str]]:
    names = [name.strip() for name in paragraph.sources if name and name.strip()][:MAX_SOURCES]
    urls = [url.strip() for url in paragraph.urls][:MAX_SOURCES]
    paired = urls if len(urls) == len(names) else []
    out: list[dict[str, str]] = []
    for index, name in enumerate(names):
        url = paired[index] if paired else ''
        parsed = urlparse(url)
        # A name with no usable link still names the outlet; a bad link never travels.
        out.append({'name': name, 'url': url if parsed.scheme in {'http', 'https'} and parsed.netloc else ''})
    return out


def build_payload(sections, *, briefing_date: str, subject: str) -> dict:
    categories = []
    for section in sections:
        if not isinstance(section, BriefingSection):
            raise PublishError('Only briefing sections can be published.')
        stories = []
        for paragraph in section.paragraphs:
            text = ' '.join(paragraph.paragraph.split())
            if not text:
                continue
            headline, body = _extract_headline(text)
            stories.append({'headline': headline, 'body': body, 'sources': _sources(paragraph)})
        if stories:
            categories.append({
                'key': section.category,
                'label': CATEGORY_LABELS.get(section.category, section.label),
                'stories': stories,
            })
    if not categories:
        raise PublishError('The briefing had no stories to publish.')
    if len(categories) > MAX_CATEGORIES:
        raise PublishError('The briefing had more categories than the store accepts.')
    return {'date': briefing_date, 'subject': subject, 'categories': categories}


def _publish(payload: dict) -> None:
    base = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
    key = os.getenv('SUPABASE_SECRET_KEY', '').strip()
    parsed = urlparse(base)
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.supabase.co') or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not key:
        raise PublishError('Configure SUPABASE_URL and SUPABASE_SECRET_KEY to publish the digest.')
    body = json.dumps({
        'p_date': payload['date'],
        'p_subject': payload['subject'],
        'p_payload': {'categories': payload['categories']},
    }).encode()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise PublishError('The briefing payload exceeded the publish size limit.')
    request = Request(
        base + '/rest/v1/rpc/newsagent_publish_edition',
        data=body,
        headers={'apikey': key, 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=10, context=_ssl_context()) as response:
            result = json.loads(response.read(10_000) or b'null')
    except (URLError, OSError, ValueError):
        # Upstream bodies can carry credentials or connection detail. Never log them.
        raise PublishError('The edition store is unavailable.') from None
    if result != 'published':
        raise PublishError('The edition store refused the payload.')


def publish_quietly(sections, *, briefing_date: str, subject: str, edition_kind: str, accepted: int) -> bool:
    """Publish after a successful production send, and never break the send.

    The briefing is the product; the website is downstream of it. A failure
    here prints a warning and leaves the last published edition in place,
    dated, rather than turning a delivered briefing into a failed run.
    """
    if not enabled():
        return False
    try:
        # Test revisions and gate alerts are not the briefing anyone received,
        # and an edition nobody accepted is not one to show the public.
        if edition_kind != 'production':
            raise PublishError('Only production editions are published.')
        if accepted < 1:
            raise PublishError('No recipient accepted this edition.')
        _publish(build_payload(sections, briefing_date=briefing_date, subject=subject))
    except PublishError as exc:
        print(f'Warning: the briefing was sent but not published to the website ({exc}).', file=sys.stderr)
        return False
    except Exception as exc:  # A defect in publishing must not fail a delivered briefing.
        print(f'Warning: the briefing was sent but not published to the website ({type(exc).__name__}).', file=sys.stderr)
        return False
    print('Published the briefing to gaplesslabs.com/newsagent.')
    return True
