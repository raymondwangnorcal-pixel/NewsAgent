from __future__ import annotations

import html
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Callable

from news_agent.evidence import rank_articles_by_evidence
from news_agent.fetch import USER_AGENT, _ssl_context
from news_agent.models import Article, EnrichmentConfig, EnrichmentStatus, ExtractionPolicyConfig, StoryCluster
from news_agent.classify import CATEGORY_NAMES


logger = logging.getLogger(__name__)
AGGREGATOR_DOMAINS = {"news.google.com"}


@dataclass(frozen=True)
class EnrichmentOutcome:
    requested_url: str
    canonical_url: str
    status: EnrichmentStatus
    extracted_text: str = ""
    error_code: str = ""


@dataclass(frozen=True)
class EnrichmentStats:
    pages_attempted: int = 0
    pages_extracted: int = 0
    pages_blocked: int = 0
    pages_failed: int = 0


@dataclass(frozen=True)
class FetchedPage:
    final_url: str
    content_type: str
    body: bytes


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonical_url = ""
        self._json_ld = False
        self._json_parts: list[str] = []
        self.json_documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "link" and "canonical" in values.get("rel", "").casefold():
            self.canonical_url = values.get("href", "")
        elif tag.casefold() == "meta" and values.get("property", "").casefold() == "og:url":
            self.canonical_url = self.canonical_url or values.get("content", "")
        elif tag.casefold() == "script" and "ld+json" in values.get("type", "").casefold():
            self._json_ld = True
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._json_ld:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._json_ld:
            self.json_documents.append("".join(self._json_parts))
            self._json_ld = False


def _hostname(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").casefold().rstrip(".")


def _domain_matches(hostname: str, allowed_domain: str) -> bool:
    domain = allowed_domain.casefold().lstrip(".")
    return hostname == domain or hostname.endswith(f".{domain}")


def policy_for_url(url: str, config: EnrichmentConfig) -> ExtractionPolicyConfig | None:
    hostname = _hostname(url)
    return next(
        (policy for policy in config.policies if any(_domain_matches(hostname, domain) for domain in policy.allowed_domains)),
        None,
    )


def _is_public_http_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            return False
    return True


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        if not _is_public_http_url(resolved):
            raise urllib.error.URLError("unsafe_redirect")
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def fetch_page(url: str, config: EnrichmentConfig) -> FetchedPage:
    if not _is_public_http_url(url):
        raise urllib.error.URLError("unsafe_url")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()),
        _SafeRedirectHandler(),
    )
    with opener.open(request, timeout=config.request_timeout_seconds) as response:
        content_type = response.headers.get_content_type()
        body = response.read(config.max_response_bytes + 1)
        if len(body) > config.max_response_bytes:
            raise ValueError("response_too_large")
        return FetchedPage(final_url=response.url, content_type=content_type, body=body)


def _walk_json(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        results = [value]
        for child in value.values():
            results.extend(_walk_json(child))
        return results
    if isinstance(value, list):
        results: list[dict[str, object]] = []
        for child in value:
            results.extend(_walk_json(child))
        return results
    return []


def _json_ld_metadata(parser: _MetadataParser) -> tuple[str, str]:
    canonical_url = ""
    article_body = ""
    for document in parser.json_documents:
        try:
            data = json.loads(document)
        except json.JSONDecodeError:
            continue
        for entry in _walk_json(data):
            entry_type = entry.get("@type")
            types = entry_type if isinstance(entry_type, list) else [entry_type]
            if not any(value in {"Article", "NewsArticle", "ReportageNewsArticle", "BlogPosting"} for value in types):
                continue
            body = entry.get("articleBody")
            if isinstance(body, str) and len(body) > len(article_body):
                article_body = body
            url = entry.get("url") or entry.get("mainEntityOfPage")
            if isinstance(url, dict):
                url = url.get("@id")
            if isinstance(url, str):
                canonical_url = canonical_url or url
    return canonical_url, article_body


def _extract_text(html_text: str, url: str) -> str:
    try:
        from trafilatura import bare_extraction
    except ImportError:
        return ""
    result = bare_extraction(
        html_text,
        url=url,
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
        deduplicate=True,
        as_dict=True,
    )
    if not isinstance(result, dict):
        return ""
    return " ".join(str(result.get("text") or "").split())


def enrich_article(
    article: Article,
    config: EnrichmentConfig,
    page_fetcher: Callable[[str, EnrichmentConfig], FetchedPage] = fetch_page,
) -> Article:
    initial_policy = policy_for_url(article.url, config)
    is_aggregator = _hostname(article.url) in AGGREGATOR_DOMAINS
    if initial_policy is None and not is_aggregator:
        return replace(article, enrichment_status="not_permitted", enrichment_error_code="domain_not_permitted")
    try:
        page = page_fetcher(article.url, config)
    except urllib.error.HTTPError as exc:
        status: EnrichmentStatus = "blocked" if exc.code in {401, 403, 429, 451} else "failed"
        return replace(article, enrichment_status=status, enrichment_error_code=f"http_{exc.code}")
    except (TimeoutError, socket.timeout):
        return replace(article, enrichment_status="failed", enrichment_error_code="timeout")
    except ValueError as exc:
        return replace(article, enrichment_status="failed", enrichment_error_code=str(exc))
    except urllib.error.URLError as exc:
        code = "unsafe_redirect" if "unsafe" in str(exc.reason) else "network_error"
        return replace(article, enrichment_status="failed", enrichment_error_code=code)

    final_policy = policy_for_url(page.final_url, config)
    if final_policy is None or final_policy.policy == "disabled":
        return replace(
            article,
            canonical_url=page.final_url,
            enrichment_status="not_permitted",
            enrichment_error_code="redirect_not_permitted" if is_aggregator else "domain_not_permitted",
        )
    if page.content_type not in {"text/html", "application/xhtml+xml"}:
        return replace(article, canonical_url=page.final_url, enrichment_status="failed", enrichment_error_code="unsupported_content_type")

    html_text = page.body.decode("utf-8", errors="replace")
    parser = _MetadataParser()
    parser.feed(html_text)
    json_canonical, json_body = _json_ld_metadata(parser)
    canonical_url = urllib.parse.urljoin(page.final_url, parser.canonical_url or json_canonical or page.final_url)
    if policy_for_url(canonical_url, config) is None:
        canonical_url = page.final_url
    if final_policy.policy == "metadata_only":
        return replace(article, canonical_url=canonical_url, enrichment_status="metadata_only")

    extracted = _extract_text(html_text, canonical_url) or " ".join(html.unescape(json_body).split())
    extracted = extracted[: config.max_extracted_chars].strip()
    if len(extracted) < config.minimum_extracted_chars:
        return replace(article, canonical_url=canonical_url, enrichment_status="too_thin", enrichment_error_code="extractor_returned_thin")
    return replace(article, canonical_url=canonical_url, extracted_text=extracted, enrichment_status="extracted")


def enrich_clusters(
    clusters: list[StoryCluster],
    config: EnrichmentConfig,
    page_fetcher: Callable[[str, EnrichmentConfig], FetchedPage] = fetch_page,
) -> tuple[list[StoryCluster], EnrichmentStats]:
    if not config.enabled:
        return clusters, EnrichmentStats()
    attempts = extracted = blocked = failed = 0
    selected_clusters = select_enrichment_clusters(clusters, config)
    selected = schedule_enrichment_articles(selected_clusters, config)

    enriched_by_url: dict[str, Article] = {}
    attempts = len(selected)
    with ThreadPoolExecutor(max_workers=min(8, max(1, attempts))) as executor:
        futures = {executor.submit(enrich_article, article, config, page_fetcher): article.url for article in selected}
        for future in as_completed(futures):
            enriched = future.result()
            enriched_by_url[futures[future]] = enriched
            extracted += int(enriched.enrichment_status == "extracted")
            blocked += int(enriched.enrichment_status == "blocked")
            failed += int(enriched.enrichment_status in {"failed", "too_thin"})

    for cluster in clusters:
        cluster.articles = [enriched_by_url.get(article.url, article) for article in cluster.articles]
    return clusters, EnrichmentStats(attempts, extracted, blocked, failed)


def _cluster_feed_hints(cluster: StoryCluster) -> tuple[str, ...]:
    values = {category for article in cluster.articles for category in article.feed_categories}
    return tuple(category for category in CATEGORY_NAMES if category in values)


def select_enrichment_clusters(clusters: list[StoryCluster], config: EnrichmentConfig) -> list[StoryCluster]:
    ranked = sorted(clusters, key=lambda item: item.total_score, reverse=True)
    selected = ranked[: config.global_cluster_slots]
    selected_ids = {id(cluster) for cluster in selected}
    coverage = Counter(category for cluster in selected for category in _cluster_feed_hints(cluster))
    queues = {
        category: [cluster for cluster in ranked if id(cluster) not in selected_ids and category in _cluster_feed_hints(cluster)]
        for category in CATEGORY_NAMES
    }
    positions = {category: 0 for category in CATEGORY_NAMES}
    while len(selected) < config.max_clusters_per_run:
        progressed = False
        for category in CATEGORY_NAMES:
            if coverage[category] >= config.reserved_clusters_per_category:
                continue
            queue = queues[category]
            while positions[category] < len(queue) and id(queue[positions[category]]) in selected_ids:
                positions[category] += 1
            if positions[category] >= len(queue):
                continue
            cluster = queue[positions[category]]
            positions[category] += 1
            selected.append(cluster)
            selected_ids.add(id(cluster))
            coverage.update(_cluster_feed_hints(cluster))
            progressed = True
            if len(selected) >= config.max_clusters_per_run:
                break
        if not progressed:
            break
    return selected


def schedule_enrichment_articles(clusters: list[StoryCluster], config: EnrichmentConfig) -> list[Article]:
    selected: list[Article] = []
    selected_urls: set[str] = set()
    ranked_by_cluster = [rank_articles_by_evidence(cluster.articles) for cluster in clusters]
    for article_index in range(config.max_articles_per_cluster):
        for ranked in ranked_by_cluster:
            permitted = [
                article for article in ranked
                if _hostname(article.url) not in AGGREGATOR_DOMAINS
                and (policy := policy_for_url(article.url, config)) is not None
                and policy.policy != "disabled"
            ]
            if article_index >= len(permitted):
                continue
            article = permitted[article_index]
            if article.url not in selected_urls:
                selected.append(article)
                selected_urls.add(article.url)
            if len(selected) >= config.max_pages_per_run:
                return selected
    return selected
