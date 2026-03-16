"""Config loading, validation, and defaults for calendar-sync."""

from __future__ import annotations

import json
import os
from typing import cast

VALID_PROVIDERS = {"google", "msgraph"}
VALID_SYNC_TYPES = {"full-detail", "busy-block", "filtered-copy"}

DEFAULTS: dict = {
    "safety": {"maxChangesPerRun": 50, "maxEventsPerMapping": 500, "dryRun": False},
    "retry": {"maxRetries": 3, "backoffSeconds": [2, 4, 8]},
    "notifications": {"method": "stdout"},
}


def load_config(path: str) -> dict:
    """Load a JSON config file, expanding ~ in the path."""
    expanded = os.path.expanduser(path)
    with open(expanded) as f:
        return cast(dict, json.load(f))


def apply_defaults(config: dict) -> dict:
    """Fill missing top-level keys from DEFAULTS. Returns the mutated config."""
    for key, value in DEFAULTS.items():
        if key not in config:
            config[key] = value.copy() if isinstance(value, dict) else value
    return config


def _get_provider_accounts(config: dict) -> dict[str, set[str]]:
    """Return {provider_name: set(account_emails)} from the providers block."""
    result: dict[str, set[str]] = {}
    providers = config.get("providers", {})
    for prov_name, prov_conf in providers.items():
        accounts: set[str] = set()
        for acct in prov_conf.get("accounts", []):
            if isinstance(acct, str):
                accounts.add(acct)
            elif isinstance(acct, dict):
                accounts.add(acct["email"])
        result[prov_name] = accounts
    return result


def validate_config(config: dict) -> list[str]:
    """Validate config and return a list of error strings. Empty list means valid."""
    errors: list[str] = []

    # Hub checks
    hub = config.get("hub")
    if not hub:
        errors.append("Missing required 'hub' section")
        return errors

    for field in ("provider", "account", "calendarId"):
        if field not in hub:
            errors.append(f"Hub missing required field '{field}'")

    # Mappings checks
    mappings = config.get("mappings")
    if not mappings:
        errors.append("Missing or empty 'mappings' section")
        return errors

    provider_accounts = _get_provider_accounts(config)
    seen_names: set[str] = set()

    for i, mapping in enumerate(mappings):
        name = mapping.get("name")
        if not name:
            errors.append(f"Mapping at index {i} missing required 'name'")
            continue

        if name in seen_names:
            errors.append(f"Duplicate mapping name '{name}'")
        seen_names.add(name)

        sync_type = mapping.get("type", "")
        if sync_type not in VALID_SYNC_TYPES:
            errors.append(f"Mapping '{name}' has invalid type '{sync_type}'")

        if sync_type == "filtered-copy" and "filter" not in mapping:
            errors.append(f"Mapping '{name}' type 'filtered-copy' requires a 'filter' key")

        # Check source(s)
        sources = []
        if "source" in mapping:
            sources = [mapping["source"]]
        elif "sources" in mapping:
            sources = mapping["sources"]
        else:
            errors.append(f"Mapping '{name}' missing 'source' or 'sources'")

        for src in sources:
            provider = src.get("provider", "")
            account = src.get("account", "")
            if provider not in VALID_PROVIDERS:
                errors.append(f"Mapping '{name}' uses unknown provider '{provider}'")
            elif provider in provider_accounts and account not in provider_accounts[provider]:
                errors.append(f"Mapping '{name}' references account '{account}' not found in providers.{provider}")

        # Check target
        target = mapping.get("target")
        if target:
            provider = target.get("provider", "")
            account = target.get("account", "")
            if provider not in VALID_PROVIDERS:
                errors.append(f"Mapping '{name}' target uses unknown provider '{provider}'")
            elif provider in provider_accounts and account not in provider_accounts[provider]:
                errors.append(
                    f"Mapping '{name}' target references account '{account}' not found in providers.{provider}"
                )

    return errors


def get_mapping(config: dict, name: str) -> dict | None:
    """Return the mapping with the given name, or None if not found."""
    for mapping in config.get("mappings", []):
        if mapping.get("name") == name:
            return cast(dict, mapping)
    return None
