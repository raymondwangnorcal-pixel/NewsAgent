from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from news_agent import cli
from news_agent.config import DEFAULT_CONFIG_PATH
from news_agent.models import Article, BriefingParagraph, BriefingSection, PipelineDiagnostics


def sample_briefings() -> list[BriefingSection]:
    return [
        BriefingSection(
            category="business_tech",
            label="Business + Tech",
            paragraphs=(
                BriefingParagraph(
                    story_id="ai-funding",
                    category="business_tech",
                    paragraph="An AI startup raised a large funding round, a sign investors "
                    "still see room to grow in the space.",
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


def test_cli_quality_report_skips_news_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "build_briefing_result_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline should not run")),
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    today = date.today().isoformat()
    (data_dir / f"quality_gate_rejections_{today}.json").write_text(
        json.dumps(
            [
                {
                    "title": "Content-free teaser headline",
                    "source": "Wire Service",
                    "url": "https://example.com/teaser",
                    "reason": "empty_summary",
                }
            ]
        ),
        encoding="utf-8",
    )

    cli.main(["--quality-report"])

    output = capsys.readouterr().out
    assert "Quality gate rejections by source" in output
    assert "1 | Wire Service" in output


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
    assert "===== TEXT 1/1 =====" in output
    assert "BUSINESS + TECH" in output
    assert "Total messages: 1" in output
    assert "An AI startup raised a large funding round" in output


def test_cli_email_dry_run_prints_requested_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    diagnostics = PipelineDiagnostics(
        duplicate_gate_deck_size=10,
        duplicate_gate_sets_merged=1,
    )
    monkeypatch.setattr(
        cli,
        "build_briefing_result_sync",
        lambda **_kwargs: SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
            diagnostics=diagnostics,
            openai_budget=None,
        ),
    )

    class FakeEmailService:
        def render_newsletter(self, *_args, **_kwargs):
            return SimpleNamespace(plain_text="EMAIL PREVIEW\n", html="<html><body>EMAIL PREVIEW</body></html>"), []

    monkeypatch.setattr(cli, "EmailService", FakeEmailService)

    cli.main(["--dry-run", "--to", "email", "--show-diagnostics", "--no-openai"])

    output = capsys.readouterr().out
    assert "EMAIL PREVIEW" in output
    assert "duplicate_gate_deck_size: 10" in output
    assert "duplicate_gate_sets_merged: 1" in output


def test_cli_email_dry_run_writes_formatted_html_preview_with_watchlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "build_briefing_result_sync",
        lambda **_kwargs: SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
            diagnostics=None,
            openai_budget=None,
        ),
    )
    formatted_html = (
        "<html><body><div>In This Briefing</div>"
        '<div id="watchlist">Watchlist</div><div>Business + Tech</div></body></html>'
    )

    class FakeEmailService:
        def render_newsletter(self, *_args: object, **_kwargs: object):
            return SimpleNamespace(plain_text="EMAIL PREVIEW\n", html=formatted_html), []

    monkeypatch.setattr(cli, "EmailService", FakeEmailService)

    cli.main(["--dry-run", "--to", "email", "--no-openai"])

    preview = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert preview == formatted_html
    assert preview.index("In This Briefing") < preview.index("Watchlist") < preview.index("Business + Tech")
    assert "Saved formatted email preview to preview.html." in capsys.readouterr().out


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


def test_scheduled_email_before_threshold_skips_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "scheduled_email_is_due", lambda: False)
    monkeypatch.setattr(
        cli,
        "build_briefing_result_sync",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline should not run")),
    )

    cli.main(["--send", "--to", "email", "--email-parity", "--scheduled", "--no-openai"])

    assert "8:20–8:35 AM" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--email-rebuild-today"],
        ["--send", "--to", "email", "--email-rebuild-today"],
        ["--send", "--to", "email", "--confirm", "--email-rebuild-today", "--scheduled"],
        ["--send", "--to", "email", "--confirm", "--email-rebuild-today", "--email-resend", "1"],
    ],
)
def test_email_rebuild_today_requires_explicit_manual_email_send(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(argv)

    assert "--email-rebuild-today requires --send --to email --confirm" in capsys.readouterr().err


def test_email_rebuild_today_uses_fresh_build_without_persisting_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    calls: dict[str, object] = {}

    def fake_build(**kwargs: object) -> object:
        calls.update(kwargs)
        return SimpleNamespace(
            briefings=sample_briefings(), skipped_stories=[], skipped_log_path=Path("skipped.json"), source_debug_lines=()
        )

    class FakeEmailService:
        def render_parity(self, *_args: object) -> object:
            return SimpleNamespace(plain_text="preview")

        def prepare_parity_edition(self, _messages: object, header: str, *, test_revision: bool) -> object:
            calls["header"] = header
            calls["test_revision"] = test_revision
            return object()

        def send_edition(self, _edition: object) -> list[object]:
            return [SimpleNamespace(state="smtp_accepted")]

    monkeypatch.setattr(cli, "build_briefing_result_sync", fake_build)
    monkeypatch.setattr(cli, "email_settings_from_env", lambda: object())
    monkeypatch.setattr(cli, "EmailService", FakeEmailService)

    cli.main(["--send", "--to", "email", "--email-parity", "--email-rebuild-today", "--confirm", "--no-openai"])

    assert calls["ignore_history"] is True
    assert calls["persist_history"] is False
    assert calls["test_revision"] is True
    assert "[Test resend]" in str(calls["header"])
    assert "Sent email to 1 recipient(s)." in capsys.readouterr().out


def test_cli_openai_mode_choices_reject_polish(capsys: pytest.CaptureFixture[str]) -> None:
    # "polish" mode was removed with the old fragmented-schema pipeline --
    # only full/off remain.
    with pytest.raises(SystemExit):
        cli.main(["--dry-run", "--openai-mode", "polish"])
    assert "invalid choice: 'polish'" in capsys.readouterr().err


def test_no_openai_drafting_alias_selects_classify_only(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    calls: dict[str, object] = {}

    def fake_build(**kwargs: object) -> object:
        calls.update(kwargs)
        return SimpleNamespace(briefings=sample_briefings(), skipped_stories=[], skipped_log_path=Path("x"), source_debug_lines=())

    monkeypatch.setattr(cli, "build_briefing_result_sync", fake_build)

    cli.main(["--dry-run", "--no-openai-drafting"])

    assert calls["openai_mode"] == "classify-only"


def test_conflicting_openai_aliases_are_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--dry-run", "--no-openai", "--no-openai-drafting"])

    assert "cannot be used together" in capsys.readouterr().err


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

    cli.main(["--dry-run", "--no-openai", "--format", "telegram"])

    output = capsys.readouterr().out
    assert "--- MESSAGE 1/1 ---" in output
    assert "An AI startup raised a large funding round" in output
    assert "What happened:" not in output
    assert "• " not in output


def test_cli_show_skipped_prints_quality_gate_rejections(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rejected_article = Article(
        title="Content-free teaser headline",
        url="https://example.com/teaser",
        source="Wire Service",
        published_at=datetime.now(timezone.utc),
        summary="",
    )
    monkeypatch.setattr(
        cli,
        "build_briefing_result_sync",
        lambda **kwargs: SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
            quality_gate_rejections=((rejected_article, "empty_summary"),),
            quality_gate_log_path=Path("quality_gate_rejections.json"),
        ),
    )

    cli.main(["--dry-run", "--no-openai", "--show-skipped"])

    output = capsys.readouterr().out
    assert "Quality gate rejections: 1" in output
    assert "empty_summary" in output
    assert "Content-free teaser headline" in output
    assert "Wire Service" in output


def test_cli_prints_importance_phase_diagnostics(capsys: pytest.CaptureFixture[str]) -> None:
    cli.print_diagnostics(PipelineDiagnostics(
        drafting_input_tokens=1_000,
        drafting_output_tokens=200,
        drafting_cost_usd=0.0055,
        drafting_budget_exhausted=False,
        openai_input_tokens=4_000,
        openai_output_tokens=800,
        openai_cost_usd=0.022,
        openai_cost_by_stage={"classification": 0.0165, "drafting": 0.0055},
        openai_budget_exhausted=False,
        floor_selected_by_category={"culture": 2},
        remainder_selected_by_category={"culture": 3},
        big_day_selected_by_category={"culture": 1},
        deck_target=25,
        deck_selected=25,
        duplicate_gate_deck_size=25,
        duplicate_gate_eligible_pairs=4,
        duplicate_gate_candidate_components=2,
        duplicate_gate_clusters_offered=4,
        duplicate_gate_components_dropped=0,
        duplicate_gate_sets_returned=1,
        duplicate_gate_sets_rejected=0,
        duplicate_gate_sets_merged=1,
        duplicate_gate_clusters_removed=1,
        duplicate_gate_cross_category_merges=0,
    ))

    output = capsys.readouterr().out
    assert "floor_selected_by_category: {'culture': 2}" in output
    assert "remainder_selected_by_category: {'culture': 3}" in output
    assert "big_day_selected_by_category: {'culture': 1}" in output
    assert "Drafting input tokens: 1000" in output
    assert "Drafting output tokens: 200" in output
    assert "Drafting cost: $0.005500" in output
    assert "Drafting budget exhausted: False" in output
    assert "Total OpenAI input tokens: 4000" in output
    assert "Total OpenAI output tokens: 800" in output
    assert "Total OpenAI cost: $0.022000" in output
    assert "OpenAI cost by stage: {'classification': 0.0165, 'drafting': 0.0055}" in output
    assert "Overall OpenAI budget exhausted: False" in output
    assert "duplicate_gate_deck_size: 25" in output
    assert "duplicate_gate_eligible_pairs: 4" in output
    assert "duplicate_gate_candidate_components: 2" in output
    assert "duplicate_gate_clusters_offered: 4" in output
    assert "duplicate_gate_components_dropped: 0" in output
    assert "duplicate_gate_sets_returned: 1" in output
    assert "duplicate_gate_sets_rejected: 0" in output
    assert "duplicate_gate_sets_merged: 1" in output
    assert "duplicate_gate_clusters_removed: 1" in output
    assert "duplicate_gate_cross_category_merges: 0" in output
    assert "Deck: 25/25 (full)" in output


def test_no_compress_flag_forces_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    def fake_build(**kwargs: object) -> object:
        seen["config"] = kwargs["config"]
        return SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
        )

    monkeypatch.setenv("BRIEFING_COMPRESSION", "true")
    monkeypatch.setattr(cli, "build_briefing_result_sync", fake_build)

    cli.main(["--dry-run", "--no-openai", "--no-compress"])

    assert getattr(seen["config"], "compression").enabled is False


def test_compress_flag_forces_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "sources.toml"
    config_path.write_text(
        DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def fake_build(**kwargs: object) -> object:
        seen["config"] = kwargs["config"]
        return SimpleNamespace(
            briefings=sample_briefings(),
            skipped_stories=[],
            skipped_log_path=Path("skipped.json"),
            source_debug_lines=(),
        )

    monkeypatch.setenv("BRIEFING_COMPRESSION", "false")
    monkeypatch.setattr(cli, "build_briefing_result_sync", fake_build)

    cli.main(["--dry-run", "--no-openai", "--compress", "--config", str(config_path)])

    assert getattr(seen["config"], "compression").enabled is True


def test_compression_flags_are_mutually_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--dry-run", "--compress", "--no-compress"])

    assert "not allowed with argument" in capsys.readouterr().err


def test_cli_rejects_unpriced_openai_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "sources.toml"
    config_path.write_text(
        DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace(
            "input_cost_usd_per_million_tokens = 2.5\n"
            "output_cost_usd_per_million_tokens = 15.0",
            "input_cost_usd_per_million_tokens = 0.0\n"
            "output_cost_usd_per_million_tokens = 0.0",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        cli.main(["--dry-run", "--config", str(config_path)])

    assert "requires positive input and output token prices" in capsys.readouterr().err
