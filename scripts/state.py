"""Sync state persistence — tracks last-sync times and error counts per mapping."""

from __future__ import annotations

import json
import os
from typing import cast


class SyncState:
    """Read/write sync state from a JSON file."""

    def __init__(self, path: str) -> None:
        self._path = os.path.expanduser(path)
        if os.path.exists(self._path):
            with open(self._path) as f:
                self._data: dict = cast(dict, json.load(f))
        else:
            self._data = {"mappings": {}}

    def _ensure_mapping(self, name: str) -> dict:
        if name not in self._data["mappings"]:
            self._data["mappings"][name] = {}
        return cast(dict, self._data["mappings"][name])

    def get_last_sync(self, mapping_name: str) -> dict | None:
        mapping = cast(dict, self._data["mappings"].get(mapping_name, {}))
        return cast(dict, mapping["lastSync"]) if "lastSync" in mapping else None

    def record_sync(
        self,
        mapping_name: str,
        synced_at: str,
        created: int,
        updated: int,
        deleted: int,
    ) -> None:
        mapping = self._ensure_mapping(mapping_name)
        mapping["lastSync"] = {
            "syncedAt": synced_at,
            "created": created,
            "updated": updated,
            "deleted": deleted,
        }
        mapping["consecutiveErrors"] = 0

    def get_error_count(self, mapping_name: str) -> int:
        mapping = cast(dict, self._data["mappings"].get(mapping_name, {}))
        return int(mapping.get("consecutiveErrors", 0))

    def record_error(self, mapping_name: str, error_msg: str) -> None:
        mapping = self._ensure_mapping(mapping_name)
        mapping["consecutiveErrors"] = mapping.get("consecutiveErrors", 0) + 1

    def save(self) -> None:
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)
        os.chmod(self._path, 0o600)
