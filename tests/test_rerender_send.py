from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
