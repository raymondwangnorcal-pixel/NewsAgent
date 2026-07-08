from __future__ import annotations

import pytest

from news_agent import cli
from news_agent.models import BriefingItem, BriefingText


def sample_briefings() -> list[BriefingText]:
    return [
        BriefingText(
            category="business_tech",
            title="1/6 Business and technology",
            items=(
                BriefingItem(
                    headline="AI startup raises funding",
                    summary="A startup raised a large round.",
                    why_it_matters="It may shape the AI market.",
                    next_watch="Watch hiring and customer growth.",
                    sources=("Reuters",),
                ),
            ),
        )
    ]


def test_cli_test_telegram_skips_news_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    called = {"telegram": False}
    monkeypatch.setattr(
        cli,
        "build_briefings_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline should not run")),
    )

    def fake_test_message() -> None:
        called["telegram"] = True

    monkeypatch.setattr(cli, "send_telegram_test_message", fake_test_message)

    cli.main(["--test-telegram"])

    assert called["telegram"] is True
    assert "Telegram test message sent." in capsys.readouterr().out


def test_cli_dry_run_prints_messages(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "build_briefings_sync", lambda use_openai, config: sample_briefings())

    cli.main(["--dry-run", "--no-openai"])

    output = capsys.readouterr().out
    assert "--- MESSAGE 1/6 ---" in output
    assert "AI startup raises funding" in output


def test_cli_send_no_openai_uses_configured_sender(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    calls: dict[str, object] = {}

    def fake_build(use_openai: bool, config: object) -> list[BriefingText]:
        calls["use_openai"] = use_openai
        return sample_briefings()

    def fake_send(messages: list[str], channel: str | None = None) -> int:
        calls["messages"] = messages
        calls["channel"] = channel
        return len(messages) + 1

    monkeypatch.setattr(cli, "build_briefings_sync", fake_build)
    monkeypatch.setattr(cli, "send_briefing_messages", fake_send)

    cli.main(["--send", "--no-openai", "--channel", "telegram"])

    assert calls["use_openai"] is False
    assert calls["channel"] == "telegram"
    assert "Sent 2 message(s)." in capsys.readouterr().out
