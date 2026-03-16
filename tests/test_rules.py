"""Tests for sync.rules — config loading, validation, and defaults."""

from __future__ import annotations

import copy
import json
import tempfile

import pytest

from sync.rules import apply_defaults, get_mapping, load_config, validate_config


@pytest.fixture()
def valid_config() -> dict:
    return {
        "version": 1,
        "hub": {
            "provider": "google",
            "account": "personal@gmail.com",
            "calendarId": "primary",
        },
        "providers": {
            "google": {"accounts": ["personal@gmail.com"], "gogBinary": "gog"},
        },
        "mappings": [
            {
                "name": "test-inbound",
                "type": "full-detail",
                "source": {
                    "provider": "google",
                    "account": "personal@gmail.com",
                    "calendarId": "work",
                },
                "target": {
                    "provider": "google",
                    "account": "personal@gmail.com",
                    "calendarId": "primary",
                },
                "lookaheadDays": 30,
            }
        ],
        "scheduling": {
            "businessHours": {"start": "07:00", "end": "21:00", "tz": "America/New_York"},
            "businessIntervalMinutes": 3,
            "offHoursIntervalMinutes": 30,
        },
        "safety": {"maxChangesPerRun": 50, "maxEventsPerMapping": 500, "dryRun": False},
        "retry": {"maxRetries": 3, "backoffSeconds": [2, 4, 8]},
        "notifications": {"method": "stdout"},
        "state": {"file": "/tmp/test-state.json"},
    }


def test_load_valid_config(valid_config: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(valid_config, f)
        f.flush()
        loaded = load_config(f.name)
    assert loaded["version"] == 1


def test_validate_valid_config(valid_config: dict) -> None:
    errors = validate_config(valid_config)
    assert errors == []


def test_validate_missing_hub(valid_config: dict) -> None:
    cfg = copy.deepcopy(valid_config)
    del cfg["hub"]
    errors = validate_config(cfg)
    assert any("hub" in e.lower() for e in errors)


def test_validate_missing_mapping_name(valid_config: dict) -> None:
    cfg = copy.deepcopy(valid_config)
    del cfg["mappings"][0]["name"]
    errors = validate_config(cfg)
    assert any("name" in e.lower() for e in errors)


def test_validate_duplicate_mapping_names(valid_config: dict) -> None:
    cfg = copy.deepcopy(valid_config)
    cfg["mappings"].append(copy.deepcopy(cfg["mappings"][0]))
    errors = validate_config(cfg)
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_unknown_provider(valid_config: dict) -> None:
    cfg = copy.deepcopy(valid_config)
    cfg["mappings"][0]["source"]["provider"] = "caldav"
    errors = validate_config(cfg)
    assert any("caldav" in e.lower() for e in errors)


def test_validate_account_not_in_providers(valid_config: dict) -> None:
    cfg = copy.deepcopy(valid_config)
    cfg["mappings"][0]["source"]["account"] = "unknown@example.com"
    errors = validate_config(cfg)
    assert any("unknown@example.com" in e.lower() for e in errors)


def test_get_mapping_by_name(valid_config: dict) -> None:
    mapping = get_mapping(valid_config, "test-inbound")
    assert mapping is not None
    assert mapping["name"] == "test-inbound"


def test_get_mapping_not_found(valid_config: dict) -> None:
    mapping = get_mapping(valid_config, "nonexistent")
    assert mapping is None


def test_validate_filtered_copy_needs_filter(valid_config: dict) -> None:
    cfg = copy.deepcopy(valid_config)
    cfg["mappings"][0]["type"] = "filtered-copy"
    errors = validate_config(cfg)
    assert any("filter" in e.lower() for e in errors)


def test_config_defaults_applied(valid_config: dict) -> None:
    cfg = copy.deepcopy(valid_config)
    del cfg["retry"]
    del cfg["safety"]
    result = apply_defaults(cfg)
    assert "retry" in result
    assert "safety" in result
    assert result["retry"]["maxRetries"] == 3
    assert result["safety"]["maxChangesPerRun"] == 50
