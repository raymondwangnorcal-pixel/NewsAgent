from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from news_agent.alerts import DEFAULT_ALERT_CONFIG_PATH, DEFAULT_ALERT_HISTORY_PATH, save_alert_history
from news_agent.config import DEFAULT_CONFIG_PATH, load_config
from news_agent.env import load_dotenv
from news_agent.formatting import FormatMode, FormatOptions, format_briefing_previews, format_console_preview
from news_agent.history import DEFAULT_HISTORY_PATH
from news_agent.mailer.service import EmailService
from news_agent.mailer.state import EmailStateStore
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
from news_agent.watchlist.benchmark import load_benchmark_candidates
from news_agent.watchlist.edgar import sec_contact_email_from_env
from news_agent.watchlist.entity_map import load_entity_map, load_relationship_ambiguities
from news_agent.watchlist.gate import activate_gate
from news_agent.watchlist.models import ActivationPreflight


def main(argv: list[str] | None = None) -> None:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    target = effective_argv[effective_argv.index("--to") + 1] if "--to" in effective_argv and effective_argv.index("--to") + 1 < len(effective_argv) else ""
    needs_build_lock = (
        (
            target in {"email", "both"}
            and ("--send" in effective_argv or "--dry-run" in effective_argv)
            and "--email-resend" not in effective_argv
            and "--email-parity" not in effective_argv
        )
        or "--activate-watchlist-gate" in effective_argv
        or "--restart-after-gate-failure" in effective_argv
    )
    if not needs_build_lock:
        _main(effective_argv)
        return
    try:
        with EmailStateStore().lock():
            _main(effective_argv)
    except RuntimeError as exc:
        if str(exc) == "another build is already running":
            raise SystemExit("another build is already running") from exc
        raise


def _main(argv: list[str] | None = None) -> None:
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
    parser.add_argument("--email-rebuild-today", action="store_true", help="Build and send a fresh same-day Gmail test revision.")
    parser.add_argument("--watchlist-benchmark-import", type=Path, help="Import independently researched Watchlist benchmark JSON/JSONL and exit.")
    parser.add_argument("--review-watchlist-benchmark", action="store_true", help="Review imported benchmark events one at a time and exit.")
    parser.add_argument("--review-watchlist-relationships", action="store_true", help="Review ambiguous Watchlist relationships one at a time and exit.")
    parser.add_argument("--review-watchlist-evaluations", action="store_true", help="Adjudicate rendered Watchlist events and large-move quiet days one at a time.")
    parser.add_argument("--review-newsletter-evaluations", action="store_true", help="Adjudicate sent and filtered newsletter candidates one at a time.")
    parser.add_argument("--review-scope", choices=("all", "sent", "filtered"), default="all")
    parser.add_argument("--review-limit", type=int, default=20)
    parser.add_argument("--review-days", type=int, default=30)
    parser.add_argument("--newsletter-review-batch-create", action="store_true", help="Freeze a randomized newsletter filtered-review batch.")
    parser.add_argument("--newsletter-example-add", action="store_true", help="Add an independently sourced newsletter example.")
    parser.add_argument("--newsletter-example-import", type=Path, help="Import newsletter examples from JSON or JSONL.")
    parser.add_argument("--review-newsletter-examples", action="store_true", help="Review independently sourced newsletter examples.")
    parser.add_argument("--newsletter-evaluation-report", action="store_true", help="Print newsletter relevance metrics and exit.")
    parser.add_argument("--newsletter-labels-export", type=Path, help="Export newsletter labels as JSONL and exit.")
    parser.add_argument("--activate-watchlist-gate", action="store_true", help="Run a full no-send preflight and activate Gate A.")
    parser.add_argument("--restart-after-gate-failure", action="store_true", help="Run the confirmed no-send recovery health check.")
    parser.add_argument("--implementation-version", help="Version identifier evaluated by the Watchlist preflight.")
    parser.add_argument("--tests-passed", action="store_true", help="Confirm the required test suite passed for --implementation-version.")
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
    if args.email_rebuild_today and (not args.send or args.to != "email" or not args.confirm or args.scheduled or args.email_resend is not None):
        parser.error("--email-rebuild-today requires --send --to email --confirm and cannot be scheduled or combined with --email-resend")
    if args.activate_watchlist_gate and not (
        args.dry_run
        and args.to == "email"
        and args.show_diagnostics
        and args.confirm
        and args.tests_passed
        and args.implementation_version
        and not args.send
    ):
        parser.error(
            "--activate-watchlist-gate requires --dry-run --to email --show-diagnostics "
            "--confirm --tests-passed --implementation-version VERSION"
        )
    if args.restart_after_gate_failure:
        if not args.confirm or args.send or args.dry_run or args.activate_watchlist_gate or args.scheduled:
            parser.error("--restart-after-gate-failure requires --confirm and cannot be combined with delivery options")
        args.dry_run = True
        args.to = "email"
        args.show_diagnostics = True
        if not args.implementation_version:
            args.implementation_version = "watchlist-v1"
        if not EmailStateStore().gate_recovery_allowed():
            parser.error("--restart-after-gate-failure requires a halted failed Gate A window")
    if args.watchlist_benchmark_import:
        if args.dry_run or args.send or args.alerts or args.email_resend is not None:
            parser.error("--watchlist-benchmark-import cannot be combined with delivery options")
        entity_map = load_entity_map()
        candidates = load_benchmark_candidates(args.watchlist_benchmark_import, set(entity_map.tickers))
        imported_at = datetime.now(timezone.utc).isoformat()
        inserted = EmailStateStore().import_benchmark_events([
            {
                "ticker": item.ticker,
                "event_date": item.event_date.isoformat(),
                "source_url": item.source_url,
                "headline": item.headline,
                "materiality_rationale": item.materiality_rationale,
                "provenance": item.provenance,
                "imported_at": imported_at,
            }
            for item in candidates
        ])
        print(f"Imported {inserted} independent Watchlist benchmark event(s).")
        return
    if args.review_watchlist_benchmark:
        if args.dry_run or args.send or args.alerts:
            parser.error("--review-watchlist-benchmark cannot be combined with delivery options")
        _review_benchmark_events(EmailStateStore())
        return
    if args.review_watchlist_relationships:
        if args.dry_run or args.send or args.alerts:
            parser.error("--review-watchlist-relationships cannot be combined with delivery options")
        store = EmailStateStore()
        created_at = datetime.now(timezone.utc).isoformat()
        items = [dict(item, created_at=created_at) for item in load_relationship_ambiguities()]
        store.sync_relationship_reviews(items)
        _review_relationships(store)
        return
    if args.review_watchlist_evaluations:
        if args.dry_run or args.send or args.alerts:
            parser.error("--review-watchlist-evaluations cannot be combined with delivery options")
        _review_watchlist_evaluations(EmailStateStore())
        return
    if args.review_newsletter_evaluations:
        if args.dry_run or args.send or args.alerts:
            parser.error("--review-newsletter-evaluations cannot be combined with delivery options")
        _review_newsletter_evaluations(EmailStateStore(), args.review_scope, args.review_limit)
        return
    if args.newsletter_review_batch_create:
        if args.dry_run or args.send or args.alerts:
            parser.error("--newsletter-review-batch-create cannot be combined with delivery options")
        from news_agent.newsletter_review import LABEL_SCHEMA_VERSION
        store = EmailStateStore()
        version, days = store.newsletter_pilot_status(LABEL_SCHEMA_VERSION)
        batch = store.create_newsletter_review_batch(LABEL_SCHEMA_VERSION)
        print(f"Created newsletter review batch {batch['batch_id']}." if batch else f"Filtered review unavailable — pilot {days} of 7 eligible days.")
        return
    if args.newsletter_example_add or args.newsletter_example_import or args.review_newsletter_examples:
        if args.dry_run or args.send or args.alerts:
            parser.error("newsletter example commands cannot be combined with delivery options")
        store = EmailStateStore()
        if args.newsletter_example_import:
            raw = args.newsletter_example_import.read_text(encoding="utf-8")
            loaded = json.loads(raw) if raw.lstrip().startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
            print(f"Imported {store.import_newsletter_examples(loaded)} example(s).")
        elif args.newsletter_example_add:
            _add_newsletter_examples(store)
        else:
            _review_newsletter_examples(store)
        return
    if args.newsletter_evaluation_report:
        if args.dry_run or args.send or args.alerts:
            parser.error("--newsletter-evaluation-report cannot be combined with delivery options")
        from news_agent.newsletter_review import format_newsletter_metrics, newsletter_metrics
        print(format_newsletter_metrics(newsletter_metrics(EmailStateStore().newsletter_label_rows())))
        return
    if args.newsletter_labels_export:
        if args.dry_run or args.send or args.alerts:
            parser.error("--newsletter-labels-export cannot be combined with delivery options")
        rows = EmailStateStore().export_newsletter_labels()
        args.newsletter_labels_export.parent.mkdir(parents=True, exist_ok=True)
        args.newsletter_labels_export.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        print(f"Exported {len(rows)} newsletter label(s).")
        return
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
    if args.scheduled and not EmailStateStore().scheduled_work_allowed():
        print("Scheduled NewsAgent work halted after Gate A failure; run --restart-after-gate-failure --confirm.")
        return

    try:
        config = load_config(
            args.config,
            compression_enabled_override=args.compression_override,
        )
    except ValueError as exc:
        parser.error(str(exc))
    delivery_target = args.to
    email_recipients: tuple[str, ...] = ()
    if args.alerts and delivery_target in {"email", "both"}:
        parser.error("--alerts does not support email delivery")
    if args.send and delivery_target in {"email", "both"}:
        try:
            email_settings = email_settings_from_env()
            if args.scheduled:
                email_recipients = email_settings.recipients
        except NotificationError as exc:
            raise SystemExit(f"Email preflight failed: {exc}") from exc
    if args.scheduled:
        gate_store = EmailStateStore()
        if gate_store.gate_failure_alert_pending():
            service = EmailService(gate_store)
            edition = gate_store.prepare_gate_failure_alert(briefing_today().isoformat())
            outcomes = service.send_edition(edition)
            accepted_count = accepted_email_count_or_raise(outcomes)
            print(f"Sent Gate A failure alert to {accepted_count} recipient(s); scheduled work is now halted.")
            return
        if gate_store.production_delivery_complete(briefing_today().isoformat(), email_recipients):
            print("Scheduled email skipped: today's edition was already accepted for all recipients.")
            return
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
    briefing_date = briefing_today().isoformat()
    result = build_briefing_result_sync(
        openai_mode=openai_mode,
        config=config,
        watchlist_path=args.watchlist,
        history_path=args.history_path,
        # Production email installs the prepared update only after its edition
        # and review rows commit; non-email deliveries retain the legacy path.
        persist_history=args.send and delivery_target not in {"email", "both"} and not args.email_rebuild_today,
        ignore_history=args.ignore_history or args.email_rebuild_today,
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
                rendered_email, watchlist_stories = service.render_newsletter(
                    email_messages,
                    f"Morning Briefing - {briefing_today().isoformat()}",
                    config.enrichment,
                    budget,
                    persist_quotes=False,
                    general_articles=getattr(result, "watchlist_candidates", ()),
                    market_quotes=getattr(getattr(result, "stock_snapshot", None), "quotes", {}),
                    use_openai=openai_mode != "off",
                    persist_watchlist_state=not args.restart_after_gate_failure,
                    read_edgar_state=args.restart_after_gate_failure,
                )
                preview_path = Path("preview.html")
                preview_path.write_text(rendered_email.html, encoding="utf-8")
                print(f"Saved formatted email preview to {preview_path}.")
                plain_text = rendered_email.plain_text
                if args.activate_watchlist_gate:
                    implementation_version = str(args.implementation_version)
                    preflight = ActivationPreflight(
                        implementation_version=implementation_version,
                        entity_map_valid=True,
                        sec_contact_valid=bool(sec_contact_email_from_env()),
                        tests_passed=args.tests_passed,
                        dry_run_version=implementation_version,
                        required_edgar_failures=tuple(
                            story.ticker for story in watchlist_stories if story.official_retrieval_failed
                        ),
                    )
                    activate_gate(preflight, confirmed=args.confirm)
                    service.store.record_activation_preflight(
                        implementation_version,
                        {
                            "entity_map_valid": preflight.entity_map_valid,
                            "sec_contact_valid": preflight.sec_contact_valid,
                            "tests_passed": preflight.tests_passed,
                            "dry_run_version": preflight.dry_run_version,
                            "required_edgar_failures": list(preflight.required_edgar_failures),
                        },
                        preflight.passed,
                    )
                    service.store.activate_gate(implementation_version, confirmed=True)
                if args.restart_after_gate_failure:
                    required_failures = tuple(
                        story.ticker for story in watchlist_stories if story.official_retrieval_failed
                    )
                    service.store.complete_gate_recovery(
                        str(args.implementation_version),
                        succeeded=not required_failures,
                    )
                    if required_failures:
                        raise SystemExit(
                            "Gate A recovery health check failed: required EDGAR failures for "
                            + ", ".join(required_failures)
                        )
            if delivery_target == "both":
                print("===== TELEGRAM =====")
                for index, message in enumerate(messages, start=1):
                    print(f"\n--- MESSAGE {index}/{len(messages)} ---")
                    print(message)
                print("\n===== EMAIL PLAIN TEXT =====")
            print(plain_text, end="")
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
            if args.activate_watchlist_gate:
                print(f"Gate A activated for {args.implementation_version}.")
            if args.restart_after_gate_failure:
                print("Gate A recovery health check passed; a fresh MEASURING window is active.")
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
            header = f"Morning Briefing - {briefing_date}"
            if args.email_rebuild_today:
                header += " [Test resend]"
            edition = (
                service.prepare_parity_edition(email_messages, header, test_revision=args.email_rebuild_today)
                if args.email_parity
                else service.prepare_newsletter_edition(
                    email_messages,
                    header,
                    config.enrichment,
                    budget,
                    test_revision=args.email_rebuild_today,
                    general_articles=getattr(result, "watchlist_candidates", ()),
                    market_quotes=getattr(getattr(result, "stock_snapshot", None), "quotes", {}),
                    use_openai=openai_mode != "off",
                    briefing_date=briefing_date,
                    candidate_records=getattr(result, "candidate_records", ()),
                    history_update=getattr(result, "history_update", None),
                    pipeline_version=config.importance.calibration_version,
                    config_hash=hashlib.sha256(repr(config).encode("utf-8")).hexdigest(),
                    deck_target=config.importance.deck_target,
                    openai_mode=openai_mode,
                    history_path=args.history_path,
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
                    f"Morning Briefing - {briefing_date}",
                    config.enrichment,
                    budget,
                    general_articles=getattr(result, "watchlist_candidates", ()),
                    market_quotes=getattr(getattr(result, "stock_snapshot", None), "quotes", {}),
                    use_openai=openai_mode != "off",
                    briefing_date=briefing_date,
                    candidate_records=getattr(result, "candidate_records", ()),
                    history_update=getattr(result, "history_update", None),
                    pipeline_version=config.importance.calibration_version,
                    config_hash=hashlib.sha256(repr(config).encode("utf-8")).hexdigest(),
                    deck_target=config.importance.deck_target,
                    openai_mode=openai_mode,
                    history_path=args.history_path,
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
    print("Duplicate-event gate")
    for field_name in (
        "duplicate_gate_deck_size",
        "duplicate_gate_eligible_pairs",
        "duplicate_gate_candidate_components",
        "duplicate_gate_clusters_offered",
        "duplicate_gate_components_dropped",
        "duplicate_gate_sets_returned",
        "duplicate_gate_sets_rejected",
        "duplicate_gate_sets_merged",
        "duplicate_gate_clusters_removed",
        "duplicate_gate_cross_category_merges",
    ):
        print(f"{field_name}: {getattr(diagnostics, field_name, 0)}")
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


def _review_benchmark_events(store: EmailStateStore) -> None:
    pending = store.pending_benchmark_events()
    if not pending:
        print("No pending Watchlist benchmark events.")
        return
    for item in pending:
        print(f"\n{item['ticker']} | {item['event_date']} | {item['headline']}")
        print(item["source_url"])
        print(f"Materiality rationale: {item['materiality_rationale']}")
        verdict = input("Verdict [material/not_material/unclear/quit]: ").strip().casefold()
        if verdict == "quit":
            return
        if verdict not in {"material", "not_material", "unclear"}:
            print("Invalid verdict; item left pending.")
            continue
        found: bool | None = None
        if verdict == "material":
            answer = input("Did NewsAgent find this event? [yes/no/unclear]: ").strip().casefold()
            found = True if answer == "yes" else False if answer == "no" else None
        store.review_benchmark_event(int(item["id"]), verdict, found_by_newsagent=found)


def _review_relationships(store: EmailStateStore) -> None:
    pending = store.pending_relationship_reviews()
    if not pending:
        print("No pending Watchlist relationships.")
        return
    for item in pending:
        print(f"\n{item['ticker']} | {item['candidate']} → {item['proposed_relationship']}")
        print(item["evidence_url"])
        print(item["reason"])
        verdict = input("Verdict [accepted/rejected/unclear/quit]: ").strip().casefold()
        if verdict == "quit":
            return
        if verdict not in {"accepted", "rejected", "unclear"}:
            print("Invalid verdict; item left pending.")
            continue
        store.review_relationship(str(item["review_id"]), verdict)


def _review_watchlist_evaluations(store: EmailStateStore) -> None:
    events = store.pending_watchlist_evaluations()
    moves = store.pending_large_move_reviews()
    if not events and not moves:
        print("No pending Watchlist evaluations.")
        return
    definitive = {"DIRECT", "AFFILIATE", "MANAGED_CAPITAL", "UNDERLYING_ASSET"}
    for item in events:
        print(f"\n{item['ticker']} | {item['headline']}")
        if item["canonical_url"]:
            print(item["canonical_url"])
        event_id = str(item["event_id"])
        if not item["story_reviewed"]:
            relevance = input("Story relevance [relevant/irrelevant/unclear/quit]: ").strip().casefold()
            if relevance == "quit":
                return
            if relevance not in {"relevant", "irrelevant", "unclear"}:
                print("Invalid verdict; item left pending.")
                continue
            store.record_adjudication("rendered_story", event_id, relevance)
        if item["relationship_label"] in definitive and not item["relationship_reviewed"]:
            relation = input("Relationship claim [correct/incorrect/unclear]: ").strip().casefold()
            if relation in {"correct", "incorrect", "unclear"}:
                store.record_adjudication("relationship_claim", event_id, relation)
            else:
                print("Invalid relationship verdict; relationship claim left pending.")
    for item in moves:
        print(
            f"\n{item['ticker']} | {item['briefing_date']} | "
            f"move {item['price_move_percent']:+.2f}% | quiet Watchlist row"
        )
        verdict = input("Was a material event missed? [yes/no/unclear/quit]: ").strip().casefold()
        if verdict == "quit":
            return
        mapped = {"yes": "missed", "no": "no_miss", "unclear": "unclear"}.get(verdict)
        if mapped is None:
            print("Invalid verdict; item left pending.")
            continue
        store.record_adjudication("large_move", str(item["subject_id"]), mapped)


def _review_newsletter_evaluations(store: EmailStateStore, scope: str = "all", limit: int = 20) -> None:
    from news_agent.newsletter_review import LABEL_SCHEMA_VERSION
    if scope == "filtered":
        pending = store.pending_newsletter_batch(LABEL_SCHEMA_VERSION)
        if not pending:
            _, days = store.newsletter_pilot_status(LABEL_SCHEMA_VERSION)
            print(f"Filtered review unavailable — pilot {days} of 7 eligible days.")
            return
    elif scope == "sent":
        pending = store.pending_newsletter_evaluations(disposition="selected")
    else:
        pending = store.pending_newsletter_evaluations(disposition="selected")
        filtered = store.pending_newsletter_batch(LABEL_SCHEMA_VERSION)
        if filtered:
            pending = [item for pair in zip(pending, filtered) for item in pair] + pending[len(filtered):] + filtered[len(pending):]
    if not pending:
        print("No pending newsletter evaluations.")
        return
    for item in pending[:limit]:
        print(f"\n{item['briefing_date']} | {item['disposition']} | {item['headline'] or '(purged headline)'}")
        if item["canonical_url"]:
            print(item["canonical_url"])
        if item["delivered_paragraph"]:
            print(item["delivered_paragraph"])
        verdict = input("Verdict [relevant/irrelevant/unclear/deck/details/skip/quit]: ").strip().casefold()
        if verdict == "deck":
            for row in store.pending_newsletter_evaluations(disposition="selected"):
                if row["briefing_date"] == item["briefing_date"]:
                    print(f"{row['category']}: {row['headline']}")
            continue
        if verdict == "details" and item["disposition"] == "filtered":
            print(f"{item['filter_stage']} / {item['filter_reason_code']} ({item['legacy_skip_reason']})")
            continue
        if verdict == "skip":
            continue
        if verdict == "quit":
            return
        if verdict not in {"relevant", "irrelevant", "unclear"}:
            print("Invalid verdict; item left pending.")
            continue
        reason = ""
        if verdict != "unclear":
            reason = input("Reason code: ").strip()
            if not reason:
                print("A reason code is required; item left pending.")
                continue
        note = input("Optional note: ").strip()
        store.record_newsletter_label(
            str(item["candidate_id"]),
            "sent_story" if item["disposition"] == "selected" else "filtered_candidate",
            verdict,
            reason,
            note,
            LABEL_SCHEMA_VERSION,
        )


def _add_newsletter_examples(store: EmailStateStore) -> None:
    while True:
        date = input("Briefing date (or done): ").strip()
        if date == "done": return
        item = {"example_date": date, "headline": input("Headline: ").strip(), "source_url": input("HTTPS URL: ").strip(),
                "publisher": input("Publisher: ").strip(), "expected_category": input("Category (optional): ").strip(),
                "why_it_matters": input("Why it matters: ").strip(), "provenance": input("Provenance: ").strip()}
        try: print(f"Imported {store.import_newsletter_examples([item])} example(s).")
        except ValueError as exc: print(exc)


def _review_newsletter_examples(store: EmailStateStore) -> None:
    from news_agent.newsletter_review import LABEL_SCHEMA_VERSION
    for example in store.pending_newsletter_examples():
        print(f"\n{example['example_date']} | {example['headline']}")
        matches = store.candidates_for_example(example)
        candidate_id = str(matches[0]['candidate_id']) if matches else None
        if candidate_id: print(f"Matched: {matches[0]['headline']}")
        verdict = input("Verdict [relevant/irrelevant/unclear/skip/quit]: ").strip().casefold()
        if verdict == "quit": return
        if verdict == "skip": continue
        if verdict not in {"relevant", "irrelevant", "unclear"}: print("Invalid verdict."); continue
        store.review_newsletter_example(int(example['id']), verdict, candidate_id, LABEL_SCHEMA_VERSION)


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
