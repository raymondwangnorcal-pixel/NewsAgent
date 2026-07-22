from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from news_agent.enrichment import enrich_article, policy_for_url
from news_agent.evidence import rank_articles_by_evidence
from news_agent.fetch import fetch_feed
from news_agent.models import EnrichmentConfig, ExtractionPolicyConfig, FeedConfig


LOOKBACK_HOURS = 30
MINIMUM_EVIDENCE_SCORE = 1.2


@dataclass(frozen=True)
class Candidate:
    name: str
    feed_url: str
    domain: str
    lane: str
    reputation: float


@dataclass(frozen=True)
class ValidationResult:
    name: str
    passed: bool
    total_recent: int
    nonempty_rate: float
    nonduplicate_rate: float
    evidence_gate_rate: float
    timestamps_valid: bool
    extraction_policy_matches: bool
    rich_or_extracted_sample: bool
    sample_status: str


CANDIDATES = (
    Candidate("The Hollywood Reporter", "https://www.hollywoodreporter.com/feed/", "hollywoodreporter.com", "film_tv", 0.80),
    Candidate("Deadline", "https://deadline.com/feed/", "deadline.com", "film_tv", 0.80),
    Candidate("Billboard", "https://www.billboard.com/feed/", "billboard.com", "music", 0.80),
    Candidate("Polygon", "https://www.polygon.com/rss/index.xml", "polygon.com", "gaming", 0.75),
)


def validate(candidate: Candidate) -> ValidationResult:
    feed = FeedConfig(
        name=candidate.name,
        url=candidate.feed_url,
        reputation=candidate.reputation,
        categories=("culture",),
        source_type="culture",
        culture_lane=candidate.lane,  # type: ignore[arg-type]
    )
    policy = ExtractionPolicyConfig(
        id=candidate.name.casefold().replace(" ", "-"),
        allowed_domains=(candidate.domain,),
        policy="article_text",
    )
    enrichment = EnrichmentConfig(policies=(policy,))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    fetched = fetch_feed(feed)
    recent = [article for article in fetched if cutoff <= article.published_at <= now + timedelta(minutes=5)]
    total = len(recent)
    nonempty = sum(bool(article.best_available_text.strip()) for article in recent)
    nonduplicate = sum(
        bool(article.best_available_text.strip())
        and article.best_available_text.strip().casefold() != article.title.strip().casefold()
        for article in recent
    )
    scored = rank_articles_by_evidence(recent)
    evidence_passes = sum(article.evidence_score >= MINIMUM_EVIDENCE_SCORE for article in scored)
    policy_matches = bool(scored) and all(policy_for_url(article.url, enrichment) is not None for article in scored)
    sample_status = "no_recent_entries"
    rich_or_extracted = False
    if scored and policy_matches:
        sample = enrich_article(scored[0], enrichment)
        sample_status = sample.enrichment_status
        rich_or_extracted = sample.enrichment_status == "extracted" or len(sample.best_available_text) >= 300
    denominator = max(total, 1)
    result = ValidationResult(
        name=candidate.name,
        passed=(
            total >= 5
            and nonempty / denominator >= 0.60
            and nonduplicate / denominator >= 0.60
            and evidence_passes / denominator >= 0.60
            and all(article.feed_timestamp_valid for article in recent)
            and policy_matches
            and rich_or_extracted
        ),
        total_recent=total,
        nonempty_rate=round(nonempty / denominator, 3),
        nonduplicate_rate=round(nonduplicate / denominator, 3),
        evidence_gate_rate=round(evidence_passes / denominator, 3),
        timestamps_valid=bool(recent) and all(article.feed_timestamp_valid for article in recent),
        extraction_policy_matches=policy_matches,
        rich_or_extracted_sample=rich_or_extracted,
        sample_status=sample_status,
    )
    return result


def main() -> None:
    results = [validate(candidate) for candidate in CANDIDATES]
    print(json.dumps([asdict(result) for result in results], indent=2))


if __name__ == "__main__":
    main()
