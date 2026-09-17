"""Server-only subscriber storage. No network call sends mail."""
from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import UUID

from news_agent.fetch import _ssl_context
from news_agent.mailer.models import EmailSettings
from news_agent.notifications.base import ConfigurationError
from news_agent.notifications.factory import parse_recipient_list

PAGE_SIZE = 500
EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$", re.I)


def enabled() -> bool:
    return os.getenv('NEWSAGENT_SUBSCRIBERS_ENABLED', '').lower() == 'true'


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    local = value.split('@')[0]
    if len(value) > 254 or not EMAIL.fullmatch(value) or len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        raise ConfigurationError('Invalid subscriber email address.')
    return value


def request(path: str, *, method: str = 'GET', body: object = None, prefer: str = '') -> object:
    base = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
    key = os.getenv('SUPABASE_SECRET_KEY', '').strip()
    parsed = urlparse(base)
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.supabase.co') or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not key:
        raise ConfigurationError('Configure SUPABASE_URL and SUPABASE_SECRET_KEY for subscriber storage.')
    headers = {'apikey': key, 'Content-Type': 'application/json'}
    if prefer:
        headers['Prefer'] = prefer
    req = Request(base + '/rest/v1/' + path, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
    try:
        with urlopen(req, timeout=10, context=_ssl_context()) as response:
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ConfigurationError('Subscriber response exceeded the size limit.')
            return json.loads(data) if data else None
    except (URLError, OSError, ValueError) as exc:
        # Neither upstream error bodies nor secret-bearing requests are logged.
        raise ConfigurationError('Subscriber storage unavailable; email delivery stopped.') from None


def active_subscribers() -> dict[str, str]:
    result: dict[str, str] = {}
    cursor = ''
    while True:
        params = {'select': 'id,email,unsubscribe_token', 'status': 'eq.active', 'order': 'id.asc', 'limit': str(PAGE_SIZE)}
        if cursor:
            params['id'] = 'gt.' + cursor
        rows = request('newsagent_subscribers?' + urlencode(params))
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ConfigurationError('Invalid subscriber response; email delivery stopped.')
        try:
            for row in rows:
                row_id = str(UUID(row['id']))
                email = normalize_email(row['email'])
                token = str(UUID(row['unsubscribe_token'], version=None))
                if UUID(token).version != 4 or row_id <= cursor or email in result:
                    raise ValueError('invalid subscriber')
                result[email] = token
                cursor = row_id
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ConfigurationError('Invalid subscriber record; email delivery stopped.') from None
        if len(rows) < PAGE_SIZE:
            return result


def for_delivery(settings: EmailSettings, edition_kind: str = 'production') -> EmailSettings:
    if not enabled():
        return settings
    if edition_kind != 'production':
        admins = tuple(normalize_email(address) for address in parse_recipient_list(os.getenv('EMAIL_ADMIN_TO', settings.from_address)))
        if not admins:
            raise ConfigurationError('No administrative recipient is configured.')
        return replace(settings, recipients=admins, unsubscribe_tokens={})
    members = active_subscribers()
    return replace(settings, recipients=tuple(members), unsubscribe_tokens=members)


def is_active(email: str, token: str) -> bool:
    params = {'select': 'id', 'email': 'eq.' + normalize_email(email), 'unsubscribe_token': 'eq.' + str(UUID(token)), 'status': 'eq.active', 'limit': '1'}
    rows = request('newsagent_subscribers?' + urlencode(params))
    if not isinstance(rows, list):
        raise ConfigurationError('Invalid subscriber status response.')
    return bool(rows)


def mark_ready() -> None:
    if not enabled():
        raise ConfigurationError('Enable NEWSAGENT_SUBSCRIBERS_ENABLED before marking the sender ready.')
    rows = request('newsagent_sender_status?id=eq.true', method='PATCH', body={'checked_at': datetime.now(timezone.utc).isoformat()}, prefer='return=representation')
    if not isinstance(rows, list) or len(rows) != 1:
        raise ConfigurationError('Apply the signup migration before marking the sender ready.')


def import_addresses(addresses: tuple[str, ...]) -> int:
    normalized = sorted({normalize_email(address) for address in addresses})
    for start in range(0, len(normalized), PAGE_SIZE):
        request('newsagent_subscribers?on_conflict=email', method='POST', body=[{'email': email, 'source': 'manual_import'} for email in normalized[start:start + PAGE_SIZE]], prefer='resolution=ignore-duplicates,return=minimal')
    return len(normalized)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Manage NewsAgent subscribers without sending any mail.')
    parser.add_argument('--import-existing', action='store_true', help='Import EMAIL_TO without reactivating existing opt-outs.')
    parser.add_argument('--mark-ready', action='store_true', help='Open signup after checking the enabled sender connection.')
    args = parser.parse_args()
    try:
        if args.import_existing:
            addresses = tuple(parse_recipient_list(os.getenv('EMAIL_TO', '')))
            if not addresses:
                raise ConfigurationError('EMAIL_TO is empty; nothing imported.')
            print(f'Processed {import_addresses(addresses)} unique import addresses; existing records unchanged.')
        members = active_subscribers()
        print(f'Subscriber connection verified: {len(members)} active subscriptions. No email sent.')
        if args.mark_ready:
            mark_ready()
            print('Sender readiness recorded; signup can open for 48 hours, refreshed by normal sends.')
    except ConfigurationError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
