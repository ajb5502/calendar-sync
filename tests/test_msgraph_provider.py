"""Tests for the MS Graph Calendar Provider."""

from __future__ import annotations

import json
import sys
import urllib.error
from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))

from providers.msgraph import MSGraphProvider, _parse_graph_event, html_to_text


def _make_provider() -> MSGraphProvider:
    p = MSGraphProvider(token_script="/fake/ms-graph-auth.py")
    p.set_credentials_map({"user@gw.com": "/fake/creds.json"})
    return p


def _mock_response(data: dict, status: int = 200) -> MagicMock:  # type: ignore[type-arg]
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


SAMPLE_EVENT = {
    "id": "AAMk123",
    "subject": "Sprint Planning",
    "start": {"dateTime": "2026-03-16T14:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2026-03-16T15:00:00.0000000", "timeZone": "UTC"},
    "body": {"contentType": "html", "content": "<p>Agenda: sprint items</p>"},
    "location": {"displayName": "Teams"},
    "isAllDay": False,
    "showAs": "busy",
    "isCancelled": False,
    "attendees": [
        {
            "emailAddress": {"address": "bob@gw.com"},
            "status": {"response": "accepted"},
        }
    ],
    "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/abc"},
    "seriesMasterId": "AAMkMaster",
}


class TestListEvents:
    @patch("providers.msgraph.MSGraphProvider._get_token", return_value="fake-token")
    @patch("providers.msgraph.urllib.request.urlopen")
    def test_list_events_parses_json(self, mock_urlopen: MagicMock, mock_token: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"value": [SAMPLE_EVENT]})

        provider = _make_provider()
        events = provider.list_events(
            "user@gw.com",
            "primary",
            datetime(2026, 3, 16),
            datetime(2026, 3, 17),
        )

        assert len(events) == 1
        ev = events[0]
        assert ev.event_id == "AAMk123"
        assert ev.summary == "Sprint Planning"
        assert ev.start == datetime(2026, 3, 16, 14, 0)
        assert ev.end == datetime(2026, 3, 16, 15, 0)
        assert ev.location == "Teams"
        assert ev.is_all_day is False
        assert ev.show_as == "busy"
        assert ev.status == "confirmed"
        assert ev.attendees == ["bob@gw.com"]
        assert "teams.microsoft.com" in ev.meeting_link
        assert ev.recurring_event_id == "AAMkMaster"

    @patch("providers.msgraph.MSGraphProvider._get_token", return_value="fake-token")
    @patch("providers.msgraph.urllib.request.urlopen")
    def test_pagination_follows_next_link(self, mock_urlopen: MagicMock, mock_token: MagicMock) -> None:
        page1_event = {**SAMPLE_EVENT, "id": "ev1", "subject": "Event 1"}
        page2_event = {**SAMPLE_EVENT, "id": "ev2", "subject": "Event 2"}

        page1 = _mock_response(
            {
                "value": [page1_event],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView?skip=1",
            }
        )
        page2 = _mock_response({"value": [page2_event]})

        mock_urlopen.side_effect = [page1, page2]

        provider = _make_provider()
        events = provider.list_events("user@gw.com", "primary", datetime(2026, 3, 16), datetime(2026, 3, 17))

        assert len(events) == 2
        assert events[0].event_id == "ev1"
        assert events[1].event_id == "ev2"
        assert mock_urlopen.call_count == 2


class TestAllDayEvent:
    def test_all_day_event(self) -> None:
        data = {
            **SAMPLE_EVENT,
            "isAllDay": True,
            "showAs": "free",
        }
        ev = _parse_graph_event(data)
        assert ev.is_all_day is True
        assert ev.show_as == "free"


class TestHTMLToText:
    def test_html_to_text(self) -> None:
        html = '<p>Hello <a href="https://example.com">world</a></p><br>Next line'
        text = html_to_text(html)
        assert "Hello" in text
        assert "https://example.com" in text
        assert "world" in text
        assert "Next line" in text

    def test_plain_text_passthrough(self) -> None:
        assert html_to_text("just plain text") == "just plain text"


class TestCreateEvent:
    @patch("providers.msgraph.MSGraphProvider._get_token", return_value="fake-token")
    @patch("providers.msgraph.urllib.request.urlopen")
    def test_create_event_returns_id(self, mock_urlopen: MagicMock, mock_token: MagicMock) -> None:
        mock_urlopen.return_value = _mock_response({"id": "new-event-id"})

        from providers.base import Event

        event = Event(
            event_id="",
            summary="New Meeting",
            start=datetime(2026, 3, 17, 10, 0),
            end=datetime(2026, 3, 17, 11, 0),
        )

        provider = _make_provider()
        event_id = provider.create_event("user@gw.com", "primary", event)

        assert event_id == "new-event-id"
        mock_urlopen.assert_called_once()


class TestRetry:
    @patch("providers.msgraph.MSGraphProvider._get_token", return_value="fake-token")
    @patch("providers.msgraph.time.sleep")
    @patch("providers.msgraph.urllib.request.urlopen")
    def test_429_retry(
        self,
        mock_urlopen: MagicMock,
        mock_sleep: MagicMock,
        mock_token: MagicMock,
    ) -> None:
        # First call raises 429, second succeeds
        err_429 = urllib.error.HTTPError(
            url="https://graph.microsoft.com/v1.0/me/events",
            code=429,
            msg="Too Many Requests",
            hdrs=MagicMock(get=lambda key, default=None: "5" if key == "Retry-After" else default),  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )
        success = _mock_response({"value": []})
        mock_urlopen.side_effect = [err_429, success]

        provider = _make_provider()
        provider.list_events("user@gw.com", "primary", datetime(2026, 3, 16), datetime(2026, 3, 17))

        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once_with(5)


class TestDatetimeParsing:
    def test_datetime_parsing_no_corruption(self) -> None:
        """T10:00:00.0000000 must parse to hour=10, not hour=1."""
        data = {
            **SAMPLE_EVENT,
            "start": {"dateTime": "2026-03-16T10:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-16T10:30:00.0000000", "timeZone": "UTC"},
        }
        ev = _parse_graph_event(data)
        assert ev.start.hour == 10
        assert ev.start.minute == 0
        assert ev.end.hour == 10
        assert ev.end.minute == 30
