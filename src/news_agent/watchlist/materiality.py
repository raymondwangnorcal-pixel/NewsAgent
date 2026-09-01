from __future__ import annotations

from news_agent.watchlist.models import Filing


MATERIAL_8K_ITEMS = frozenset(
    {
        "1.01", "1.02", "1.03",
        "2.01", "2.02", "2.03", "2.04", "2.05", "2.06",
        "3.01", "4.02",
        "5.01", "5.02", "5.03", "5.04", "5.05", "5.06", "5.07",
        "8.01",
    }
)
CONTENT_REVIEW_8K_ITEMS = frozenset({"7.01"})
MATERIAL_PERIODIC_FORMS = frozenset({"10-Q", "10-K", "20-F", "40-F"})
ETHEREUM_MATERIAL_TERMS = (
    "protocol upgrade", "hard fork", "staking", "exchange-traded", "etf approval",
    "regulation", "regulatory", "security incident", "exploit", "hack",
)
SIX_K_MATERIAL_TERMS = (
    "annual results", "interim results", "quarterly results", "financial results",
    "earnings", "guidance", "outlook", "acquisition", "merger", "disposition", "divestiture",
    "regulatory approval", "investigation", "litigation", "chief executive", "ceo",
    "dividend", "share repurchase", "capital raise", "offering", "restructuring",
)
EDITORIAL_MATERIAL_TERMS = SIX_K_MATERIAL_TERMS + (
    "product launch", "product recall", "data breach", "cyberattack", "bankruptcy",
    "delisting", "antitrust", "settlement", "profit warning", "forecast",
)


def filing_is_material(filing: Filing) -> bool | None:
    form = filing.form.removesuffix("/A")
    if form == "8-K":
        if MATERIAL_8K_ITEMS.intersection(filing.items):
            return True
        if CONTENT_REVIEW_8K_ITEMS.intersection(filing.items):
            return None
        return False
    if form in MATERIAL_PERIODIC_FORMS:
        return True
    if form == "6-K":
        return None
    return False


def ethereum_item_is_material(text: str, *, move_explained: bool = False) -> bool:
    lowered = text.casefold()
    return move_explained or any(term in lowered for term in ETHEREUM_MATERIAL_TERMS)


def six_k_metadata_is_material(text: str) -> bool:
    """Fail-closed official-metadata fallback for an otherwise indeterminate 6-K."""
    lowered = text.casefold()
    return any(term in lowered for term in SIX_K_MATERIAL_TERMS)


def official_content_is_material(text: str) -> bool:
    """Evaluate official filing text when form metadata alone is indeterminate."""
    return six_k_metadata_is_material(text)


def six_k_content_is_material(text: str) -> bool:
    """Apply the configured material-event categories to extracted 6-K text."""
    return official_content_is_material(text)


def editorial_metadata_is_material(text: str) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in EDITORIAL_MATERIAL_TERMS)
