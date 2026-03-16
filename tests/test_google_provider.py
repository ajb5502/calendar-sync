"""Tests for the Google Calendar provider (gog CLI wrapper)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from providers.google import GoogleProvider, _extract_meeting_link, _parse_event

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SAMPLE_EVENT = {
    "id": "abc123",
    "summary": "Team Standup",
    "start": {"dateTime": "2026-03-16T15:00:00-04:00", "timeZone": "America/New_York"},
    "end": {"dateTime": "2026-03-16T15:30:00-04:00", "timeZone": "America/New_York"},
    "description": "Daily sync",
    "location": "Room A",
    "status": "confirmed",
    "transparency": "transparent",
    "colorId": "3",
    "recurringEventId": "abc_parent",
    "attendees": [
        {"email": "bob@example.com", "responseStatus": "accepted"},
        {"email": "alice@example.com", "responseStatus": "tentative"},
    ],
    "conferenceData": {
        "entryPoints": [
            {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
        ]
    },
}

ALL_DAY_EVENT = {
    "id": "allday1",
    "summary": "Company Holiday",
    "start": {"date": "2026-03-20"},
    "end": {"date": "2026-03-21"},
    "status": "confirmed",
}


def _gog_cmd(*extra: str) -> list:  # type: ignore[type-arg]
    """Build expected gog command list with standard trailing flags."""
    return ["gog", *extra, "--json", "--results-only", "--no-input", "--force"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListEvents:
    def test_list_events_parses_json(self, fp) -> None:  # type: ignore[no-untyped-def]
        fp.register(
            _gog_cmd(
                "calendar",
                "events",
                "primary",
                "--account",
                "user@example.com",
                "--from",
                "2026-03-16T00:00:00Z",
                "--to",
                "2026-03-17T00:00:00Z",
                "--all-pages",
                "--max",
                "500",
            ),
            stdout=json.dumps([SAMPLE_EVENT]),
        )

        provider = GoogleProvider()
        events = provider.list_events(
            account="user@example.com",
            calendar_id="primary",
            start=datetime(2026, 3, 16),
            end=datetime(2026, 3, 17),
        )

        assert len(events) == 1
        ev = events[0]
        assert ev.event_id == "abc123"
        assert ev.summary == "Team Standup"
        assert ev.start == datetime(2026, 3, 16, 19, 0)  # 15:00 EDT → 19:00 UTC
        assert ev.end == datetime(2026, 3, 16, 19, 30)
        assert ev.description == "Daily sync"
        assert ev.location == "Room A"
        assert ev.show_as == "free"  # transparent → free
        assert ev.color_id == "3"
        assert ev.recurring_event_id == "abc_parent"
        assert ev.attendees == ["bob@example.com", "alice@example.com"]
        assert ev.meeting_link == "https://meet.google.com/abc-defg-hij"
        assert ev.is_all_day is False

    def test_all_day_event(self, fp) -> None:  # type: ignore[no-untyped-def]
        fp.register(
            _gog_cmd(
                "calendar",
                "events",
                "primary",
                "--account",
                "user@example.com",
                "--from",
                "2026-03-16T00:00:00Z",
                "--to",
                "2026-03-22T00:00:00Z",
                "--all-pages",
                "--max",
                "500",
            ),
            stdout=json.dumps([ALL_DAY_EVENT]),
        )

        provider = GoogleProvider()
        events = provider.list_events(
            account="user@example.com",
            calendar_id="primary",
            start=datetime(2026, 3, 16),
            end=datetime(2026, 3, 22),
        )

        assert len(events) == 1
        ev = events[0]
        assert ev.is_all_day is True
        assert ev.start == datetime(2026, 3, 20)
        assert ev.end == datetime(2026, 3, 21)
        assert ev.show_as == "busy"


class TestMeetingLink:
    def test_meeting_link_from_conference_data(self) -> None:
        ev = _parse_event(SAMPLE_EVENT)
        assert ev.meeting_link == "https://meet.google.com/abc-defg-hij"

    def test_meeting_link_from_description(self) -> None:
        data = {
            "id": "teams1",
            "summary": "Teams Call",
            "start": {"dateTime": "2026-03-16T10:00:00Z"},
            "end": {"dateTime": "2026-03-16T11:00:00Z"},
            "description": "Join: https://teams.microsoft.com/l/meetup-join/abc123 click here",
        }
        link = _extract_meeting_link(data)
        assert link == "https://teams.microsoft.com/l/meetup-join/abc123"


class TestCreateEvent:
    def test_create_event_returns_id(self, fp) -> None:  # type: ignore[no-untyped-def]
        fp.register(
            _gog_cmd(
                "calendar",
                "create",
                "primary",
                "--account",
                "user@example.com",
                "--summary",
                "New Meeting",
                "--from",
                "2026-03-17T14:00:00Z",
                "--to",
                "2026-03-17T15:00:00Z",
                "--transparency",
                "opaque",
            ),
            stdout=json.dumps({"id": "new_event_42"}),
        )

        provider = GoogleProvider()
        event = _parse_event(
            {
                "id": "",
                "summary": "New Meeting",
                "start": {"dateTime": "2026-03-17T14:00:00Z"},
                "end": {"dateTime": "2026-03-17T15:00:00Z"},
            }
        )
        new_id = provider.create_event(
            account="user@example.com",
            calendar_id="primary",
            event=event,
        )
        assert new_id == "new_event_42"


class TestDeleteEvent:
    def test_delete_event(self, fp) -> None:  # type: ignore[no-untyped-def]
        fp.register(
            _gog_cmd(
                "calendar",
                "delete",
                "primary",
                "evt_99",
                "--account",
                "user@example.com",
            ),
            stdout="",
        )

        provider = GoogleProvider()
        provider.delete_event(
            account="user@example.com",
            calendar_id="primary",
            event_id="evt_99",
        )

        assert (
            fp.call_count(
                _gog_cmd(
                    "calendar",
                    "delete",
                    "primary",
                    "evt_99",
                    "--account",
                    "user@example.com",
                )
            )
            == 1
        )


class TestErrorHandling:
    def test_gog_error_raises_runtime_error(self, fp) -> None:  # type: ignore[no-untyped-def]
        fp.register(
            _gog_cmd(
                "calendar",
                "events",
                "primary",
                "--account",
                "user@example.com",
                "--from",
                "2026-03-16T00:00:00Z",
                "--to",
                "2026-03-17T00:00:00Z",
                "--all-pages",
                "--max",
                "500",
            ),
            returncode=1,
            stderr="auth failed",
        )

        provider = GoogleProvider()
        with pytest.raises(RuntimeError, match="gog exited with code 1"):
            provider.list_events(
                account="user@example.com",
                calendar_id="primary",
                start=datetime(2026, 3, 16),
                end=datetime(2026, 3, 17),
            )
