from __future__ import annotations

from pathlib import Path

import pytest

import selectel_floating_ip as sfip
from selectel_floating_ip import (
    env_flag,
    env_float,
    env_int,
    load_env_file,
    normalize_chat_id,
)


def test_env_int_reads_value(monkeypatch):
    monkeypatch.setenv("X_TEST_INT", "42")
    assert env_int("X_TEST_INT", 0) == 42


def test_env_int_uses_default(monkeypatch):
    monkeypatch.delenv("X_TEST_INT", raising=False)
    assert env_int("X_TEST_INT", 7) == 7


def test_env_int_invalid_raises_systemexit(monkeypatch):
    monkeypatch.setenv("X_TEST_INT", "not-a-number")
    with pytest.raises(SystemExit):
        env_int("X_TEST_INT", 0)


def test_env_float_reads_value(monkeypatch):
    monkeypatch.setenv("X_TEST_FLOAT", "1.5")
    assert env_float("X_TEST_FLOAT", 0.0) == pytest.approx(1.5)


def test_env_float_invalid_raises_systemexit(monkeypatch):
    monkeypatch.setenv("X_TEST_FLOAT", "abc")
    with pytest.raises(SystemExit):
        env_float("X_TEST_FLOAT", 0.0)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Y", "on"])
def test_env_flag_truthy(monkeypatch, value):
    monkeypatch.setenv("X_TEST_FLAG", value)
    assert env_flag("X_TEST_FLAG") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_env_flag_falsy(monkeypatch, value):
    monkeypatch.setenv("X_TEST_FLAG", value)
    assert env_flag("X_TEST_FLAG") is False


def test_env_flag_default(monkeypatch):
    monkeypatch.delenv("X_TEST_FLAG", raising=False)
    assert env_flag("X_TEST_FLAG", default=True) is True
    assert env_flag("X_TEST_FLAG", default=False) is False


def test_normalize_chat_id():
    assert normalize_chat_id(123) == "123"
    assert normalize_chat_id("  -100123  ") == "-100123"
    assert normalize_chat_id(None) == ""
    assert normalize_chat_id("") == ""


def test_load_env_file_parses_quotes_and_skips_comments(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "PLAIN=value\n"
        "QUOTED=\"with spaces\"\n"
        "SINGLE='also-spaces'\n"
        "EMPTY=\n"
        "MALFORMED_LINE_WITHOUT_EQUALS\n"
        "PRESET=should-not-override\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sfip, "ENV_PATH", env_file)
    for key in ("PLAIN", "QUOTED", "SINGLE", "EMPTY", "PRESET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PRESET", "from-environment")

    load_env_file()

    import os

    assert os.environ["PLAIN"] == "value"
    assert os.environ["QUOTED"] == "with spaces"
    assert os.environ["SINGLE"] == "also-spaces"
    assert os.environ["EMPTY"] == ""
    assert os.environ["PRESET"] == "from-environment"


def test_load_env_file_no_file_is_noop(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sfip, "ENV_PATH", tmp_path / "missing.env")
    load_env_file()
