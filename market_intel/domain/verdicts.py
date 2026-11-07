"""Ukrainian verdict sentences and value formatting for gauges.

Verdicts are keyed by (module, state) where state is a composite band key
('composite_<module><index>') or 'stale'. Every verdict carries a timing
disclaimer: the system describes historical associations and never predicts
direction (report secs. 0.5 and 12).
"""
from __future__ import annotations

from typing import Mapping

from market_intel.domain.models import MODULE_TITLES_UK

_VERDICTS_UK: Mapping[tuple[str, str], str] = {
    # --- valuation ---------------------------------------------------------
    ("valuation", "1"): (
        "Валютація дешева за довгими мірилками: історично такі рівні асоціювалися з вищими 10-річними реальними доходностями."
    ),
    ("valuation", "2"): (
        "Валютація справедлива за довгими мірилками: очікування помірні, запас проти альтернатив невеликий."
    ),
    ("valuation", "3"): (
        "Валютація дорога за довгими мірилками: довгострокова очікувана доходність стиснута."
    ),
    ("valuation", "4"): (
        "Валютація дуже дорога за довгими мірилками: історично це стискало очікувану 10-річну дохідність, але не давало робочого таймінгу виходу."
    ),
    # --- sentiment ---------------------------------------------------------
    ("sentiment", "1"): (
        "Настрої спокійні: паніки і ейфорії немає, класичні деплойні тригери не активовані."
    ),
    ("sentiment", "2"): (
        "Настрої нейтральні: опитування й позиціонування не дають сильного сигналу в жоден бік."
    ),
    ("sentiment", "3"): (
        "Настрої обережні: страх і напруження накопичуються - історична зона можливих вікон деплою, перевірте кредитний шар."
    ),
    ("sentiment", "4"): (
        "Настрої ейфоричні: леверидж і жадібність на високих рівнях - історичний маркер вразливості, не сигналу на вихід."
    ),
    # --- technique ---------------------------------------------------------
    ("technique", "1"): (
        "Техніка перепродана: моментум у зоні капітуляції, історично 12-місячні перспективи були хорошими за спокійного кредиту."
    ),
    ("technique", "2"): (
        "Техніка нейтральна: без перепроданості й без перегріву таймінг-тригерів немає."
    ),
    ("technique", "3"): (
        "Техніка перекуплена: ринок розтягнутий вгору - це не вихід-сигнал, але запас до середніх малий."
    ),
    # --- macro -------------------------------------------------------------
    ("macro", "1"): (
        "Макро і ліквідність сприятливі: фінансові умови м'які, крива і ринок праці без тривожних нахилів."
    ),
    ("macro", "2"): (
        "Макро і ліквідність нейтральні: змішані сигнали без чіткого нахилу."
    ),
    ("macro", "3"): (
        "Макро і ліквідність рестриктивні: ставки й фінансові умови тиснуть - тримайте фокус на кредитному шарі."
    ),
    ("macro", "4"): (
        "Макро і ліквідність дуже рестриктивні: історично такі конфігурації супроводжувалися підвищеним ризиком просадок."
    ),
    # --- credit ------------------------------------------------------------
    ("credit", "1"): (
        "Кредит спокійний: спреди вузькі, фандинг-стрес низький - системний гейт відкритий."
    ),
    ("credit", "2"): (
        "Кредит помірний: спреди починають ширше - саме цей шар відрізняє звичайну корекцію від системного падіння."
    ),
    ("credit", "3"): (
        "Кредит підвищений: розширення спредів і/або фандинг-тиск - ризик-нарощування варто заморозити."
    ),
    ("credit", "4"): (
        "Кредит високий: стрес у кредиті - історично головний маркер системних падінь."
    ),
}

def _module_title(module: str) -> str:
    return MODULE_TITLES_UK.get(module, module)


_STALE_VERDICTS_UK: Mapping[str, str] = {
    module: f"Композит «{_module_title(module)}» недоступний: свіжих даних недостатньо для чесної оцінки."
    for module in ("valuation", "sentiment", "technique", "macro", "credit")
}

_TIMING_TAIL_UK = (
    " Система не прогнозує напрям: це історичні асоціації, а не таймінг."
)

MODULE_FORWARD_CONTEXT_UK: Mapping[str, str] = {
    "valuation": (
        "Валютація задає бюджет очікувань і просадок на 5-10+ років; конкретні історичні розподіли - "
        "у шарі «умови -> форвардна дохідність»."
    ),
    "sentiment": (
        "Настрої інформують тижні-місяці; історичні наслідки страху й ейфорії - у таблиці "
        "«умови -> форвардна дохідність»."
    ),
    "technique": (
        "Технічні умови дають таймінг-віконця тижнів-місяців; див. рядки RSI/ширини у таблиці "
        "форвардних доходностей."
    ),
    "macro": (
        "Макро-умови працюють у горизонті місяців-років (крива - 6-24 міс.); див. рядки curve/claims/NFCI "
        "у таблиці форвардних доходностей."
    ),
    "credit": (
        "Кредит - головний системний гейт; історичні наслідки стресу в кредиті показані рядками HY/claims/NFCI "
        "у таблиці форвардних доходностей."
    ),
}
""": Forward-looking context sentence per module (honest horizons, no direction claims)."""


def module_forward_context_uk(module_key: str) -> str:
    """Forward-looking context sentence for ``module_key`` (fallback honest)."""
    return MODULE_FORWARD_CONTEXT_UK.get(
        module_key,
        "Історичні асоціації для цього модуля зібрані в таблиці форвардних доходностей.",
    )


def module_verdict_uk(module_key: str, state_key: str, score: float | None, n_inputs: int) -> str:
    """One-sentence Ukrainian verdict for a module state, with disclaimer tail.

    ``state_key`` is the composite band key (e.g. 'composite_valuation2') or
    'stale'. Unknown combinations degrade to a neutral honest sentence rather
    than raising. n_inputs mentions small-sample caution when < 3.
    """
    tail = _timing_tail(n_inputs)
    if state_key == "stale":
        base = _STALE_VERDICTS_UK.get(module_key, "Композит недоступний: даних недостатньо.")
        return base + tail
    index = _state_index(state_key)
    if index is None:
        base = f"Стан модуля «{_module_title(module_key)}» невизначений."
        return base + tail
    base = _VERDICTS_UK.get((module_key, index))
    if base is None:
        base = f"Стан модуля «{_module_title(module_key)}» оцінено, але інтерпретація відсутня."
    return base + tail


def _timing_tail(n_inputs: int) -> str:
    """Small-sample caution (when < 3 inputs) plus the standing disclaimer."""
    if n_inputs == 1:
        return " Оцінка спиралася лише на 1 вході - знижена надійність." + _TIMING_TAIL_UK
    if n_inputs == 2:
        return " Оцінка спиралася лише на 2 входах - знижена надійність." + _TIMING_TAIL_UK
    return _TIMING_TAIL_UK


def _state_index(state_key: str) -> str | None:
    """'composite_valuation3' -> '3'; None when the key carries no index."""
    last_char = state_key[-1:] if state_key else ""
    if last_char.isdigit():
        return last_char
    return None


def format_value_uk(value: float | None, unit: str) -> str:
    """Human value with comma decimal mark; None -> 'недоступно'.

    '38.79' -> '38,79'; thousands get thin spaces; '%' sticks to the number,
    other units are appended after a space ('% ВВП' style units pass through).
    """
    if value is None:
        return "недоступно"
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    if not unit:
        return text
    if unit == "%":
        return f"{text}%"
    return f"{text} {unit}"


def uk_days_word(days: int) -> str:
    """Ukrainian plural for 'днів': 3 -> 'дні', 8 -> 'днів', 35 -> 'днів'."""
    if days % 100 in (11, 12, 13, 14):
        return "днів"
    remainder = days % 10
    if remainder == 1:
        return "день"
    if remainder in (2, 3, 4):
        return "дні"
    return "днів"
