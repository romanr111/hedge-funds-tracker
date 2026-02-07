from __future__ import annotations


class TrackerError(Exception):
    """Base exception for tracker domain and application flows."""


class SubmissionsFetchError(TrackerError):
    """Failed to fetch or decode manager submissions feed."""


class InformationTableLookupError(TrackerError):
    """Failed to locate or fetch 13F information table metadata/content."""


class InformationTableNotFoundError(InformationTableLookupError):
    """Information table cannot be found for a filing accession."""


class InvalidInformationTableError(TrackerError):
    """Information table XML payload is invalid or malformed."""


class StateStoreError(TrackerError):
    """State repository read/write operation failed."""


class NotificationError(TrackerError):
    """Notification send operation failed."""
