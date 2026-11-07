from __future__ import annotations

import pytest

from market_intel.domain.models import MODULE_KEYS
from market_intel.domain.verdicts import (
    format_value_uk,
    module_forward_context_uk,
    module_verdict_uk,
    uk_days_word,
)

_TAIL = "Система не прогнозує напрям: це історичні асоціації, а не таймінг."

_STATE_COUNTS = {
    "valuation": 4,
    "sentiment": 4,
    "technique": 3,
    "macro": 4,
    "credit": 4,
}


def test_every_module_state_has_distinct_verdict_with_disclaimer() -> None:
    for module in MODULE_KEYS:
        for index in range(1, _STATE_COUNTS[module] + 1):
            verdict = module_verdict_uk(module, f"composite_{module}{index}", None, 3)
            assert verdict, (module, index)
            assert verdict.endswith(_TAIL)


def test_verdicts_differ_between_states_of_one_module() -> None:
    seen = set()
    for index in range(1, 5):
        seen.add(module_verdict_uk("valuation", f"composite_valuation{index}", None, 4))
    assert len(seen) == 4


def test_stale_state_verdict_degrades_honestly() -> None:
    for module in MODULE_KEYS:
        verdict = module_verdict_uk(module, "stale", None, 0)
        assert "недоступний" in verdict
        assert verdict.endswith(_TAIL)


def test_small_sample_cautions_for_one_and_two_inputs() -> None:
    v1 = module_verdict_uk("credit", "composite_credit2", 55.0, 1)
    assert "1 вході" in v1
    v2 = module_verdict_uk("credit", "composite_credit2", 55.0, 2)
    assert "2 входах" in v2
    three = module_verdict_uk("credit", "composite_credit2", 55.0, 3)
    assert "вход" not in three[: three.index(_TAIL)]  # no small-sample note at 3+
    # caution appears AFTER the base sentence but BEFORE the disclaimer tail
    assert v1.index("вход") < v1.index(_TAIL)


def test_unknown_state_key_degrades_without_raising() -> None:
    verdict = module_verdict_uk("valuation", "composite_mystery", None, 3)
    assert verdict.endswith(_TAIL)
    assert "невизначений" in verdict or "інтерпретація відсутня" in verdict


def test_format_value_uk_comma_decimal_and_units() -> None:
    assert format_value_uk(38.79, "") == "38,79"
    assert format_value_uk(0.5, "%") == "0,50%"
    assert format_value_uk(-0.06, "%") == "-0,06%"
    assert format_value_uk(None, "%") == "недоступно"
    assert format_value_uk(None, "") == "недоступно"
    assert format_value_uk(1234567.891, "") == "1 234 567,89"
    assert format_value_uk(2.0, "bp") == "2,00 bp"
    assert format_value_uk(5835.9, "$ млрд") == "5 835,90 $ млрд"


def test_module_forward_context_known_and_fallback() -> None:
    for module in MODULE_KEYS:
        context = module_forward_context_uk(module)
        assert context and "форвардн" in context
    fallback = module_forward_context_uk("custom_module")
    assert fallback and fallback.endswith(".")


@pytest.mark.parametrize(
    ("days", "word"),
    [(1, "день"), (2, "дні"), (3, "дні"), (4, "дні"), (5, "днів"), (8, "днів"),
     (11, "днів"), (12, "днів"), (21, "день"), (22, "дні"), (62, "дні"),
     (35, "днів"), (100, "днів")],
)
def test_uk_days_word_plurals(days: int, word: str) -> None:
    assert uk_days_word(days) == word
