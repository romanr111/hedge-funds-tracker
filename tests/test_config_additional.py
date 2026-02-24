from __future__ import annotations

import json
from pathlib import Path

import pytest

import tracker.config as config_module


def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [
        "SEC_USER_AGENT",
        "SEC_RATE_LIMIT_PER_SEC",
        "MAX_FILING_AGE_DAYS",
        "DB_PATH",
        "MANAGERS_FILE",
        "MANAGERS_JSON",
        "NOTIFIERS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "PIPELINE_TOP_K",
        "PIPELINE_MIN_CONF",
        "PIPELINE_MIN_OOS_QUARTERS",
        "PIPELINE_HOLD_QUARTERS",
        "PIPELINE_POSITION_CAP",
        "PIPELINE_SECTOR_CAP",
        "PIPELINE_ADV20_USD_MIN",
        "PIPELINE_PRICE_MIN",
        "PIPELINE_COST_BPS_PER_SIDE",
        "PIPELINE_REPORT_DIR",
        "SYMBOL_METADATA_FILE",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_load_dotenv_fallback_parses_lines_and_respects_existing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
            ]
        )
    )
    monkeypatch.setenv("FOO", "already-present")
    monkeypatch.delenv("BAR", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    monkeypatch.delenv("BROKEN", raising=False)
    monkeypatch.delenv("EMPTY", raising=False)

    config_module._load_dotenv_fallback(env_path)

    assert config_module.os.environ["FOO"] == "already-present"
    assert config_module.os.environ["BAR"] == "quoted value"
    assert config_module.os.environ["BAZ"] == "double quoted"
    assert config_module.os.environ["BROKEN"] == "unterminated"
    assert config_module.os.environ["EMPTY"] == ""


def test_load_dotenv_fallback_noop_when_file_missing(tmp_path: Path) -> None:
    config_module._load_dotenv_fallback(tmp_path / "missing.env")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ("alpha,beta", ["alpha", "beta"]),
        (" alpha, , beta ,", ["alpha", "beta"]),
    ],
)
def test_split_csv(value: str | None, expected: list[str]) -> None:
    assert config_module._split_csv(value) == expected


def test_resolve_path_uses_default_relative_and_absolute(tmp_path: Path) -> None:
    default = tmp_path / "default.json"
    assert config_module._resolve_path(None, default) == default
    assert config_module._resolve_path("", default) == default
    assert config_module._resolve_path("/tmp/absolute.json", default) == Path("/tmp/absolute.json")
    assert config_module._resolve_path("config/managers.json", default) == config_module.REPO_ROOT / "config/managers.json"


def test_env_int_and_float_validate_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INT_OK", "10")
    monkeypatch.setenv("INT_BAD", "bad")
    monkeypatch.setenv("INT_LOW", "0")
    monkeypatch.setenv("INT_HIGH", "11")
    monkeypatch.setenv("FLOAT_OK", "0.8")
    monkeypatch.setenv("FLOAT_BAD", "bad")
    monkeypatch.setenv("FLOAT_LOW", "-1")
    monkeypatch.setenv("FLOAT_HIGH", "2")

    assert config_module._env_int("INT_OK", 5, minimum=1, maximum=10) == 10
    with pytest.raises(ValueError, match="INT_BAD must be an integer"):
        config_module._env_int("INT_BAD", 5)
    with pytest.raises(ValueError, match="INT_LOW must be >= 1"):
        config_module._env_int("INT_LOW", 5, minimum=1)
    with pytest.raises(ValueError, match="INT_HIGH must be <= 10"):
        config_module._env_int("INT_HIGH", 5, maximum=10)

    assert config_module._env_float("FLOAT_OK", 0.5, minimum=0.0, maximum=1.0) == 0.8
    with pytest.raises(ValueError, match="FLOAT_BAD must be a number"):
        config_module._env_float("FLOAT_BAD", 0.5)
    with pytest.raises(ValueError, match="FLOAT_LOW must be >= 0.0"):
        config_module._env_float("FLOAT_LOW", 0.5, minimum=0.0)
    with pytest.raises(ValueError, match="FLOAT_HIGH must be <= 1.0"):
        config_module._env_float("FLOAT_HIGH", 0.5, maximum=1.0)


def test_load_managers_validates_json_and_file(tmp_path: Path) -> None:
    managers_file = tmp_path / "managers.json"
    managers_file.write_text('[{"name":"Fund A","cik":"0000000001","weight":"1.5"}]')
    parsed = config_module.load_managers(managers_file, None)
    assert len(parsed) == 1
    assert parsed[0].weight == 1.5

    bad_shape_file = tmp_path / "bad-shape.json"
    bad_shape_file.write_text('{"name":"Fund A"}')
    with pytest.raises(ValueError, match="Managers file must contain a JSON array"):
        config_module.load_managers(bad_shape_file, None)

    with pytest.raises(FileNotFoundError, match="Managers file not found"):
        config_module.load_managers(tmp_path / "missing.json", None)

    with pytest.raises(ValueError, match="MANAGERS_JSON must be a JSON array"):
        config_module.load_managers(None, '{"name":"Fund A"}')

    with pytest.raises(ValueError, match="non-empty 'name' and 'cik'"):
        config_module.load_managers(None, '[{"name":" ","cik":"0000000001","weight":1}]')

    with pytest.raises(ValueError, match="invalid 'weight' value"):
        config_module.load_managers(None, '[{"name":"Fund A","cik":"0000000001","weight":"x"}]')


def test_load_config_reads_pipeline_and_notifiers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SEC_USER_AGENT", "Tracker/1.0 (test@example.com)")
    monkeypatch.setenv("SEC_RATE_LIMIT_PER_SEC", "4")
    monkeypatch.setenv("MAX_FILING_AGE_DAYS", "90")
    monkeypatch.setenv("MANAGERS_JSON", '[{"name":"Fund A","cik":"0000000001","weight":2}]')
    monkeypatch.setenv("NOTIFIERS", "telegram, email")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("PIPELINE_TOP_K", "5")
    monkeypatch.setenv("PIPELINE_MIN_CONF", "0.55")
    monkeypatch.setenv("PIPELINE_MIN_OOS_QUARTERS", "9")
    monkeypatch.setenv("PIPELINE_HOLD_QUARTERS", "3")
    monkeypatch.setenv("PIPELINE_POSITION_CAP", "0.10")
    monkeypatch.setenv("PIPELINE_SECTOR_CAP", "0.25")
    monkeypatch.setenv("PIPELINE_ADV20_USD_MIN", "12345")
    monkeypatch.setenv("PIPELINE_PRICE_MIN", "7")
    monkeypatch.setenv("PIPELINE_COST_BPS_PER_SIDE", "12")
    monkeypatch.setenv("PIPELINE_REPORT_DIR", "reports/custom")
    monkeypatch.setenv("SYMBOL_METADATA_FILE", "config/symbols.custom.json")

    resolved = config_module.load_config(db_path=str(tmp_path / "state.sqlite3"), notify_initial=True)

    assert resolved.sec_user_agent.startswith("Tracker/1.0")
    assert resolved.sec_rate_limit_per_sec == 4.0
    assert resolved.max_filing_age_days == 90
    assert resolved.db_path == tmp_path / "state.sqlite3"
    assert [item.name for item in resolved.managers] == ["Fund A"]
    assert resolved.notifiers == ["telegram", "email"]
    assert resolved.telegram_bot_token == "token"
    assert resolved.telegram_chat_id == "chat"
    assert resolved.notify_initial is True
    assert resolved.pipeline.top_k == 5
    assert resolved.pipeline.min_conf == pytest.approx(0.55)
    assert resolved.pipeline.min_oos_quarters == 9
    assert resolved.pipeline.hold_quarters == 3
    assert resolved.pipeline.position_cap == pytest.approx(0.10)
    assert resolved.pipeline.sector_cap == pytest.approx(0.25)
    assert resolved.pipeline.adv20_usd_min == pytest.approx(12345.0)
    assert resolved.pipeline.price_min == pytest.approx(7.0)
    assert resolved.pipeline.cost_bps_per_side == pytest.approx(12.0)
    assert resolved.pipeline.report_dir == config_module.REPO_ROOT / "reports/custom"
    assert resolved.pipeline.symbol_metadata_file == config_module.REPO_ROOT / "config/symbols.custom.json"


@pytest.mark.parametrize(
    ("env_name", "env_value", "expected"),
    [
        ("SEC_USER_AGENT", "", "SEC_USER_AGENT is required"),
        ("SEC_RATE_LIMIT_PER_SEC", "abc", "SEC_RATE_LIMIT_PER_SEC must be a number"),
        ("SEC_RATE_LIMIT_PER_SEC", "11", "SEC_RATE_LIMIT_PER_SEC must be > 0 and <= 10"),
        ("MAX_FILING_AGE_DAYS", "abc", "MAX_FILING_AGE_DAYS must be an integer"),
        ("MAX_FILING_AGE_DAYS", "-1", "MAX_FILING_AGE_DAYS must be >= 0"),
        ("PIPELINE_TOP_K", "0", "PIPELINE_TOP_K must be >= 1"),
        ("PIPELINE_MIN_CONF", "2", "PIPELINE_MIN_CONF must be <= 1.0"),
    ],
)
def test_load_config_validates_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
    expected: str,
) -> None:
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("SEC_USER_AGENT", "Tracker/1.0 (test@example.com)")
    monkeypatch.setenv("MANAGERS_JSON", '[{"name":"Fund A","cik":"0000000001","weight":1}]')
    monkeypatch.setenv(env_name, env_value)

    with pytest.raises(ValueError, match=expected):
        config_module.load_config()


def test_load_managers_accepts_numeric_weight_from_file(tmp_path: Path) -> None:
    managers_file = tmp_path / "managers.json"
    managers_file.write_text(json.dumps([{"name": "Fund A", "cik": "0000000001", "weight": 3}]))
    managers = config_module.load_managers(managers_file, None)
    assert managers[0].weight == 3.0
