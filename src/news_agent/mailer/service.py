from __future__ import annotations

import sys
from datetime import datetime

from news_agent.formatting import FormattedMessage
from news_agent.mailer.models import EmailEdition, RecipientOutcome
from news_agent.mailer.render import RenderedEmail, render_parity_email
from news_agent.mailer.render import render_minimal_newsletter, render_watchlist_section
from news_agent.mailer.settings import email_settings_from_env
from news_agent.mailer.smtp import SMTPFactory, send_email
from news_agent.mailer.state import EmailStateStore
from news_agent.mailer.quotes import EndOfDayQuote, fetch_quotes_with_shared_deadline, validate_quote_provider_configuration
from news_agent.mailer.watchlist import load_email_watchlist, validate_shared_watchlist_consistency
from news_agent.mailer.watchlist_news import WatchlistStory, discover_watchlists_with_shared_deadline, summarize_watchlist
from news_agent.models import EnrichmentConfig
from news_agent.openai_budget import OpenAIBudget
from news_agent.time import briefing_now


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
    ) -> EmailEdition:
        rendered, stories = self.render_newsletter(messages, header, enrichment_config, budget)
        today = briefing_now().date().isoformat()
        story_ids = [(message.title, "general") for message in messages]
        story_ids.extend((story.ticker, "watchlist") for story in stories)
        if test_revision:
            return self.store.prepare_test_revision(today, rendered.subject, rendered.plain_text, rendered.html, story_ids)
        return self.store.prepare_edition(today, rendered.subject, rendered.plain_text, rendered.html, story_ids)

    def render_newsletter(
        self,
        messages: list[FormattedMessage],
        header: str,
        enrichment_config: EnrichmentConfig,
        budget: OpenAIBudget,
        *,
        persist_quotes: bool = True,
    ) -> tuple[RenderedEmail, list[WatchlistStory]]:
        validate_quote_provider_configuration()
        validate_shared_watchlist_consistency()
        entries = load_email_watchlist()
        quotes: dict[str, EndOfDayQuote | None] = {}
        stories: list[WatchlistStory] = []
        live_quotes = fetch_quotes_with_shared_deadline(tuple(entry.ticker for entry in entries))
        discovered_articles = discover_watchlists_with_shared_deadline(entries, enrichment_config)
        for entry in entries:
            quote = live_quotes[entry.ticker]
            if quote is None:
                cached = self.store.cached_quote(entry.ticker)
                if cached is not None:
                    close_date, close_price, previous_close, provider = cached
                    quote = EndOfDayQuote(entry.ticker, close_date, close_price, previous_close, provider)
            elif persist_quotes:
                self.store.cache_quote(entry.ticker, quote.close_date, quote.close_price, quote.previous_close, quote.provider)
            quotes[entry.ticker] = quote
            articles, error = discovered_articles[entry.ticker]
            if error:
                stories.append(WatchlistStory(entry.ticker, search_error=error))
            else:
                stories.append(summarize_watchlist(entry, articles, budget))
        watchlist_text, watchlist_html = render_watchlist_section(quotes, stories)
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
