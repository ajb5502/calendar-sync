"""Tests for SYNCV2 metadata encode/decode and hashing."""

from __future__ import annotations

import base64
import json

from sync.metadata import append_syncv2, compute_hash, decode_syncv2, encode_syncv2, strip_syncv2

# Minimal valid SYNCV2 metadata dict
SAMPLE_META: dict = {
    "srcProvider": "msgraph",
    "srcAccount": "user@company.com",
    "srcCalendar": "primary",
    "srcEventId": "AAMk123",
    "syncType": "full-detail",
    "mappingName": "work-to-personal",
    "srcHash": "abc123",
    "syncedAt": "2026-03-16T12:00:00Z",
    "version": 2,
}


class TestRoundTrip:
    def test_round_trip(self) -> None:
        encoded = encode_syncv2(SAMPLE_META)
        assert encoded.startswith("SYNCV2:")
        decoded = decode_syncv2(encoded)
        assert decoded == SAMPLE_META


class TestDecodeReturnsNoneForNoTag:
    def test_none(self) -> None:
        assert decode_syncv2(None) is None

    def test_empty_string(self) -> None:
        assert decode_syncv2("") is None

    def test_normal_text(self) -> None:
        assert decode_syncv2("Just a normal description") is None


class TestDecodeExtractsFromMultiline:
    def test_multiline(self) -> None:
        tag = encode_syncv2(SAMPLE_META)
        text = f"Join: https://teams.microsoft.com/l/meetup\n---\n{tag}"
        decoded = decode_syncv2(text)
        assert decoded is not None
        assert decoded["srcEventId"] == "AAMk123"


class TestUnknownVersionIgnored:
    def test_version_99(self) -> None:
        payload = base64.urlsafe_b64encode(json.dumps({"version": 99}).encode()).decode()
        tag = f"SYNCV2:{payload}"
        assert decode_syncv2(tag) is None


class TestHashDeterministic:
    def test_same_input_same_hash(self) -> None:
        fields = {"summary": "Standup", "start": "2026-03-16T09:00:00Z"}
        assert compute_hash(fields) == compute_hash(fields)


class TestHashChangesOnDiff:
    def test_different_input_different_hash(self) -> None:
        h1 = compute_hash({"summary": "Standup"})
        h2 = compute_hash({"summary": "Retro"})
        assert h1 != h2


class TestHashIgnoresKeyOrder:
    def test_key_order(self) -> None:
        h1 = compute_hash({"a": "1", "b": "2"})
        h2 = compute_hash({"b": "2", "a": "1"})
        assert h1 == h2


class TestAppendToDescription:
    def test_append(self) -> None:
        result = append_syncv2("Team meeting", SAMPLE_META)
        assert result.startswith("SYNCV2:")
        assert "Team meeting" in result
        decoded = decode_syncv2(result)
        assert decoded == SAMPLE_META


class TestStripSyncv2:
    def test_strip_none(self) -> None:
        assert strip_syncv2(None) == ""

    def test_strip_empty(self) -> None:
        assert strip_syncv2("") == ""

    def test_strip_no_tag(self) -> None:
        assert strip_syncv2("No tag here") == "No tag here"

    def test_strip_removes_tag(self) -> None:
        original = "Original description"
        with_tag = append_syncv2(original, SAMPLE_META)
        assert strip_syncv2(with_tag) == original
