from __future__ import annotations

import argparse
from pathlib import Path

from news_agent.config import DEFAULT_CONFIG_PATH, load_config
from news_agent.env import load_dotenv
from news_agent.notifications.base import NotificationError
from news_agent.notifications.factory import send_briefing_messages, send_telegram_test_message
from news_agent.pipeline import OpenAIMode, build_briefings_sync


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Build and send six morning news briefing messages.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Print messages instead of sending them.")
    parser.add_argument("--send", action="store_true", help="Send messages through the configured delivery channel.")
    parser.add_argument("--test-telegram", action="store_true", help="Send one Telegram test message and exit.")
    parser.add_argument("--channel", choices=("telegram", "sms"), help="Override BRIEFING_DELIVERY_CHANNEL.")
    parser.add_argument(
        "--openai-mode",
        choices=("full", "polish", "off"),
        default="full",
        help=(
            "Choose OpenAI usage: full sends ranked article context, polish rewrites the "
            "local fallback draft, off uses only deterministic fallback summaries."
        ),
    )
    parser.add_argument(
        "--no-openai",
        action="store_true",
        help="Alias for --openai-mode off.",
    )
    args = parser.parse_args(argv)

    if args.test_telegram:
        try:
            recipient_count = send_telegram_test_message()
        except NotificationError as exc:
            raise SystemExit(f"Telegram test failed: {exc}") from exc
        print(f"Telegram test message sent to {recipient_count} recipient(s).")
        return

    if not args.dry_run and not args.send:
        parser.error("Choose --dry-run, --send, or --test-telegram.")

    config = load_config(args.config)
    openai_mode: OpenAIMode = "off" if args.no_openai else args.openai_mode
    briefings = build_briefings_sync(openai_mode=openai_mode, config=config)
    messages = [briefing.to_message() for briefing in briefings]

    if args.dry_run:
        for index, message in enumerate(messages, start=1):
            divider = f"\n--- MESSAGE {index}/6 ---"
            print(divider)
            print(message)
        return

    try:
        sent_count = send_briefing_messages(messages, channel=args.channel)
    except NotificationError as exc:
        raise SystemExit(f"Send failed: {exc}") from exc
    print(f"Sent {sent_count} message(s).")


if __name__ == "__main__":
    main()
