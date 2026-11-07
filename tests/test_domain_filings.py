from __future__ import annotations

from signals.domain.filings import extract_13f_notice_other_manager_ciks


def test_extract_notice_other_manager_ciks_handles_namespaced_cover_page() -> None:
    xml = """<?xml version="1.0"?>
    <edgarSubmission xmlns="urn:sec:edgar:thirteenfnotice">
        <form13FInfo>
            <otherManagersInfo>
                <otherManager>
                    <cik>2026053</cik>
                </otherManager>
            </otherManagersInfo>
        </form13FInfo>
    </edgarSubmission>"""

    assert extract_13f_notice_other_manager_ciks(xml) == ("0002026053",)


def test_extract_notice_other_manager_ciks_sorts_and_dedupes_candidates() -> None:
    xml = """<?xml version="1.0"?>
    <edgarSubmission>
        <otherManager sequence="2"><cik>0002026053</cik></otherManager>
        <otherManager sequence="1"><cik>CIK 0000000005</cik></otherManager>
        <otherManager sequence="3"><cik>0000000005</cik></otherManager>
    </edgarSubmission>"""

    assert extract_13f_notice_other_manager_ciks(xml) == ("0000000005", "0002026053")


def test_extract_notice_other_manager_ciks_returns_empty_for_malformed_xml() -> None:
    assert extract_13f_notice_other_manager_ciks("<edgarSubmission><otherManager><cik>2026053") == ()


def test_extract_notice_other_manager_ciks_returns_empty_without_other_managers() -> None:
    xml = """<?xml version="1.0"?>
    <edgarSubmission xmlns="urn:sec:edgar:thirteenfnotice">
        <form13FInfo>
            <infoTables />
        </form13FInfo>
    </edgarSubmission>"""

    assert extract_13f_notice_other_manager_ciks(xml) == ()
