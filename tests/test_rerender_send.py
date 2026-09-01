from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from news_agent.mailer.state import EmailStateStore
from scripts import rerender_send
from scripts.rerender_send import _extract_watchlist_html


def test_extract_watchlist_html_reformats_legacy_section_markup() -> None:
    watchlist = (
        '<section><h2>Watchlist</h2>'
        '<div><strong>AAPL: <span style="color: #d93025;">205.00 (-1.25%)</span> · live</strong>'
        '<p>No verified news today.</p></div>'
        '<div><strong>NVO: <span style="color: #188038;">45.16 (+2.00%)</span> · live</strong>'
        '<h3>Disclosed</h3><ul><li><a href="https://www.sec.gov/example">'
        '6-K accepted 17:23 ET — material filing</a></li></ul></div>'
        '<div><strong>SHOP: 145.71 (+18.18%) · live</strong><h3>Reported</h3>'
        '<p>Shopify reported a material company update. '
        '<a href="https://example.com/shopify">Reuters</a></p>'
        '<p>Relevance: directly about the SHOP issuer. '
        '<a href="https://example.com/relationship">relationship evidence</a></p></div>'
        '<p><em>Watchlist evaluation disabled.</em></p>'
        '</section>'
    )
    stored_html = (
        '<html><body><section><h2>Finance</h2><p>Market news.</p></section>'
        f'{watchlist}<footer>Disclaimer</footer></body></html>'
    )

    rendered = _extract_watchlist_html(stored_html)

    assert rendered.startswith('<div style="padding:0 28px;">')
    assert '<td width="50%"' in rendered
    assert "No verified news today." not in rendered
    assert "AAPL" in rendered
    assert "205.00" in rendered
    assert "NVO" in rendered
    assert "6-K accepted 17:23 ET — material filing" in rendered
    assert 'href="https://www.sec.gov/example"' in rendered
    assert "Shopify reported a material company update." in rendered
    assert 'href="https://example.com/shopify"' in rendered
    assert "Relevance: directly about the SHOP issuer." in rendered
    assert "Watchlist evaluation disabled." in rendered


def test_extract_watchlist_html_keeps_current_styled_div_markup() -> None:
    watchlist = (
        '<div style="padding:0 28px;"><table><tr><td>Watchlist</td></tr></table>'
        '<div><strong>AAPL</strong><p>Reported news.</p></div></div>'
    )
    stored_html = f'<html><body><div>News</div>{watchlist}<div>Footer</div></body></html>'

    assert _extract_watchlist_html(stored_html) == watchlist


def test_refresh_watchlist_disclosures_replaces_generic_labels_and_deduplicates_events(
    tmp_path: Path,
) -> None:
    store = EmailStateStore(tmp_path / "email_state.db")
    full_results_url = "https://www.sec.gov/Archives/edgar/data/353278/000035327826000023/nvo-20260804.htm"
    preliminary_results_url = "https://www.sec.gov/Archives/edgar/data/353278/000117184326005184/exh_991.htm"
    buyback_url = "https://www.sec.gov/Archives/edgar/data/353278/000117184326005172/exh_991.htm"
    documents = (
        (
            "edgar:NVO:0000353278-26-000023",
            "0000353278-26-000023",
            "2026-08-04T21:23:00+00:00",
            full_results_url,
            "Novo Nordisk Q2 2026 financial results. Adjusted sales rose by 7% and "
            "adjusted operating profit rose by 11%. The company raised its 2026 sales "
            "and profit outlook.",
        ),
        (
            "edgar:NVO:0001171843-26-005184",
            "0001171843-26-005184",
            "2026-08-04T17:40:00+00:00",
            preliminary_results_url,
            "Novo Nordisk Q2 2026 financial results. Adjusted sales rose by 7% and "
            "adjusted operating profit rose by 11%. The company raised its 2026 sales "
            "and profit outlook.",
        ),
        (
            "edgar:NVO:0001171843-26-005172",
            "0001171843-26-005172",
            "2026-08-04T14:39:00+00:00",
            buyback_url,
            "Novo Nordisk provided an update on its share buyback program.",
        ),
    )
    with store.connect() as connection:
        for document_id, accession, accepted_at, url, body in documents:
            connection.execute(
                """
                INSERT INTO watchlist_documents(
                    document_id, ticker, source_id, accession, form_type, accepted_at,
                    first_observed_at, canonical_url, content_hash, body, metadata_json, created_at
                ) VALUES (?, 'NVO', 'edgar', ?, '6-K', ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    document_id,
                    accession,
                    accepted_at,
                    accepted_at,
                    url,
                    body,
                    json.dumps({"description": "6-K", "items": []}),
                    accepted_at,
                ),
            )

    watchlist = (
        '<div style="padding:0 28px;"><table><tr><td>Watchlist</td></tr></table>'
        '<div><strong>NVO 45.16 +2.00%</strong><p>Disclosed</p>'
        f'<p><a href="{full_results_url}">6-K accepted 17:23 ET — material filing</a> '
        '<span style="font-size:11px; color:#9AA0A6;">6-K · 17:23 ET</span></p>'
        f'<p><a href="{preliminary_results_url}">6-K accepted 13:40 ET — material filing</a> '
        '<span style="font-size:11px; color:#9AA0A6;">6-K · 13:40 ET</span></p>'
        f'<p><a href="{buyback_url}">Also: 6-K — 2026-08-04</a> '
        '<span style="font-size:11px; color:#9AA0A6;">6-K · 2026-08-04</span></p>'
        '</div></div>'
    )

    refreshed = rerender_send._refresh_watchlist_disclosures(watchlist, store)

    assert refreshed.count(
        "Novo Nordisk raised its 2026 sales and profit outlook after adjusted Q2 sales rose 7% "
        "and adjusted profit rose 11%."
    ) == 1
    assert refreshed.count("Novo Nordisk updated its share buyback program.") == 1
    assert "material filing" not in refreshed
    assert "accepted 13:40" not in refreshed
    assert preliminary_results_url not in refreshed
    assert "6-K · 17:23 ET" in refreshed
    assert "6-K · 10:39 ET" in refreshed
