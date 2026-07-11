from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from news_agent import cli
from news_agent.models import BriefingItem, BriefingText


def sample_briefings() -> list[BriefingText]:
    return [
        BriefingText(
            category="business_tech",
            title="1/5 Business and technology",
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
        "build_briefing_result_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline should not run")),
    )

    def fake_test_message() -> int:
        called["telegram"] = True
        return 2

    monkeypatch.setattr(cli, "send_telegram_test_message", fake_test_message)

    cli.main(["--test-telegram"])

    assert called["telegram"] is True
    assert "Telegram test message sent to 2 recipient(s)." in capsys.readouterr().out


def test_cli_dry_run_prints_messages(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "build_briefing_result_sync",
        lambda **kwargs: SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
        ),
    )

    cli.main(["--dry-run", "--no-openai"])

    output = capsys.readouterr().out
    assert "TEXT 1/1: BUSINESS + TECH" in output
    assert "Total messages: 1" in output
    assert "AI startup raises funding" in output


def test_cli_send_no_openai_uses_configured_sender(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    calls: dict[str, object] = {}

    def fake_build(**kwargs: object) -> object:
        calls["openai_mode"] = kwargs["openai_mode"]
        calls["persist_history"] = kwargs["persist_history"]
        return SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
        )

    def fake_send(messages: list[str], channel: str | None = None, header: str | None = None) -> int:
        calls["messages"] = messages
        calls["channel"] = channel
        return len(messages) + 1

    monkeypatch.setattr(cli, "build_briefing_result_sync", fake_build)
    monkeypatch.setattr(cli, "send_briefing_messages", fake_send)

    cli.main(["--send", "--no-openai", "--channel", "telegram"])

    assert calls["openai_mode"] == "off"
    assert calls["persist_history"] is True
    assert calls["channel"] == "telegram"
    assert str(calls["messages"][0]).startswith("🧠 BUSINESS + TECH")
    assert "Sent 2 message(s)." in capsys.readouterr().out


def test_cli_openai_mode_polish(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    calls: dict[str, object] = {}

    def fake_build(**kwargs: object) -> object:
        calls["openai_mode"] = kwargs["openai_mode"]
        return SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
        )

    monkeypatch.setattr(cli, "build_briefing_result_sync", fake_build)

    cli.main(["--dry-run", "--openai-mode", "polish"])

    assert calls["openai_mode"] == "polish"
    assert "AI startup raises funding" in capsys.readouterr().out


def test_cli_dry_run_can_print_telegram_format(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "build_briefing_result_sync",
        lambda **kwargs: SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
        ),
    )

    cli.main(["--dry-run", "--no-openai", "--format", "telegram", "--brief"])

    output = capsys.readouterr().out
    assert "--- MESSAGE 1/1 ---" in output
    assert "• AI startup raises funding — It may shape the AI market." in output
    assert "What happened:" not in output
