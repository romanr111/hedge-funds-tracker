from __future__ import annotations


class SignalsError(Exception):
    """Base exception for signals domain and application flows."""


class SubmissionsFetchError(SignalsError):
    """Failed to fetch or decode manager submissions feed."""


class InformationTableLookupError(SignalsError):
    """Failed to locate or fetch 13F information table metadata/content."""


class InformationTableNotFoundError(InformationTableLookupError):
    """Information table cannot be found for a filing accession."""


class InvalidInformationTableError(SignalsError):
    """Information table XML payload is invalid or malformed."""


class StateStoreError(SignalsError):
    """State repository read/write operation failed."""


class NotificationError(SignalsError):
    """Notification send operation failed."""
