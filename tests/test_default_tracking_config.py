from __future__ import annotations

import json
from pathlib import Path

from signals.config import load_managers


def test_default_symbol_map_covers_top_spy_holding_identifiers() -> None:
    symbol_map = json.loads(Path("config/cusip_tickers.json").read_text())

    assert len(symbol_map) >= 400
    assert symbol_map["11135F101"] == "AVGO"
    assert symbol_map["038222105"] == "AMAT"


def test_default_managers_include_sec_13f_situational_awareness_lp() -> None:
    managers = load_managers(Path("config/managers.json"), None)

    assert any(
        manager.name == "Situational Awareness LP" and manager.cik == "0002045724"
        for manager in managers
    )


def test_default_managers_weight_appaloosa_as_high_signal() -> None:
    managers = load_managers(Path("config/managers.json"), None)

    appaloosa = next(manager for manager in managers if manager.name == "Appaloosa Management LP")
    assert appaloosa.weight == 1.5


def test_default_pershing_source_transition_uses_parent_from_2026q2() -> None:
    managers = load_managers(Path("config/managers.json"), None)

    pershing = next(manager for manager in managers if manager.cik == "0001336528")
    assert pershing.source_ciks_for_quarter("2026Q1") == ("0001336528",)
    assert pershing.source_ciks_for_quarter("2026Q2") == ("0002026053",)
