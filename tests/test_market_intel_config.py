from __future__ import annotations

from pathlib import Path

import pytest

import market_intel.config as config_module


MARKET_INTEL_ENV_KEYS = [
    "MARKET_INTEL_DB_PATH",
    "SHEETS_SPREADSHEET_ID",
    "SHEETS_FOLDER_ID",
    "MARKET_INTEL_FRED_INTERVAL",
    "MARKET_INTEL_YAHOO_INTERVAL",
    "MARKET_INTEL_HTTP_INTERVAL",
    "EXPORT_JSON_GDRIVE_FOLDER_ID",
]


def _clear_market_intel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in MARKET_INTEL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_config_defaults() -> None:
    config = config_module.load_config()

    assert config.db_path == config_module.REPO_ROOT / "data" / "local" / "market_intel.sqlite3"
    assert config.sheets_spreadsheet_id == ""
    assert config.sheets_folder_id == ""
    assert config.fred_interval_seconds == 1.0
    assert config.yahoo_interval_seconds == 1.5
    assert config.http_interval_seconds == 1.0


def test_load_config_reads_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_market_intel_env(monkeypatch)
    monkeypatch.setenv("MARKET_INTEL_DB_PATH", str(tmp_path / "mi.sqlite3"))
    monkeypatch.setenv("SHEETS_SPREADSHEET_ID", "  sheet-123 ")
    monkeypatch.setenv("SHEETS_FOLDER_ID", "folder-42")
    monkeypatch.setenv("MARKET_INTEL_FRED_INTERVAL", "0.5")
    monkeypatch.setenv("MARKET_INTEL_YAHOO_INTERVAL", "2")
    monkeypatch.setenv("MARKET_INTEL_HTTP_INTERVAL", "0.2")

    config = config_module.load_config()

    assert config.db_path == tmp_path / "mi.sqlite3"
    assert config.sheets_spreadsheet_id == "sheet-123"
    assert config.sheets_folder_id == "folder-42"
    assert config.fred_interval_seconds == 0.5
    assert config.yahoo_interval_seconds == 2.0
    assert config.http_interval_seconds == 0.2


def test_load_config_db_path_param_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_market_intel_env(monkeypatch)
    monkeypatch.setenv("MARKET_INTEL_DB_PATH", str(tmp_path / "env.sqlite3"))

    config = config_module.load_config(db_path=str(tmp_path / "param.sqlite3"))

    assert config.db_path == tmp_path / "param.sqlite3"


def test_load_config_sheets_folder_falls_back_to_gdrive_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_market_intel_env(monkeypatch)
    monkeypatch.setenv("EXPORT_JSON_GDRIVE_FOLDER_ID", "gdrive-folder-1")

    config = config_module.load_config()

    assert config.sheets_folder_id == "gdrive-folder-1"

    monkeypatch.setenv("SHEETS_FOLDER_ID", "explicit-folder")
    assert config_module.load_config().sheets_folder_id == "explicit-folder"


@pytest.mark.parametrize(
    ("env_name", "env_value", "expected"),
    [
        ("MARKET_INTEL_FRED_INTERVAL", "abc", "MARKET_INTEL_FRED_INTERVAL must be a number"),
        ("MARKET_INTEL_FRED_INTERVAL", "0.1", "MARKET_INTEL_FRED_INTERVAL must be >= 0.2 and <= 10.0"),
        ("MARKET_INTEL_FRED_INTERVAL", "11", "MARKET_INTEL_FRED_INTERVAL must be >= 0.2 and <= 10.0"),
        ("MARKET_INTEL_YAHOO_INTERVAL", "abc", "MARKET_INTEL_YAHOO_INTERVAL must be a number"),
        ("MARKET_INTEL_YAHOO_INTERVAL", "0.19", "MARKET_INTEL_YAHOO_INTERVAL must be >= 0.2 and <= 60.0"),
        ("MARKET_INTEL_YAHOO_INTERVAL", "61", "MARKET_INTEL_YAHOO_INTERVAL must be >= 0.2 and <= 60.0"),
        ("MARKET_INTEL_HTTP_INTERVAL", "abc", "MARKET_INTEL_HTTP_INTERVAL must be a number"),
        ("MARKET_INTEL_HTTP_INTERVAL", "0.05", "MARKET_INTEL_HTTP_INTERVAL must be >= 0.2 and <= 60.0"),
        ("MARKET_INTEL_HTTP_INTERVAL", "100", "MARKET_INTEL_HTTP_INTERVAL must be >= 0.2 and <= 60.0"),
    ],
)
def test_load_config_validates_interval_envs(
    monkeypatch: pytest.MonkeyPatch, env_name: str, env_value: str, expected: str
) -> None:
    _clear_market_intel_env(monkeypatch)
    monkeypatch.setenv(env_name, env_value)

    with pytest.raises(ValueError, match=expected):
        config_module.load_config()


def test_load_dotenv_fallback_parses_lines_and_respects_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "export FOO=bar",
                "BAR='quoted value'",
                'BAZ="double quoted"',
                "BROKEN='unterminated",
                "NOVALUE",
                " =skip",
                "EMPTY=",
                "export MI_DB=data/local/market_intel.sqlite3",
            ]
        )
    )
    monkeypatch.setenv("FOO", "already-present")
    for key in ("BAR", "BAZ", "BROKEN", "EMPTY", "MI_DB"):
        monkeypatch.delenv(key, raising=False)

    config_module._load_dotenv_fallback(env_path)

    assert config_module.os.environ["FOO"] == "already-present"
    assert config_module.os.environ["BAR"] == "quoted value"
    assert config_module.os.environ["BAZ"] == "double quoted"
    assert config_module.os.environ["BROKEN"] == "unterminated"
    assert config_module.os.environ["EMPTY"] == ""
    assert config_module.os.environ["MI_DB"] == "data/local/market_intel.sqlite3"


def test_load_dotenv_fallback_noop_when_file_missing(tmp_path: Path) -> None:
    config_module._load_dotenv_fallback(tmp_path / "missing.env")
