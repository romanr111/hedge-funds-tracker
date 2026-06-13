#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    def _line(parts: list[str]) -> str:
        return " | ".join(part.ljust(widths[idx]) for idx, part in enumerate(parts))

    sep = "-+-".join("-" * width for width in widths)
    lines = [_line(headers), sep]
    lines.extend(_line(row) for row in rows)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print manager_state data in a readable table and key/value format."
    )
    parser.add_argument(
        "--db",
        default="data/signals.sqlite3",
        help="Path to SQLite DB (default: data/signals.sqlite3)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute(
        """
        SELECT
            cik,
            name,
            last_accession,
            last_filing_date,
            last_report_date,
            last_positions_json,
            updated_at
        FROM manager_state
        ORDER BY name
        """
    ).fetchall()

    if not rows:
        print("No records in manager_state.")
        con.close()
        return 0

    table_rows: list[list[str]] = []
    for row in rows:
        positions = (
            json.loads(row["last_positions_json"]) if row["last_positions_json"] else []
        )
        table_rows.append(
            [
                row["cik"] or "",
                row["name"] or "",
                row["last_accession"] or "",
                row["last_filing_date"] or "",
                str(len(positions)),
                row["updated_at"] or "",
            ]
        )

    headers = [
        "cik",
        "name",
        "last_accession",
        "last_filing_date",
        "positions",
        "updated_at",
    ]
    print(_format_table(headers, table_rows))
    print()
    print("Details:")
    for row in rows:
        positions = (
            json.loads(row["last_positions_json"]) if row["last_positions_json"] else []
        )
        print("-" * 60)
        print(f"cik: {row['cik']}")
        print(f"name: {row['name']}")
        print(f"last_accession: {row['last_accession']}")
        print(f"last_filing_date: {row['last_filing_date']}")
        print(f"last_report_date: {row['last_report_date']}")
        print(f"positions_count: {len(positions)}")
        print(f"updated_at: {row['updated_at']}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
