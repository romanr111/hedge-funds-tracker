from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/"


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
        last_error: Exception | None = None
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
            raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error
        raise RuntimeError(f"Failed to fetch {url}")

    def get_json(self, url: str) -> dict[str, Any]:
        response = self._request("GET", url)
        return response.json()

    def get_text(self, url: str) -> str:
        response = self._request("GET", url)
        return response.text

    def get_submissions(self, cik: str) -> dict[str, Any]:
        url = SUBMISSIONS_URL.format(cik=normalize_cik(cik))
        return self.get_json(url)

    def get_filing_index(self, cik: str, accession: str) -> list[FilingIndexEntry]:
        accession_dir = accession_no_dashes(accession)
        index_url = urljoin(EDGAR_ARCHIVES_BASE, f"{cik_dir(cik)}/{accession_dir}/index.json")
        data = self.get_json(index_url)
        items = data.get("directory", {}).get("item", [])
        entries: list[FilingIndexEntry] = []
        for item in items:
            name = item.get("name")
            if not name:
                continue
            entries.append(FilingIndexEntry(name=name, type=item.get("type")))
        return entries

    def find_information_table_url(self, cik: str, accession: str) -> str:
        accession_dir = accession_no_dashes(accession)
        base_url = urljoin(EDGAR_ARCHIVES_BASE, f"{cik_dir(cik)}/{accession_dir}/")

        try:
            entries = self.get_filing_index(cik, accession)
            candidates = []
            for entry in entries:
                name_lower = entry.name.lower()
                type_lower = (entry.type or "").lower()
                if not name_lower.endswith(".xml"):
                    continue
                if "information table" in type_lower or "infotable" in name_lower or "informationtable" in name_lower:
                    candidates.append(entry.name)
            if not candidates:
                # Fallback: any XML with "info" in the filename
                candidates = [entry.name for entry in entries if entry.name.lower().endswith(".xml") and "info" in entry.name.lower()]
            if candidates:
                return urljoin(base_url, candidates[0])
        except requests.HTTPError:
            pass

        index_html_url = urljoin(base_url, f"{accession}-index.html")
        html = self.get_text(index_html_url)
        match = re.search(r'href="([^"]+?info[^"]+?\.xml)"', html, flags=re.IGNORECASE)
        if match:
            return urljoin(base_url, match.group(1))

        match = re.search(r'href="([^"]+?\.xml)"', html, flags=re.IGNORECASE)
        if match:
            return urljoin(base_url, match.group(1))

        raise ValueError(f"Could not locate information table XML for accession {accession}.")
