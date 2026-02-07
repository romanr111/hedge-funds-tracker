from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urljoin

import requests

from tracker.domain.exceptions import (
    InformationTableLookupError,
    InformationTableNotFoundError,
    SubmissionsFetchError,
)


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/"
INFO_TABLE_ROOT_RE = re.compile(r"<\s*(?:\w+:)?informationtable\b", flags=re.IGNORECASE)
INFO_TABLE_ENTRY_RE = re.compile(r"<\s*(?:\w+:)?infotable\b", flags=re.IGNORECASE)


def normalize_cik(cik: str) -> str:
    digits = "".join(ch for ch in cik if ch.isdigit())
    return digits.zfill(10)


def cik_dir(cik: str) -> str:
    return str(int(normalize_cik(cik)))


def accession_no_dashes(accession: str) -> str:
    return accession.replace("-", "")


@dataclass
class FilingIndexEntry:
    name: str
    type: str | None


class SecClient:
    def __init__(self, *, user_agent: str, min_interval_seconds: float = 0.2) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._min_interval = min_interval_seconds
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _request(self, method: str, url: str, max_retries: int = 3) -> requests.Response:
        last_response: requests.Response | None = None
        last_error: requests.RequestException | None = None
        for attempt in range(1, max_retries + 1):
            try:
                self._throttle()
                response = self._session.request(method, url, timeout=(10, 60))
                self._last_request_time = time.time()
                last_response = response

                if response.status_code in {429, 503} and attempt < max_retries:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        time.sleep(int(retry_after))
                    else:
                        time.sleep(attempt)
                    continue

                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(attempt)
                    continue
                break

        if last_response is not None:
            last_response.raise_for_status()
        if last_error is not None:
            raise last_error
        raise requests.RequestException(f"Failed to fetch {url}")

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self._request("GET", url)
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise requests.RequestException(f"Failed to decode JSON from {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise requests.RequestException(f"Expected JSON object from {url}")
        return cast(dict[str, Any], payload)

    def get_text(self, url: str) -> str:
        try:
            response = self._request("GET", url)
        except requests.RequestException as exc:
            raise InformationTableLookupError(f"Failed to fetch text from {url}: {exc}") from exc
        return response.text

    def get_submissions(self, cik: str) -> dict[str, Any]:
        url = SUBMISSIONS_URL.format(cik=normalize_cik(cik))
        try:
            return self._get_json(url)
        except requests.RequestException as exc:
            raise SubmissionsFetchError(f"Failed to fetch submissions for CIK {cik}: {exc}") from exc

    def get_filing_index(self, cik: str, accession: str) -> list[FilingIndexEntry]:
        accession_dir = accession_no_dashes(accession)
        index_url = urljoin(EDGAR_ARCHIVES_BASE, f"{cik_dir(cik)}/{accession_dir}/index.json")
        try:
            data = self._get_json(index_url)
        except requests.RequestException as exc:
            raise InformationTableLookupError(
                f"Failed to fetch filing index for accession {accession}: {exc}"
            ) from exc
        directory = data.get("directory", {})
        if not isinstance(directory, dict):
            raise InformationTableLookupError(f"Invalid filing index format for accession {accession}")
        items_raw = directory.get("item", [])
        if not isinstance(items_raw, list):
            raise InformationTableLookupError(f"Invalid filing index entries for accession {accession}")
        entries: list[FilingIndexEntry] = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            type_field = item.get("type")
            entries.append(FilingIndexEntry(name=name, type=type_field if isinstance(type_field, str) else None))
        return entries

    def find_information_table_url(self, cik: str, accession: str) -> str:
        accession_dir = accession_no_dashes(accession)
        base_url = urljoin(EDGAR_ARCHIVES_BASE, f"{cik_dir(cik)}/{accession_dir}/")

        def looks_like_information_table_xml(text: str) -> bool:
            return bool(INFO_TABLE_ROOT_RE.search(text) and INFO_TABLE_ENTRY_RE.search(text))

        def candidate_rank(name: str) -> tuple[int, str]:
            lowered = name.lower()
            if "infotable" in lowered or "informationtable" in lowered:
                return (0, lowered)
            if "primary_doc" in lowered:
                return (2, lowered)
            return (1, lowered)

        def find_valid_candidate(candidate_names: list[str]) -> str | None:
            for name in sorted(candidate_names, key=candidate_rank):
                candidate_url = urljoin(base_url, name)
                try:
                    text = self.get_text(candidate_url)
                except InformationTableLookupError:
                    continue
                if looks_like_information_table_xml(text):
                    return candidate_url
            return None

        try:
            entries = self.get_filing_index(cik, accession)
            xml_names = [entry.name for entry in entries if entry.name.lower().endswith(".xml")]
            if xml_names:
                candidate_url = find_valid_candidate(xml_names)
                if candidate_url:
                    return candidate_url
        except InformationTableLookupError:
            pass

        index_html_url = urljoin(base_url, f"{accession}-index.html")
        html = self.get_text(index_html_url)
        info_matches = re.findall(r'href="([^"]*?info[^"]*?\.xml)"', html, flags=re.IGNORECASE)
        xml_matches = re.findall(r'href="([^"]+?\.xml)"', html, flags=re.IGNORECASE)
        # Keep discovery order while de-duplicating.
        candidates = list(dict.fromkeys(info_matches + xml_matches))
        if candidates:
            candidate_url = find_valid_candidate(candidates)
            if candidate_url:
                return candidate_url

        raise InformationTableNotFoundError(f"Could not locate information table XML for accession {accession}.")
