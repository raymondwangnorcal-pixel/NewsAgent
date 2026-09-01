from __future__ import annotations

import sys
import hashlib
import uuid
from dataclasses import replace
from datetime import datetime
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from news_agent.formatting import FormattedMessage
from news_agent.mailer.models import EmailEdition, RecipientOutcome
from news_agent.mailer.render import RenderedEmail, render_parity_email
from news_agent.mailer.render import render_minimal_newsletter, render_watchlist_section
from news_agent.mailer.settings import email_settings_from_env
from news_agent.mailer.smtp import SMTPFactory, send_email
from news_agent.mailer.state import EmailStateStore
from news_agent.history import HistoryUpdate, apply_history_update
from news_agent.newsletter_review import CandidateRecord, bind_run_id
from news_agent.mailer.quotes import (
    EndOfDayQuote,
    expected_quote_close_date,
    fetch_quotes_with_shared_deadline,
    is_regular_nyse_market_hours,
    validate_quote_provider_configuration,
)
from news_agent.mailer.watchlist import load_email_watchlist, validate_shared_watchlist_consistency
from news_agent.mailer.watchlist_news import (
    WatchlistStory,
    deserialize_articles,
    discover_watchlist_keys_with_shared_deadline,
    serialize_articles,
    summarize_watchlist,
)
from news_agent.models import Article, EnrichmentConfig, StockQuote
from news_agent.openai_budget import OpenAIBudget
from news_agent.stocks import fetch_yahoo_quotes
from news_agent.time import briefing_now
from news_agent.watchlist.edgar import EdgarClient, sec_contact_email_from_env
from news_agent.watchlist.entity_map import classify_text, load_entity_map, load_relationship_ambiguities
from news_agent.watchlist.filings import discover_material_filings
from news_agent.watchlist.discovery import EXCLUDED_HOSTS, distinct_discovery_keys
from news_agent.watchlist.models import RelationshipLabel, SourceState


class EmailService:
    """Owns email state and SMTP delivery without changing notification adapters."""

    def __init__(self, store: EmailStateStore | None = None) -> None:
        self.store = store or EmailStateStore()

    def render_parity(self, messages: list[FormattedMessage], header: str) -> RenderedEmail:
        return render_parity_email(messages, header)

    def prepare_parity_edition(self, messages: list[FormattedMessage], header: str, *, test_revision: bool = False) -> EmailEdition:
        rendered = self.render_parity(messages, header)
        today = briefing_now().date().isoformat()
        story_ids = [(message.title, "general") for message in messages]
        if test_revision:
            return self.store.prepare_test_revision(today, rendered.subject, rendered.plain_text, rendered.html, story_ids)
        return self.store.prepare_edition(today, rendered.subject, rendered.plain_text, rendered.html, story_ids)

    def prepare_newsletter_edition(
        self,
        messages: list[FormattedMessage],
        header: str,
        enrichment_config: EnrichmentConfig,
        budget: OpenAIBudget,
        *,
        test_revision: bool = False,
        general_articles: tuple[Article, ...] = (),
        market_quotes: Mapping[str, StockQuote] | None = None,
        use_openai: bool = True,
        read_edgar_state: bool = False,
        briefing_date: str | None = None,
        candidate_records: tuple[CandidateRecord, ...] = (),
        history_update: HistoryUpdate | None = None,
        pipeline_version: str = "",
        config_hash: str = "",
        deck_target: int = 0,
        openai_mode: str = "",
        history_path: Path | None = None,
    ) -> EmailEdition:
        rendered, stories = self.render_newsletter(
            messages,
            header,
            enrichment_config,
            budget,
            persist_quotes=not test_revision,
            allow_cached_quotes=not test_revision,
            bypass_sent_suppression=test_revision,
            general_articles=general_articles,
            market_quotes=market_quotes,
            persist_watchlist_state=not test_revision,
            use_openai=use_openai,
            read_edgar_state=read_edgar_state,
        )
        today = briefing_date or briefing_now().date().isoformat()
        story_ids = [(message.title, "general") for message in messages]
        story_ids.extend(
            (event_id, f"watchlist:{story.ticker}")
            for story in stories
            for event_id in story.event_ids
        )
        if test_revision:
            return self.store.prepare_test_revision(today, rendered.subject, rendered.plain_text, rendered.html, story_ids)
        gate_state = self.store.record_watchlist_run(
            uuid.uuid4().hex,
            today,
            [_watchlist_run_record(story) for story in stories],
        )
        if gate_state == "FAIL":
            return self.store.prepare_gate_failure_alert(today)
        run_id = uuid.uuid4().hex
        edition = self.store.prepare_newsletter_run(
            run_id=run_id,
            briefing_date=today,
            pipeline_version=pipeline_version or "unknown",
            config_hash=config_hash or "unknown",
            deck_target=deck_target,
            openai_mode=openai_mode or "unknown",
            history_update_json=history_update.to_json() if history_update else None,
            subject=rendered.subject,
            plain_text=rendered.plain_text,
            html=rendered.html,
            story_ids=story_ids,
            candidates=[record.as_db_dict() for record in bind_run_id(candidate_records, run_id)],
        )
        if history_update is not None:
            apply_history_update(history_update, history_path) if history_path else apply_history_update(history_update)
            self.store.mark_newsletter_history_applied(run_id)
        return edition

    def render_newsletter(
        self,
        messages: list[FormattedMessage],
        header: str,
        enrichment_config: EnrichmentConfig,
        budget: OpenAIBudget,
        *,
        persist_quotes: bool = True,
        allow_cached_quotes: bool = True,
        bypass_sent_suppression: bool = False,
        general_articles: tuple[Article, ...] = (),
        market_quotes: Mapping[str, StockQuote] | None = None,
        persist_watchlist_state: bool = True,
        use_openai: bool = True,
        read_edgar_state: bool = False,
    ) -> tuple[RenderedEmail, list[WatchlistStory]]:
        validate_quote_provider_configuration()
        validate_shared_watchlist_consistency()
        entries = load_email_watchlist()
        entity_map = load_entity_map()
        ambiguity_items = [
            dict(item, created_at=datetime.now().astimezone().isoformat())
            for item in load_relationship_ambiguities()
        ]
        if persist_watchlist_state:
            self.store.sync_relationship_reviews(ambiguity_items)
        filing_outcomes = discover_material_filings(
            entity_map,
            EdgarClient(sec_contact_email_from_env()),
            briefing_date=briefing_now().date(),
            cutoff=briefing_now(),
            state_store=self.store if persist_watchlist_state or read_edgar_state else None,
            persist_state=persist_watchlist_state,
        )
        quotes: dict[str, EndOfDayQuote | None] = {}
        stories: list[WatchlistStory] = []
        market_is_open = is_regular_nyse_market_hours()
        expected_close_date = expected_quote_close_date()
        if market_is_open:
            shared_quotes = dict(market_quotes or {})
            missing_tickers = [entry.ticker for entry in entries if entry.ticker not in shared_quotes]
            shared_quotes.update(fetch_yahoo_quotes(missing_tickers))
            live_quotes = {
                entry.ticker: _live_watchlist_quote(entry.ticker, shared_quotes.get(entry.ticker))
                for entry in entries
            }
        else:
            live_quotes = fetch_quotes_with_shared_deadline(
                tuple(entry.ticker for entry in entries), expected_close_date=expected_close_date
            )
        briefing_date = briefing_now().date().isoformat()
        discovery_by_key: dict[str, tuple[tuple[Article, ...], str]] = {}
        missing_keys: list[str] = []
        for key in distinct_discovery_keys(entity_map):
            payload = (
                self.store.successful_watchlist_source("yahoo", key, briefing_date)
                if persist_watchlist_state else None
            )
            if payload is None:
                missing_keys.append(key)
                continue
            try:
                discovery_by_key[key] = (deserialize_articles(payload), "")
            except (KeyError, TypeError, ValueError):
                missing_keys.append(key)
        fetched = discover_watchlist_keys_with_shared_deadline(tuple(missing_keys), enrichment_config)
        for key, (articles, error) in fetched.items():
            if persist_watchlist_state:
                self.store.cache_watchlist_source(
                    "yahoo",
                    key,
                    briefing_date,
                    state="FAILED" if error else "OK",
                    payload=None if error else serialize_articles(articles),
                    error_code=error,
                )
            discovery_by_key[key] = (articles, error)
        discovered_articles: dict[str, tuple[tuple[Article, ...], str]] = {}
        for ticker, entity in entity_map.tickers.items():
            routed: dict[str, Article] = {}
            errors: list[str] = []
            for key in entity.discovery_keys:
                key_articles, key_error = discovery_by_key.get(key, ((), "search_unavailable"))
                for article in key_articles:
                    routed.setdefault(article.canonical_url or article.url, article)
                if key_error:
                    errors.append(key_error)
            discovered_articles[ticker] = (tuple(routed.values()), errors[0] if errors else "")
        for entry in entries:
            quote = live_quotes[entry.ticker]
            if quote is None and not market_is_open and allow_cached_quotes:
                cached = self.store.cached_quote(
                    entry.ticker, expected_close_date=expected_close_date.isoformat()
                )
                if cached is not None:
                    close_date, close_price, previous_close, provider = cached
                    quote = EndOfDayQuote(entry.ticker, close_date, close_price, previous_close, provider)
            elif quote is not None and not market_is_open and persist_quotes:
                self.store.cache_quote(entry.ticker, quote.close_date, quote.close_price, quote.previous_close, quote.provider)
                self.store.record_quote_history(entry.ticker, quote.close_date, quote.close_price, quote.previous_close, quote.provider)
            quotes[entry.ticker] = quote
            articles, error = discovered_articles[entry.ticker]
            filing_outcome = filing_outcomes[entry.ticker]
            # Tier 5a reuses the general briefing's already-fetched and enriched
            # articles.  Entity classification below remains the single relevance
            # choke point for both general-feed and ticker-feed candidates.
            merged_articles = {
                article.canonical_url or article.url: article
                for article in (*articles, *general_articles)
                if not _excluded_editorial_host(article.canonical_url or article.url)
            }
            articles = tuple(merged_articles.values())
            classifications = [
                classify_text(
                    entity_map.tickers[entry.ticker],
                    "\n".join((article.title, article.summary, article.best_available_text[:3000])),
                    "subject",
                    current_governing_accession=filing_outcome.current_annual_accession,
                )
                for article in articles
            ]
            relevant_articles = tuple(
                article
                for article, classification in zip(articles, classifications, strict=True)
                if classification.label is not RelationshipLabel.MENTION_ONLY
            )
            relevant_classifications = [
                classification
                for classification in classifications
                if classification.label is not RelationshipLabel.MENTION_ONLY
            ]
            editorial_ids = tuple(
                "editorial:" + hashlib.sha256(
                    (entry.ticker + "|" + (article.canonical_url or article.url)).encode("utf-8")
                ).hexdigest()
                for article in relevant_articles
            )
            if not bypass_sent_suppression:
                retained = [
                    (article, classification, event_id)
                    for article, classification, event_id in zip(
                        relevant_articles, relevant_classifications, editorial_ids, strict=True
                    )
                    if not self.store.watchlist_event_was_sent(event_id, entry.ticker)
                ]
                relevant_articles = tuple(item[0] for item in retained)
                relevant_classifications = [item[1] for item in retained]
                editorial_ids = tuple(item[2] for item in retained)
            unsuppressed_filings = tuple(
                filing
                for filing in filing_outcome.filings
                if bypass_sent_suppression or not self.store.watchlist_event_was_sent(filing.accession, entry.ticker)
            )
            filing_urls = {filing.url.rstrip("/") for filing in unsuppressed_filings}
            if filing_urls:
                deduplicated = [
                    (article, classification, event_id)
                    for article, classification, event_id in zip(
                        relevant_articles, relevant_classifications, editorial_ids, strict=True
                    )
                    if (article.canonical_url or article.url).rstrip("/") not in filing_urls
                ]
                relevant_articles = tuple(item[0] for item in deduplicated)
                relevant_classifications = [item[1] for item in deduplicated]
                editorial_ids = tuple(item[2] for item in deduplicated)
            editorial_slots = max(0, 2 - min(2, len(unsuppressed_filings)))
            story = summarize_watchlist(
                entry,
                relevant_articles[:editorial_slots],
                budget,
                use_openai=use_openai,
            )
            if error:
                story = replace(story, search_error=error)
            classification_by_url = {
                article.canonical_url or article.url: classification
                for article, classification in zip(relevant_articles, relevant_classifications, strict=True)
            }
            first_classification = (
                classification_by_url.get(story.articles[0].canonical_url or story.articles[0].url)
                if story.articles else None
            )
            rendered_editorial_ids = tuple(
                "editorial:" + hashlib.sha256(
                    (entry.ticker + "|" + (article.canonical_url or article.url)).encode("utf-8")
                ).hexdigest()
                for article in story.articles
            )
            stories.append(replace(
                story,
                disclosures=unsuppressed_filings,
                official_retrieval_failed=filing_outcome.state is SourceState.FAILED,
                relationship_label=first_classification.label if first_classification else "",
                relationship_source=first_classification.relationship_source if first_classification else "",
                event_ids=tuple(
                    dict.fromkeys(
                        [filing.accession for filing in unsuppressed_filings]
                        + list(rendered_editorial_ids)
                    )
                ),
                official_state=filing_outcome.state.value,
                optional_state="FAILED" if error else "OK",
                eligible_filings=filing_outcome.eligible_count,
                processed_filings=filing_outcome.processed_count,
                expected_catchup_filings=filing_outcome.catchup_expected,
                processed_catchup_filings=filing_outcome.catchup_processed,
                filing_dispositions=filing_outcome.dispositions,
                filing_bodies=filing_outcome.filing_bodies,
                price_move_percent=quote.percent_change if quote is not None else None,
            ))
        watchlist_text, watchlist_html = render_watchlist_section(
            quotes,
            stories,
            gate_state=self.store.gate_state(),
            pending_relationships=self.store.pending_relationship_count(),
            gate_progress_notice=self.store.gate_progress_notice(),
        )
        rendered = render_minimal_newsletter(messages, header, watchlist_html, watchlist_text)
        return rendered, stories

    def send_edition(
        self,
        edition: EmailEdition,
        smtp_factory: SMTPFactory | None = None,
        *,
        retry_indeterminate: bool = False,
        force_resend: bool = False,
    ) -> list[RecipientOutcome]:
        if edition.edition_kind == "production" and not self.store.newsletter_send_allowed(edition.edition_id):
            raise ValueError("Email edition cannot send until its newsletter history update is applied.")
        settings = email_settings_from_env()
        outcomes: list[RecipientOutcome] = []
        with self.store.lock():
            for recipient in settings.recipients:
                previous = {outcome.recipient: outcome for outcome in self.store.delivery_outcomes(edition.edition_id)}
                previous_outcome = previous.get(recipient, RecipientOutcome(recipient, "prepared"))
                if not force_resend and (previous_outcome.state == "smtp_accepted" or (
                    previous_outcome.state == "indeterminate" and not retry_indeterminate
                )):
                    outcomes.append(previous_outcome)
                    continue
                self.store.record_delivery(edition.edition_id, RecipientOutcome(recipient, "sending"))
                try:
                    outcome = send_email(
                        settings,
                        recipient,
                        edition.subject,
                        edition.plain_text,
                        edition.html,
                        smtp_factory=smtp_factory,
                        message_id=(
                            f"<newsagent-gate-{edition.edition_id}@{settings.from_address.rsplit('@', 1)[-1]}>"
                            if edition.edition_kind == "gate_alert" else ""
                        ),
                    )
                except Exception as exc:  # State integrity must survive unexpected mailer defects.
                    outcome = RecipientOutcome(recipient, "failed", f"unhandled_{type(exc).__name__.lower()}")
                self.store.record_delivery(edition.edition_id, outcome)
                outcomes.append(outcome)
        for outcome in outcomes:
            if outcome.state != "smtp_accepted":
                print(
                    f"Warning: email to {outcome.recipient} did not reach SMTP acceptance "
                    f"({outcome.state}: {outcome.error_code or 'no_error_code'}).",
                    file=sys.stderr,
                )
        if edition.edition_kind == "gate_alert" and outcomes:
            terminal = (
                "smtp_accepted" if any(item.state == "smtp_accepted" for item in outcomes)
                else "indeterminate" if any(item.state == "indeterminate" for item in outcomes)
                else "failed"
            )
            self.store.record_failure_alert_terminal(terminal)
        if outcomes and all(item.state in {"smtp_accepted", "failed", "indeterminate"} for item in outcomes):
            self.store.cleanup_watchlist_retention()
            self.store.cleanup_newsletter_retention()
        return outcomes

    def status_lines(self, limit: int = 10) -> list[str]:
        lines: list[str] = []
        for edition in self.store.latest_editions(limit):
            outcomes = self.store.delivery_outcomes(edition.edition_id)
            recipient_state = ", ".join(f"{item.recipient}: {item.state}" for item in outcomes) or "no recipients"
            lines.append(f"{edition.edition_id} | {edition.local_date} r{edition.revision} | {edition.state} | {recipient_state}")
        return lines

    def resend(self, edition_id: int, confirmed: bool) -> list[RecipientOutcome]:
        if not confirmed:
            raise ValueError("Email resend requires --confirm because it may create a duplicate delivery.")
        edition = self.store.edition(edition_id)
        if edition is None:
            raise ValueError(f"Unknown email edition: {edition_id}")
        return self.send_edition(edition, retry_indeterminate=True, force_resend=True)


def _live_watchlist_quote(ticker: str, quote: StockQuote | None) -> EndOfDayQuote | None:
    """Adapt the Finance section's Yahoo quote for the Watchlist renderer."""
    if quote is None or quote.price is None or quote.previous_close in (None, 0):
        return None
    return EndOfDayQuote(
        ticker=ticker,
        close_date=briefing_now().date().isoformat(),
        close_price=quote.price,
        previous_close=quote.previous_close,
        provider=quote.provider,
        quote_kind="live",
    )


def _watchlist_run_record(story: WatchlistStory) -> dict[str, object]:
    documents: list[dict[str, object]] = []
    event_documents: list[tuple[str, str]] = []
    filing_bodies = dict(story.filing_bodies)
    for filing in story.disclosures:
        document_id = f"sec:{getattr(filing, 'accession', '')}"
        documents.append({
            "document_id": document_id,
            "source_id": "edgar",
            "accession": getattr(filing, "accession", ""),
            "form_type": getattr(filing, "form", ""),
            "accepted_at": getattr(filing, "accepted_at", None).isoformat()
                if getattr(filing, "accepted_at", None) is not None else None,
            "canonical_url": getattr(filing, "url", ""),
            "body": filing_bodies.get(str(getattr(filing, "accession", ""))),
            "metadata": {
                "description": getattr(filing, "description", ""),
                "items": list(getattr(filing, "items", ())),
                "headline": getattr(filing, "headline", ""),
                "event_key": getattr(filing, "event_key", ""),
            },
        })
        event_documents.append((str(getattr(filing, "accession", "")), document_id))
    article_event_ids = [event_id for event_id in story.event_ids if event_id.startswith("editorial:")]
    for article, event_id in zip(story.articles, article_event_ids, strict=False):
        document_id = "url:" + hashlib.sha256((article.canonical_url or article.url).encode("utf-8")).hexdigest()
        documents.append({
            "document_id": document_id,
            "source_id": "editorial",
            "canonical_url": article.canonical_url or article.url,
            "body": article.best_available_text,
            "metadata": {"title": article.title, "publisher": article.source},
        })
        event_documents.append((event_id, document_id))
    return {
        "ticker": story.ticker,
        "official_state": story.official_state,
        "optional_state": story.optional_state,
        "content_state": "CONTENT" if story.event_ids else (
            "INCOMPLETE" if story.classification_incomplete else "QUIET"
        ),
        "retrieval_state": "FAILED" if story.official_retrieval_failed else (
            "PARTIAL" if story.search_error or story.classification_incomplete else "COMPLETE"
        ),
        "event_ids": list(story.event_ids),
        "relationship_label": str(story.relationship_label),
        "relationship_source": story.relationship_source,
        "eligible_filings": story.eligible_filings,
        "processed_filings": story.processed_filings,
        "expected_catchup_filings": story.expected_catchup_filings,
        "processed_catchup_filings": story.processed_catchup_filings,
        "filing_dispositions": list(story.filing_dispositions),
        "price_move_percent": story.price_move_percent,
        "classification_incomplete": story.classification_incomplete,
        "documents": documents,
        "event_documents": event_documents,
    }


def _excluded_editorial_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    return any(host == excluded or host.endswith("." + excluded) for excluded in EXCLUDED_HOSTS)
