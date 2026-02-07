from __future__ import annotations

import logging
from io import StringIO

from tracker.infrastructure.logging.json_logger import JsonFormatter, configure_logging


def test_configure_logging_normalizes_existing_root_handler() -> None:
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    try:
        stream = StringIO()
        existing_handler = logging.StreamHandler(stream)
        root.handlers = [existing_handler]
        root.setLevel(logging.WARNING)

        configure_logging(level=logging.INFO)

        assert root.handlers == [existing_handler]
        assert isinstance(existing_handler.formatter, JsonFormatter)
        assert any(type(existing_filter).__name__ == "_ContextFilter" for existing_filter in existing_handler.filters)
        assert existing_handler.level == logging.INFO
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)


def test_configure_logging_reuses_handler_without_duplicate_filters() -> None:
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    try:
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        root.handlers = [handler]

        configure_logging(level=logging.INFO)
        filter_count_before = len(handler.filters)
        configure_logging(level=logging.DEBUG)

        assert len(handler.filters) == filter_count_before
        assert handler.level == logging.DEBUG
        assert isinstance(handler.formatter, JsonFormatter)
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)
