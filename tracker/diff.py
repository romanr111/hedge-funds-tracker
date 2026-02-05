from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def position_key(position: dict[str, Any]) -> str:
    cusip = (position.get("cusip") or "").strip()
    if cusip:
        return cusip
    name = (position.get("name") or "").strip()
    title = (position.get("title") or "").strip()
    put_call = (position.get("put_call") or "").strip()
    return f"{name}|{title}|{put_call}".strip("|")


def _primary_metric(position: dict[str, Any]) -> int | None:
    value = position.get("value")
    if isinstance(value, int):
        return value
    shares = position.get("shares")
    if isinstance(shares, int):
        return shares
    return None


@dataclass
class DiffResult:
    new_positions: list[dict[str, Any]]
    exited_positions: list[dict[str, Any]]
    increased_positions: list[dict[str, Any]]
    decreased_positions: list[dict[str, Any]]


def diff_positions(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> DiffResult:
    previous_map = {position_key(pos): pos for pos in previous}
    current_map = {position_key(pos): pos for pos in current}

    new_positions = [current_map[key] for key in current_map.keys() - previous_map.keys()]
    exited_positions = [previous_map[key] for key in previous_map.keys() - current_map.keys()]

    increased_positions: list[dict[str, Any]] = []
    decreased_positions: list[dict[str, Any]] = []

    for key in current_map.keys() & previous_map.keys():
        curr = current_map[key]
        prev = previous_map[key]
        curr_metric = _primary_metric(curr)
        prev_metric = _primary_metric(prev)
        if curr_metric is None or prev_metric is None:
            continue
        if curr_metric > prev_metric:
            increased_positions.append(curr)
        elif curr_metric < prev_metric:
            decreased_positions.append(curr)

    return DiffResult(
        new_positions=new_positions,
        exited_positions=exited_positions,
        increased_positions=increased_positions,
        decreased_positions=decreased_positions,
    )


def summarize_position(position: dict[str, Any]) -> str:
    name = position.get("name") or "Unknown"
    title = position.get("title") or ""
    cusip = position.get("cusip") or ""
    value = position.get("value")
    shares = position.get("shares")

    parts = [name]
    if title:
        parts.append(title)
    if cusip:
        parts.append(f"CUSIP {cusip}")
    if value is not None:
        parts.append(f"value ${value:,}k")
    if shares is not None:
        parts.append(f"shares {shares:,}")
    return " | ".join(parts)


def build_diff_message(result: DiffResult, *, max_lines: int = 15) -> str:
    lines: list[str] = []

    def add_section(title: str, positions: list[dict[str, Any]]) -> None:
        if not positions:
            return
        lines.append(f"{title} ({len(positions)}):")
        for position in positions[: max_lines]:
            lines.append(f"- {summarize_position(position)}")

    add_section("New positions", result.new_positions)
    add_section("Exited positions", result.exited_positions)
    add_section("Increased positions", result.increased_positions)
    add_section("Decreased positions", result.decreased_positions)

    if not lines:
        return "No position-level changes detected."

    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["- ...truncated"]

    return "\n".join(lines)
