from __future__ import annotations

from datetime import datetime, timezone

from news_agent.evidence import evidence_substance_score, rank_articles_by_evidence
from news_agent.models import Article


def article(title: str, summary: str, **kwargs: object) -> Article:
    return Article(title=title, summary=summary, url="https://example.com/a", source="Example", published_at=datetime.now(timezone.utc), **kwargs)


def test_rich_specific_evidence_outscores_headline_duplicate() -> None:
    thin = article("Fed cuts rates", "Fed cuts rates Example")
    rich = article(
        "Fed cuts rates",
        "The central bank lowered its benchmark rate by 0.25 percentage points on Wednesday. Officials voted 9-2 after inflation slowed to 2.4%, affecting borrowing costs nationwide.",
    )

    assert evidence_substance_score(rich) > evidence_substance_score(thin)


def test_rank_articles_prefers_extracted_context() -> None:
    feed = article("Company expands", "Company expands into Europe.")
    extracted = article(
        "Company expands",
        "Short feed item.",
        extracted_text="The company committed $2 billion to three European plants. The project is expected to employ 4,000 people when production begins in 2028.",
        enrichment_status="extracted",
    )

    assert rank_articles_by_evidence([feed, extracted])[0].enrichment_status == "extracted"
