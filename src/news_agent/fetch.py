from __future__ import annotations

import asyncio
import html
import re
import os
import ssl
import urllib.error
import urllib.request
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from news_agent.models import Article, FeedConfig


USER_AGENT = "morning-news-agent/0.1 (+https://example.local)"
TAG_RE = re.compile(r"<[^>]+>")
ATOM_NS = "{http://www.w3.org/2005/Atom}"
SYSTEM_CA_BUNDLE = "/etc/ssl/cert.pem"
MAX_TITLE_LENGTH = 320
MAX_FULLWIDTH_CHARACTERS = 8
MALFORMED_TITLE_MARKUP_RE = re.compile(r"!+\[|\]!+|@!+")
STREAMING_SPAM_RE = re.compile(
    r"\b(?:live\s+stream(?:ing)?\s+free|free\s+(?:live\s+)?stream(?:ing)?|"
    r"watch\b.*\b(?:live\s+)?stream(?:ing)?\b.*\bfree\b)",
    re.IGNORECASE,
)


def _ssl_context() -> ssl.SSLContext:
    configured_bundle = os.getenv("BRIEFING_CA_BUNDLE")
    if configured_bundle:
        return ssl.create_default_context(cafile=configured_bundle)
    if os.path.exists(SYSTEM_CA_BUNDLE):
        return ssl.create_default_context(cafile=SYSTEM_CA_BUNDLE)
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return _clean_text(child.text)
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _content_candidates(node: ET.Element) -> list[str]:
    candidates: list[str] = []
    for child in node.iter():
        if _local_name(child.tag) not in {"description", "summary", "content", "encoded"}:
            continue
        value = _clean_text(" ".join(child.itertext()))
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _select_feed_content(title: str, candidates: list[str]) -> str:
    substantive = [value for value in candidates if value.casefold() != title.casefold()]
    return max(substantive, key=len, default="")


def _child_link(node: ET.Element) -> str:
    link = _child_text(node, ("link", f"{ATOM_NS}link"))
    if link:
        return link
    atom_link = node.find(f"{ATOM_NS}link")
    if atom_link is not None:
        return atom_link.attrib.get("href", "")
    return ""


def _article_source(node: ET.Element, feed: FeedConfig) -> str:
    source = _child_text(node, ("source", f"{ATOM_NS}source"))
    if source and feed.name.startswith("Google News"):
        return source
    return feed.name


def _clean_title(title: str, source: str) -> str:
    title = unicodedata.normalize("NFKC", title)
    suffix = f" - {source}"
    if source and title.endswith(suffix):
        return title[: -len(suffix)].strip()
    return title


def _is_acceptable_title(raw_title: str) -> bool:
    normalized = unicodedata.normalize("NFKC", raw_title)
    fullwidth_count = sum("\uff01" <= char <= "\uff5e" for char in raw_title)
    if len(normalized) > MAX_TITLE_LENGTH or fullwidth_count >= MAX_FULLWIDTH_CHARACTERS:
        return False
    if MALFORMED_TITLE_MARKUP_RE.search(raw_title):
        return False
    return not STREAMING_SPAM_RE.search(normalized)


def parse_feed(xml_text: str, feed: FeedConfig) -> list[Article]:
    root = ET.fromstring(xml_text)
    nodes = root.findall(".//item")
    if not nodes:
        nodes = root.findall(f".//{ATOM_NS}entry")

    articles: list[Article] = []
    for node in nodes:
        raw_title = _child_text(node, ("title", f"{ATOM_NS}title"))
        url = _child_link(node)
        source = _article_source(node, feed)
        if not _is_acceptable_title(raw_title):
            continue
        title = _clean_title(raw_title, source)
        content_candidates = _content_candidates(node)
        summary = _child_text(node, ("description", "summary", f"{ATOM_NS}summary", f"{ATOM_NS}content"))
        feed_content = _select_feed_content(title, content_candidates)
        published_raw = _child_text(
            node,
            ("pubDate", "published", "updated", f"{ATOM_NS}published", f"{ATOM_NS}updated"),
        )
        if title and url:
            articles.append(
                Article(
                    title=title,
                    url=url,
                    source=source,
                    published_at=_parse_datetime(published_raw),
                    summary=summary,
                    feed_content=feed_content,
                    enrichment_status="feed_content" if feed_content else "not_attempted",
                    reputation=feed.reputation,
                    feed_categories=feed.categories,
                    feed_source_type=feed.source_type,
                )
            )
    return articles


def fetch_feed(feed: FeedConfig, timeout_seconds: float = 12.0) -> list[Article]:
    request = urllib.request.Request(feed.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=_ssl_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        return []
    try:
        return parse_feed(body, feed)
    except ET.ParseError:
        return []


async def fetch_all_feeds(feeds: tuple[FeedConfig, ...], lookback_hours: int, max_articles: int) -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    tasks = [asyncio.to_thread(fetch_feed, feed) for feed in feeds]
    results = await asyncio.gather(*tasks)
    articles = [article for batch in results for article in batch if article.published_at >= cutoff]
    articles.sort(key=lambda item: item.published_at, reverse=True)
    return articles[:max_articles]
