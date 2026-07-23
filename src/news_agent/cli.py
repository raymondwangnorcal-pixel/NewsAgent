from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from news_agent.alerts import DEFAULT_ALERT_CONFIG_PATH, DEFAULT_ALERT_HISTORY_PATH, save_alert_history
from news_agent.config import DEFAULT_CONFIG_PATH, load_config
from news_agent.env import load_dotenv
from news_agent.formatting import FormatMode, FormatOptions, format_briefing_previews, format_console_preview
from news_agent.history import DEFAULT_HISTORY_PATH
from news_agent.notifications.base import NotificationError
from news_agent.notifications.factory import selected_channel, send_briefing_messages, send_telegram_test_message
from news_agent.pipeline import OpenAIMode, build_alert_result_sync, build_briefing_result_sync
from news_agent.quality_gate import DEFAULT_QUALITY_GATE_REJECTIONS_DIR, format_quality_gate_rejections
from news_agent.quality_report import aggregate_source_rejections, format_source_rejection_report
from news_agent.skipped_log import format_skipped_table
from news_agent.watchlist import DEFAULT_WATCHLIST_PATH


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
    parser.add_argument("--channel", choices=("telegram", "sms"), help="Override BRIEFING_DELIVERY_CHANNEL.")
    parser.add_argument(
        "--format",
        choices=("sms", "telegram", "console"),
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
    args = parser.parse_args(argv)

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

    config = load_config(args.config)
    format_mode = resolve_format_mode(args.format, args.dry_run, args.channel)

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
                header=f"Breaking News Alerts - {date.today().isoformat()}",
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
        sent_count = send_briefing_messages(messages, channel=args.channel)
    except NotificationError as exc:
        raise SystemExit(f"Send failed: {exc}") from exc
    print(f"Sent {sent_count} message(s).")
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


def print_quality_gate_rejections(quality_gate_rejections: object) -> None:
    rejections = list(quality_gate_rejections or ())
    if not rejections:
        return
    print()
    print(f"Quality gate rejections: {len(rejections)}")
    for entry in format_quality_gate_rejections(rejections):
        print(f"{entry['reason']} | {entry['source']} | {entry['title']}")


def resolve_format_mode(requested: str | None, dry_run: bool, channel: str | None) -> FormatMode:
    if requested is not None:
        return requested  # type: ignore[return-value]
    if dry_run:
        return "console"
    if selected_channel(channel) == "sms":
        return "sms"
    return "telegram"


if __name__ == "__main__":
    main()
