from __future__ import annotations

import argparse
import sys
from pathlib import Path

from news_agent.alerts import DEFAULT_ALERT_CONFIG_PATH, DEFAULT_ALERT_HISTORY_PATH, save_alert_history
from news_agent.config import DEFAULT_CONFIG_PATH, load_config
from news_agent.env import load_dotenv
from news_agent.formatting import FormatMode, FormatOptions, format_briefing_previews, format_console_preview
from news_agent.history import DEFAULT_HISTORY_PATH
from news_agent.mailer.service import EmailService
from news_agent.mailer.schedule import scheduled_email_is_due
from news_agent.mailer.settings import email_settings_from_env
from news_agent.notifications.base import NotificationError
from news_agent.notifications.factory import selected_channel, send_briefing_messages, send_telegram_test_message
from news_agent.pipeline import OpenAIMode, build_alert_result_sync, build_briefing_result_sync
from news_agent.openai_budget import OpenAIBudget
from news_agent.quality_gate import DEFAULT_QUALITY_GATE_REJECTIONS_DIR, format_quality_gate_rejections
from news_agent.quality_report import aggregate_source_rejections, format_source_rejection_report
from news_agent.skipped_log import format_skipped_table
from news_agent.watchlist import DEFAULT_WATCHLIST_PATH
from news_agent.time import briefing_today


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Build and send five morning news briefing messages.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST_PATH, help="Path to watchlist JSON/YAML.")
    parser.add_argument("--history-path", type=Path, default=DEFAULT_HISTORY_PATH, help="Story history JSON path.")
    parser.add_argument("--ignore-history", action="store_true", help="Do not suppress stories seen in history.")
    parser.add_argument("--show-skipped", action="store_true", help="Print skipped-story audit output.")
    parser.add_argument("--show-diagnostics", action="store_true", help="Print enrichment and drafting diagnostics.")
    parser.add_argument("--alerts", action="store_true", help="Run breaking-news alert mode instead of the briefing.")
    parser.add_argument("--alert-config", type=Path, default=DEFAULT_ALERT_CONFIG_PATH, help="Alert config JSON path.")
    parser.add_argument(
        "--alert-history-path",
        type=Path,
        default=DEFAULT_ALERT_HISTORY_PATH,
        help="Alert cooldown history JSON path.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print messages instead of sending them.")
    parser.add_argument("--send", action="store_true", help="Send messages through the configured delivery channel.")
    parser.add_argument("--test-telegram", action="store_true", help="Send one Telegram test message and exit.")
    parser.add_argument(
        "--quality-report",
        action="store_true",
        help="Print a per-source quality-gate rejection report and exit.",
    )
    parser.add_argument(
        "--report-days",
        type=int,
        default=7,
        help="Lookback window in calendar days for --quality-report (default: 7).",
    )
    parser.add_argument("--channel", choices=("telegram", "sms"), help="Deprecated override for BRIEFING_DELIVERY_CHANNEL.")
    parser.add_argument("--to", choices=("email", "telegram", "both"), help="Explicit V1 delivery target.")
    parser.add_argument(
        "--email-parity",
        action="store_true",
        help="Temporary Gmail-only Telegram-format smoke test; does not build the Watchlist newsletter.",
    )
    parser.add_argument("--email-status", action="store_true", help="Print recent email delivery outcomes and exit.")
    parser.add_argument("--email-resend", type=int, metavar="EDITION_ID", help="Resend a stored email edition.")
    parser.add_argument("--confirm", action="store_true", help="Confirm a potentially duplicate email resend.")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Apply the 8:15 AM local-time guard for a launchd-triggered email delivery.",
    )
    parser.add_argument(
        "--format",
        choices=("sms", "telegram", "console", "email"),
        help="Output format. Defaults to console for dry-run, sms for SMS sends, and telegram for Telegram sends.",
    )
    parser.add_argument(
        "--openai-mode",
        choices=("full", "classify-only", "off"),
        default="full",
        help=(
            "Choose OpenAI usage: full judges/classifies/drafts; classify-only judges and "
            "classifies but uses extractive drafts; off makes no OpenAI calls."
        ),
    )
    parser.add_argument(
        "--no-openai",
        action="store_true",
        help="Alias for --openai-mode off.",
    )
    parser.add_argument(
        "--no-openai-drafting",
        action="store_true",
        help="Alias for --openai-mode classify-only.",
    )
    compression_group = parser.add_mutually_exclusive_group()
    compression_group.add_argument(
        "--compress",
        dest="compression_override",
        action="store_const",
        const=True,
        default=None,
        help="Enable the post-draft concise compression pass for this run.",
    )
    compression_group.add_argument(
        "--no-compress",
        dest="compression_override",
        action="store_const",
        const=False,
        help="Disable the post-draft concise compression pass for this run.",
    )
    args = parser.parse_args(argv)

    if args.to and args.channel:
        parser.error("--to cannot be combined with --channel")
    if args.email_parity and args.to not in {"email", "both"}:
        parser.error("--email-parity requires --to email or --to both")
    if args.scheduled and (not args.send or args.to != "email"):
        parser.error("--scheduled requires --send with --to email")
    if args.email_status:
        if args.dry_run or args.send or args.alerts or args.email_resend is not None:
            parser.error("--email-status cannot be combined with delivery options")
        for line in EmailService().status_lines():
            print(line)
        return
    if args.email_resend is not None:
        if args.dry_run or args.send or args.alerts:
            parser.error("--email-resend cannot be combined with normal delivery options")
        try:
            outcomes = EmailService().resend(args.email_resend, args.confirm)
        except (NotificationError, ValueError) as exc:
            raise SystemExit(f"Email resend failed: {exc}") from exc
        print(f"Email resend completed for {sum(item.state == 'smtp_accepted' for item in outcomes)} recipient(s).")
        return

    if args.no_openai and args.no_openai_drafting:
        parser.error("--no-openai and --no-openai-drafting cannot be used together")

    if args.test_telegram:
        try:
            recipient_count = send_telegram_test_message()
        except NotificationError as exc:
            raise SystemExit(f"Telegram test failed: {exc}") from exc
        print(f"Telegram test message sent to {recipient_count} recipient(s).")
        return

    if args.quality_report:
        counts = aggregate_source_rejections(DEFAULT_QUALITY_GATE_REJECTIONS_DIR, args.report_days)
        print(format_source_rejection_report(counts))
        return

    if not args.dry_run and not args.send:
        parser.error("Choose --dry-run, --send, or --test-telegram.")

    if args.channel:
        print("Warning: --channel is deprecated; use --to telegram for Telegram delivery.", file=sys.stderr)
    if args.scheduled and not scheduled_email_is_due():
        print("Scheduled email skipped: it is outside the 8:20–8:35 AM BRIEFING_TIMEZONE retry window.")
        return

    try:
        config = load_config(
            args.config,
            compression_enabled_override=args.compression_override,
        )
    except ValueError as exc:
        parser.error(str(exc))
    delivery_target = args.to
    if args.alerts and delivery_target in {"email", "both"}:
        parser.error("--alerts does not support email delivery")
    if args.send and delivery_target in {"email", "both"}:
        try:
            email_settings_from_env()
        except NotificationError as exc:
            raise SystemExit(f"Email preflight failed: {exc}") from exc
    format_mode = resolve_format_mode(args.format, args.dry_run, args.channel, delivery_target)

    if args.alerts:
        result = build_alert_result_sync(
            config=config,
            watchlist_path=args.watchlist,
            alert_config_path=args.alert_config,
            alert_history_path=args.alert_history_path,
        )
        messages = [alert.to_message() for alert in result.alerts]
        if args.dry_run:
            if not messages:
                print("No alerts triggered.")
                return
            for index, message in enumerate(messages, start=1):
                print(f"\n--- ALERT {index}/{len(messages)} ---")
                print(message)
            return
        if not messages:
            print("No alerts triggered.")
            return
        try:
            sent_count = send_briefing_messages(
                messages,
                channel=args.channel,
                header=f"Breaking News Alerts - {briefing_today().isoformat()}",
            )
        except NotificationError as exc:
            raise SystemExit(f"Alert send failed: {exc}") from exc
        save_alert_history(result.alerts, args.alert_history_path)
        print(f"Sent {sent_count} alert message(s).")
        return

    openai_mode: OpenAIMode = (
        "off" if args.no_openai else "classify-only" if args.no_openai_drafting else args.openai_mode
    )
    result = build_briefing_result_sync(
        openai_mode=openai_mode,
        config=config,
        watchlist_path=args.watchlist,
        history_path=args.history_path,
        ignore_history=args.ignore_history,
        persist_history=args.send,
    )
    options = FormatOptions.from_config(config.formatting, mode=format_mode)
    formatted_messages = format_briefing_previews(result.briefings, options=options)
    messages = [message.text for message in formatted_messages]
    diagnostics = getattr(result, "diagnostics", None)
    if diagnostics is not None and getattr(diagnostics, "fallback_drafts", 0):
        print(
            f"Warning: {diagnostics.fallback_drafts}/"
            f"{diagnostics.fallback_drafts + diagnostics.llm_drafts} stories used deterministic fallback.",
            file=sys.stderr,
        )

    if args.dry_run:
        if delivery_target in {"email", "both"}:
            email_messages = format_briefing_previews(
                result.briefings,
                options=FormatOptions.from_config(config.formatting, mode="email"),
            )
            service = EmailService()
            budget = getattr(result, "openai_budget", None) or OpenAIBudget(config.openai_costs)
            if args.email_parity:
                plain_text = service.render_parity(email_messages, f"Morning Briefing - {briefing_today().isoformat()}").plain_text
            else:
                plain_text, _stories = service.render_newsletter(
                    email_messages,
                    f"Morning Briefing - {briefing_today().isoformat()}",
                    config.enrichment,
                    budget,
                    persist_quotes=False,
                )
                plain_text = plain_text.plain_text
            if delivery_target == "both":
                print("===== TELEGRAM =====")
                for index, message in enumerate(messages, start=1):
                    print(f"\n--- MESSAGE {index}/{len(messages)} ---")
                    print(message)
                print("\n===== EMAIL PLAIN TEXT =====")
            print(plain_text, end="")
            return
        if format_mode == "console":
            print(format_console_preview(formatted_messages))
        else:
            for index, message in enumerate(messages, start=1):
                divider = f"\n--- MESSAGE {index}/{len(messages)} ---"
                print(divider)
                print(message)
        if args.show_skipped:
            if result.source_debug_lines:
                print()
                print("Source distribution")
                for line in result.source_debug_lines:
                    print(line)
            print_quality_gate_rejections(getattr(result, "quality_gate_rejections", ()))
            print()
            print(format_skipped_table(result.skipped_stories))
        if args.show_diagnostics:
            print_diagnostics(diagnostics)
        return

    try:
        if delivery_target == "email":
            service = EmailService()
            email_messages = format_briefing_previews(
                result.briefings,
                options=FormatOptions.from_config(config.formatting, mode="email"),
            )
            budget = getattr(result, "openai_budget", None) or OpenAIBudget(config.openai_costs)
            edition = (
                service.prepare_parity_edition(email_messages, f"Morning Briefing - {briefing_today().isoformat()}")
                if args.email_parity
                else service.prepare_newsletter_edition(
                    email_messages,
                    f"Morning Briefing - {briefing_today().isoformat()}",
                    config.enrichment,
                    budget,
                )
            )
            outcomes = service.send_edition(edition)
            accepted_count = accepted_email_count_or_raise(outcomes)
            print(f"Sent email to {accepted_count} recipient(s).")
        elif delivery_target == "both":
            sent_count = send_briefing_messages(messages, channel="telegram")
            service = EmailService()
            email_messages = format_briefing_previews(
                result.briefings,
                options=FormatOptions.from_config(config.formatting, mode="email"),
            )
            budget = getattr(result, "openai_budget", None) or OpenAIBudget(config.openai_costs)
            edition = (
                service.prepare_parity_edition(email_messages, f"Morning Briefing - {briefing_today().isoformat()}")
                if args.email_parity
                else service.prepare_newsletter_edition(
                    email_messages,
                    f"Morning Briefing - {briefing_today().isoformat()}",
                    config.enrichment,
                    budget,
                )
            )
            outcomes = service.send_edition(edition)
            accepted_count = accepted_email_count_or_raise(outcomes)
            print(
                f"Sent {sent_count} Telegram message(s) and email to "
                f"{accepted_count} recipient(s)."
            )
        else:
            sent_count = send_briefing_messages(messages, channel=args.channel)
            print(f"Sent {sent_count} message(s).")
    except (NotificationError, ValueError) as exc:
        raise SystemExit(f"Send failed: {exc}") from exc
    if args.show_skipped:
        if result.source_debug_lines:
            print()
            print("Source distribution")
            for line in result.source_debug_lines:
                print(line)
        print_quality_gate_rejections(getattr(result, "quality_gate_rejections", ()))
        print()
        print(format_skipped_table(result.skipped_stories))
    if args.show_diagnostics:
        print_diagnostics(diagnostics)


def print_diagnostics(diagnostics: object) -> None:
    if diagnostics is None:
        print("Diagnostics unavailable.")
        return
    print()
    print("Pipeline diagnostics")
    print(f"Articles fetched: {getattr(diagnostics, 'articles_fetched', 0)}")
    print(f"Rich feed entries: {getattr(diagnostics, 'feed_content_articles', 0)}")
    print(f"Pages attempted: {getattr(diagnostics, 'pages_attempted', 0)}")
    print(f"Pages extracted: {getattr(diagnostics, 'pages_extracted', 0)}")
    print(f"Pages blocked: {getattr(diagnostics, 'pages_blocked', 0)}")
    print(f"Pages failed/thin: {getattr(diagnostics, 'pages_failed', 0)}")
    print(f"LLM drafts: {getattr(diagnostics, 'llm_drafts', 0)}")
    print(f"Fallback drafts: {getattr(diagnostics, 'fallback_drafts', 0)}")
    print(f"Drafting input tokens: {getattr(diagnostics, 'drafting_input_tokens', 0)}")
    print(f"Drafting output tokens: {getattr(diagnostics, 'drafting_output_tokens', 0)}")
    print(f"Drafting cost: ${getattr(diagnostics, 'drafting_cost_usd', 0.0):.6f}")
    print(f"Drafting budget exhausted: {getattr(diagnostics, 'drafting_budget_exhausted', False)}")
    print(f"Compressed paragraphs: {getattr(diagnostics, 'compressed_count', 0)}")
    print(f"Compression statuses: {getattr(diagnostics, 'compression_status_counts', {})}")
    print(f"Median compression ratio: {getattr(diagnostics, 'median_compression_ratio', 0.0):.1%}")
    print(f"Compression guard failures: {getattr(diagnostics, 'guard_failures', 0)}")
    print(f"Compression entity warnings: {getattr(diagnostics, 'entity_warnings', 0)}")
    print(f"Compression cost: ${getattr(diagnostics, 'compression_cost_usd', 0.0):.6f}")
    print(f"Compression budget exhausted: {getattr(diagnostics, 'compression_budget_exhausted', False)}")
    print(f"Total OpenAI input tokens: {getattr(diagnostics, 'openai_input_tokens', 0)}")
    print(f"Total OpenAI output tokens: {getattr(diagnostics, 'openai_output_tokens', 0)}")
    print(f"Total OpenAI cost: ${getattr(diagnostics, 'openai_cost_usd', 0.0):.6f}")
    print(f"OpenAI cost by stage: {getattr(diagnostics, 'openai_cost_by_stage', {})}")
    print(f"OpenAI stage outcomes: {getattr(diagnostics, 'openai_stage_outcomes', {})}")
    print(f"Overall OpenAI budget exhausted: {getattr(diagnostics, 'openai_budget_exhausted', False)}")
    print("Feed-hint pipeline")
    for field_name in (
        "fetched_articles_by_feed_hint",
        "preliminary_clusters_by_feed_hint",
        "enrichment_clusters_by_feed_hint",
        "classification_pool_by_feed_hint",
        "history_suppressed_by_feed_hint",
        "insufficient_context_by_feed_hint",
    ):
        print(f"{field_name}: {getattr(diagnostics, field_name, {})}")
    print("Classified results")
    for field_name in (
        "classified_clusters_by_category",
        "backfill_candidates_by_category",
        "selected_stories_by_category",
        "underfilled_reason_by_category",
        "importance_by_category",
        "floor_selected_by_category",
        "remainder_selected_by_category",
        "big_day_selected_by_category",
        "source_cap_relaxed_by_category",
    ):
        print(f"{field_name}: {getattr(diagnostics, field_name, {})}")
    print(
        "Deck: "
        f"{getattr(diagnostics, 'deck_selected', 0)}/{getattr(diagnostics, 'deck_target', 0)} "
        f"({getattr(diagnostics, 'deck_underfilled_reason', '') or 'full'})"
    )


def accepted_email_count_or_raise(outcomes: list[object]) -> int:
    accepted = [outcome for outcome in outcomes if getattr(outcome, "state", "") == "smtp_accepted"]
    if accepted:
        return len(accepted)
    errors = ", ".join(
        f"{getattr(outcome, 'recipient', 'unknown')}={getattr(outcome, 'error_code', '') or getattr(outcome, 'state', 'failed')}"
        for outcome in outcomes
    ) or "no recipient outcomes"
    raise NotificationError(f"No Gmail recipient reached SMTP acceptance ({errors}).")


def print_quality_gate_rejections(quality_gate_rejections: object) -> None:
    rejections = list(quality_gate_rejections or ())
    if not rejections:
        return
    print()
    print(f"Quality gate rejections: {len(rejections)}")
    for entry in format_quality_gate_rejections(rejections):
        print(f"{entry['reason']} | {entry['source']} | {entry['title']}")


def resolve_format_mode(
    requested: str | None,
    dry_run: bool,
    channel: str | None,
    delivery_target: str | None = None,
) -> FormatMode:
    if requested is not None:
        return requested  # type: ignore[return-value]
    if dry_run:
        return "console"
    if delivery_target == "email":
        return "email"
    if delivery_target in {"telegram", "both"}:
        return "telegram"
    if selected_channel(channel) == "sms":
        return "sms"
    return "telegram"


if __name__ == "__main__":
    main()
