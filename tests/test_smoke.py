"""Smoke tests — lightweight read-only API validation for drift detection.
Run daily via cron. Validates real API output matches expected shapes.
Alerts via WhatsApp if drift detected."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

START = datetime.now(timezone.utc)
END = START + timedelta(days=7)


@pytest.mark.smoke
class TestAPISmoke:
    """Read-only smoke tests that hit real APIs to detect schema drift."""

    def test_gog_event_schema(self) -> None:
        """Verify gog CLI returns events with expected fields."""
        result = subprocess.run(
            [
                "gog",
                "calendar",
                "events",
                "primary",
                "--account",
                "alexanderbuchanan91@gmail.com",
                "--from",
                START.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "--to",
                END.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "--json",
                "--results-only",
                "--max",
                "5",
                "--no-input",
                "--force",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"gog failed: {result.stderr}"
        events = json.loads(result.stdout)
        if events:
            required_keys = {"id", "summary", "start", "end", "status"}
            actual_keys = set(events[0].keys())
            missing = required_keys - actual_keys
            assert not missing, f"gog event missing keys: {missing}"

    def test_ms_graph_token_valid(self) -> None:
        """Verify MS Graph tokens are refreshable for all configured accounts."""
        creds_files = [
            ("GW", "/Users/johnthomas/.openclaw/credentials/gw-corp-azure.json"),
            ("BNB", "/Users/johnthomas/.openclaw/credentials/bnb-ventures-azure.json"),
        ]
        for name, creds in creds_files:
            result = subprocess.run(
                ["python3", "/Users/johnthomas/.openclaw/scripts/ms-graph-auth.py", "token", "--config", creds],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, f"{name} token fetch failed: {result.stderr}"
            token = result.stdout.strip()
            assert len(token) > 100, f"{name} token suspiciously short ({len(token)} chars)"

    def test_gog_auth_valid(self) -> None:
        """Verify all gog accounts are still authenticated."""
        result = subprocess.run(
            ["gog", "auth", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        expected_accounts = [
            "alexanderbuchanan91@gmail.com",
            "akbuchanan2017@gmail.com",
            "assistantsecretarypburglodge52@gmail.com",
        ]
        for acct in expected_accounts:
            assert acct in result.stdout, f"gog auth missing account: {acct}"
