from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_PATH = Path(__file__).parents[1] / ".github/workflows/signals.yml"
EXPECTED_CRONS = (
    "17 4 * * *",
    "17 5 * * *",
    "17 16 * * *",
    "17 17 * * *",
)
EXPECTED_ACTIVE_SCHEDULES = {
    "+0300": {"17 4 * * *", "17 16 * * *"},
    "+0200": {"17 5 * * *", "17 17 * * *"},
}


def test_signals_schedule_uses_off_hour_crons_and_matching_dst_gate() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert tuple(re.findall(r'- cron: "([^"]+)"', workflow)) == EXPECTED_CRONS

    case_body = re.search(
        r'case "\$\{kyiv_offset\}:\$\{trigger_schedule\}" in\n(?P<body>.*?)\n\s*\*\)',
        workflow,
        re.DOTALL,
    )
    assert case_body is not None
    active_pairs = set(re.findall(r'"([+-]\d{4}:[^"]+)"', case_body.group("body")))

    expected_pairs = {
        f"{offset}:{schedule}"
        for offset, schedules in EXPECTED_ACTIVE_SCHEDULES.items()
        for schedule in schedules
    }
    assert active_pairs == expected_pairs
