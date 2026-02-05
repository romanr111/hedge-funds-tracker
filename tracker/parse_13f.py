from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _find_child(node: ET.Element, tag: str) -> ET.Element | None:
    for child in node:
        if _strip_ns(child.tag) == tag:
            return child
    return None


def _find_text(node: ET.Element, tag: str) -> str | None:
    child = _find_child(node, tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _find_nested_text(node: ET.Element, parent_tag: str, child_tag: str) -> str | None:
    parent = _find_child(node, parent_tag)
    if parent is None:
        return None
    return _find_text(parent, child_tag)


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def parse_infotable(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    positions: list[dict[str, Any]] = []

    for info in root.iter():
        if _strip_ns(info.tag) != "infoTable":
            continue

        name = _find_text(info, "nameOfIssuer")
        title = _find_text(info, "titleOfClass")
        cusip = _find_text(info, "cusip")
        value = _parse_int(_find_text(info, "value"))
        shares = _parse_int(_find_nested_text(info, "shrsOrPrnAmt", "sshPrnamt"))
        shares_type = _find_nested_text(info, "shrsOrPrnAmt", "sshPrnamtType")
        put_call = _find_text(info, "putCall")
        investment_discretion = _find_text(info, "investmentDiscretion")
        other_manager = _find_text(info, "otherManager")
        voting_sole = _parse_int(_find_nested_text(info, "votingAuthority", "Sole"))
        voting_shared = _parse_int(_find_nested_text(info, "votingAuthority", "Shared"))
        voting_none = _parse_int(_find_nested_text(info, "votingAuthority", "None"))

        if not cusip and not name:
            continue

        positions.append(
            {
                "name": name,
                "title": title,
                "cusip": cusip,
                "value": value,
                "shares": shares,
                "shares_type": shares_type,
                "put_call": put_call,
                "investment_discretion": investment_discretion,
                "other_manager": other_manager,
                "voting_sole": voting_sole,
                "voting_shared": voting_shared,
                "voting_none": voting_none,
            }
        )

    return positions
