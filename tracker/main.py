from __future__ import annotations

import argparse
from datetime import datetime, timezone

from tracker.config import load_config
from tracker.diff import build_diff_message, diff_positions
from tracker.notifiers import build_notifiers
from tracker.parse_13f import parse_infotable
from tracker.sec_client import SecClient
from tracker.storage import StateStore


TARGET_FORMS = {"13F-HR", "13F-HR/A"}


def _extract_filings(submissions: dict) -> list[dict]:
    recent = submissions.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])

    count = min(len(accessions), len(forms), len(filing_dates), len(report_dates))
    filings = []
    for idx in range(count):
        if forms[idx] not in TARGET_FORMS:
            continue
        filings.append(
            {
                "accession": accessions[idx],
                "form": forms[idx],
                "filing_date": filing_dates[idx],
                "report_date": report_dates[idx],
            }
        )

    filings.sort(key=lambda item: item["filing_date"], reverse=True)
    return filings


def _format_subject(manager_name: str, filing_date: str | None) -> str:
    if filing_date:
        return f"{manager_name} 13F update ({filing_date})"
    return f"{manager_name} 13F update"


def _send_notifications(notifiers, subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def process_manager(manager, store, client, notifiers, *, notify_initial: bool, dry_run: bool) -> None:
    print(f"Checking {manager.name} ({manager.cik})...")
    try:
        submissions = client.get_submissions(manager.cik)
    except Exception as exc:
        print(f"  Failed to fetch submissions: {exc}")
        return
    filings = _extract_filings(submissions)
    if not filings:
        print("  No 13F filings found.")
        return

    state = store.get_state(manager.cik)
    last_accession = state.last_accession if state else None
    new_filings = []

    for filing in filings:
        if last_accession and filing["accession"] == last_accession:
            break
        new_filings.append(filing)

    if not new_filings:
        print("  No new filings.")
        return

    previous_positions = state.last_positions if state else None

    # Process oldest-to-newest so diffs are deterministic.
    for filing in reversed(new_filings):
        try:
            info_url = client.find_information_table_url(manager.cik, filing["accession"])
            xml_text = client.get_text(info_url)
            positions = parse_infotable(xml_text)
        except Exception as exc:
            print(f"  Skipping accession {filing['accession']} due to fetch/parse error: {exc}")
            continue

        if not previous_positions:
            if notify_initial:
                subject = _format_subject(manager.name, filing["filing_date"])
                body = (
                    f"Baseline stored for {manager.name} ({manager.cik}).\n"
                    f"Accession {filing['accession']} filed {filing['filing_date']}."
                )
                if not dry_run:
                    _send_notifications(notifiers, subject, body)
            else:
                print("  Baseline stored (notifications suppressed).")
        else:
            diff = diff_positions(previous_positions, positions)
            if any(
                [
                    diff.new_positions,
                    diff.exited_positions,
                    diff.increased_positions,
                    diff.decreased_positions,
                ]
            ):
                subject = _format_subject(manager.name, filing["filing_date"])
                summary = build_diff_message(diff)
                body = (
                    f"Accession {filing['accession']} filed {filing['filing_date']}.\n"
                    f"Report date {filing['report_date']}.\n\n"
                    f"{summary}"
                )
                if not dry_run:
                    _send_notifications(notifiers, subject, body)
            else:
                print("  No position-level changes detected.")

        if not dry_run:
            store.upsert_state(
                cik=manager.cik,
                name=manager.name,
                last_accession=filing["accession"],
                last_filing_date=filing["filing_date"],
                last_report_date=filing["report_date"],
                last_positions=positions,
            )
        previous_positions = positions


def main() -> int:
    parser = argparse.ArgumentParser(description="Track 13F filings and send notifications.")
    parser.add_argument("--db-path", help="Path to SQLite DB")
    parser.add_argument("--managers-file", help="Path to managers.json")
    parser.add_argument("--notifiers", help="Comma-separated list (telegram,email)")
    parser.add_argument("--notify-initial", action="store_true", help="Notify on initial baseline set")
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Send a test notification and exit (without SEC checks).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send notifications or write state")
    args = parser.parse_args()

    if args.test_notification and args.dry_run:
        print("Cannot combine --test-notification with --dry-run.")
        return 2

    config = load_config(
        db_path=args.db_path,
        managers_file=args.managers_file,
        notifiers=args.notifiers,
        notify_initial=args.notify_initial,
    )

    if not config.notifiers:
        print("No notifiers configured. Running without notifications.")

    if args.dry_run and not args.test_notification:
        notifiers = []
    elif config.notifiers:
        notifiers = build_notifiers(
            config.notifiers,
            telegram_bot_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_user=config.smtp_user,
            smtp_pass=config.smtp_pass,
            email_from=config.email_from,
            email_to=config.email_to,
        )
    else:
        notifiers = []

    if args.test_notification:
        if not notifiers:
            print("No notifiers configured for test notification.")
            return 1
        subject = "13F Tracker test notification"
        body = f"Test notification sent at {datetime.now(timezone.utc).isoformat()}."
        _send_notifications(notifiers, subject, body)
        print("Test notification sent.")
        return 0

    min_interval = 1.0 / config.sec_rate_limit_per_sec
    client = SecClient(user_agent=config.sec_user_agent, min_interval_seconds=min_interval)
    store = StateStore(config.db_path)

    for manager in config.managers:
        process_manager(
            manager,
            store,
            client,
            notifiers,
            notify_initial=config.notify_initial,
            dry_run=args.dry_run,
        )

    store.close()
    print(f"Done at {datetime.now(timezone.utc).isoformat()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
