from __future__ import annotations

from news_agent.alerts import AlertConfig, apply_alert_cooldown, generate_alerts, save_alert_history
from news_agent.models import MarketMover


def test_generate_alerts_triggers_major_index_move() -> None:
    mover = MarketMover(
        symbol="QQQ",
        name="Nasdaq 100 ETF",
        asset_type="major_index_etf",
        latest_price=97,
        previous_close=100,
        absolute_change=-3,
        percent_change=-3.0,
        move_reason="Stocks fell as rate expectations rose.",
        reason_confidence="medium",
        reason_sources=("Reuters",),
    )

    alerts = generate_alerts([], (mover,), AlertConfig(enabled=True))

    assert alerts[0].headline == "QQQ moves down 3.0%"
    assert alerts[0].confidence == "medium"


def test_alert_cooldown_suppresses_recent_alert(tmp_path) -> None:
    mover = MarketMover(
        symbol="BTC",
        name="Bitcoin",
        asset_type="crypto",
        latest_price=90,
        previous_close=100,
        absolute_change=-10,
        percent_change=-10.0,
        move_reason="Crypto sold off after ETF outflows.",
        reason_confidence="high",
        reason_sources=("Bloomberg",),
    )
    alert = generate_alerts([], (mover,), AlertConfig(enabled=True))[0]
    history_path = tmp_path / "alert_history.json"
    save_alert_history([alert], history_path)

    assert apply_alert_cooldown([alert], history_path, cooldown_minutes=180) == []
