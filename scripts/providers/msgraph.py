"""Microsoft Graph Calendar Provider."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from typing import cast

from providers.base import Calendar, CalendarProvider, Event

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.microsoft.com/v1.0"


class _HTMLStripper(HTMLParser):
    """Strips HTML tags, preserving text content and basic structure."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "p", "div", "tr"):
            self._parts.append("\n")
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self._parts.append(f"[{value}] ")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def html_to_text(html: str) -> str:
    """Strip HTML tags, preserving links and line breaks."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def _parse_graph_event(data: dict) -> Event:
    """Convert a MS Graph event JSON dict to an Event dataclass."""
    start_raw = data.get("start", {})
    end_raw = data.get("end", {})

    start_str = start_raw.get("dateTime", "")
    end_str = end_raw.get("dateTime", "")

    # CRITICAL: strip fractional seconds by splitting on ".", not rstrip
    start_dt = datetime.fromisoformat(start_str.split(".")[0]) if start_str else datetime.min
    end_dt = datetime.fromisoformat(end_str.split(".")[0]) if end_str else datetime.min

    # Description
    body = data.get("body", {})
    description = body.get("content", "")
    if body.get("contentType", "").lower() == "html" and description:
        description = html_to_text(description)

    # Location
    location = (data.get("location") or {}).get("displayName", "")

    # Attendees
    attendees: list[str] = []
    for att in data.get("attendees", []):
        addr = (att.get("emailAddress") or {}).get("address", "")
        if addr:
            attendees.append(addr)

    # Meeting link
    meeting_link = (data.get("onlineMeeting") or {}).get("joinUrl", "")

    # Status
    status = "cancelled" if data.get("isCancelled") else "confirmed"

    return Event(
        event_id=data.get("id", ""),
        summary=data.get("subject", ""),
        start=start_dt,
        end=end_dt,
        description=description,
        location=location,
        is_all_day=data.get("isAllDay", False),
        show_as=data.get("showAs", "busy"),
        status=status,
        visibility="private" if data.get("sensitivity") == "private" else "default",
        attendees=attendees,
        meeting_link=meeting_link,
        recurring_event_id=data.get("seriesMasterId", ""),
        raw=data,
    )


def _event_to_graph_body(event: Event) -> dict:
    """Convert an Event dataclass into an MS Graph request body dict."""
    body: dict = {
        "subject": event.summary,
        "body": {"contentType": "text", "content": event.description},
        "start": {"dateTime": event.start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": event.end.isoformat(), "timeZone": "UTC"},
        "isAllDay": event.is_all_day,
        "showAs": event.show_as,
    }
    if event.location:
        body["location"] = {"displayName": event.location}
    if event.visibility == "private":
        body["sensitivity"] = "private"
    return body


class MSGraphProvider(CalendarProvider):
    """Calendar provider using Microsoft Graph API."""

    def __init__(
        self,
        token_script: str,
        max_retries: int = 3,
        backoff: list[int] | None = None,
    ) -> None:
        self.token_script = os.path.expanduser(token_script)
        self.max_retries = max_retries
        self.backoff = backoff if backoff is not None else [2, 4, 8]
        self._creds_map: dict[str, str] = {}

    def set_credentials_map(self, creds_map: dict[str, str]) -> None:
        """Set mapping of {account_email: creds_file_path}."""
        self._creds_map = dict(creds_map)

    def _resolve_creds(self, account: str) -> str:
        """Look up credentials file path for an account."""
        if account not in self._creds_map:
            raise ValueError(f"No credentials configured for account: {account}")
        return os.path.expanduser(self._creds_map[account])

    def _get_token(self, account: str, creds_file: str) -> str:
        """Get access token by calling the token management script."""
        result = subprocess.run(
            ["python3", self.token_script, "token", "--config", creds_file],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Token script failed for {account}: {result.stderr.strip()}")
        return result.stdout.strip()

    def _request(
        self,
        method: str,
        url: str,
        token: str,
        body: dict | None = None,
    ) -> dict:
        """Make an HTTP request to MS Graph with retry on 429."""
        data_bytes = json.dumps(body).encode() if body is not None else None
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            req = urllib.request.Request(
                url,
                data=data_bytes,
                method=method,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    resp_body = resp.read().decode()
                    if not resp_body:
                        return {}
                    return cast(dict, json.loads(resp_body))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    wait = int(retry_after) if retry_after else self.backoff[min(attempt, len(self.backoff) - 1)]
                    logger.warning(
                        "HTTP 429 on %s %s — retrying in %ds (attempt %d/%d)",
                        method,
                        url,
                        wait,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(wait)
                    last_exc = exc
                else:
                    raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Graph API request failed: {method} {url}")

    def list_events(self, account: str, calendar_id: str, start: datetime, end: datetime) -> list[Event]:
        creds_file = self._resolve_creds(account)
        token = self._get_token(account, creds_file)

        start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        params = f"startDateTime={start_str}&endDateTime={end_str}&$top=500"

        if calendar_id == "primary" or not calendar_id:
            url: str | None = f"{BASE_URL}/me/calendarView?{params}"
        else:
            url = f"{BASE_URL}/me/calendars/{calendar_id}/calendarView?{params}"

        events: list[Event] = []
        while url:
            data = self._request("GET", url, token)
            for item in data.get("value", []):
                events.append(_parse_graph_event(item))
            url = data.get("@odata.nextLink")

        return events

    def create_event(self, account: str, calendar_id: str, event: Event) -> str:
        creds_file = self._resolve_creds(account)
        token = self._get_token(account, creds_file)

        body = _event_to_graph_body(event)

        if calendar_id == "primary" or not calendar_id:
            url = f"{BASE_URL}/me/events"
        else:
            url = f"{BASE_URL}/me/calendars/{calendar_id}/events"

        result = self._request("POST", url, token, body)
        return cast(str, result.get("id", ""))

    def update_event(self, account: str, calendar_id: str, event_id: str, event: Event) -> None:
        creds_file = self._resolve_creds(account)
        token = self._get_token(account, creds_file)
        body = _event_to_graph_body(event)
        url = f"{BASE_URL}/me/events/{event_id}"
        self._request("PATCH", url, token, body)

    def delete_event(self, account: str, calendar_id: str, event_id: str) -> None:
        creds_file = self._resolve_creds(account)
        token = self._get_token(account, creds_file)
        url = f"{BASE_URL}/me/events/{event_id}"
        self._request("DELETE", url, token)

    def list_calendars(self, account: str) -> list[Calendar]:
        creds_file = self._resolve_creds(account)
        token = self._get_token(account, creds_file)
        url = f"{BASE_URL}/me/calendars"
        data = self._request("GET", url, token)
        calendars: list[Calendar] = []
        for item in data.get("value", []):
            calendars.append(
                Calendar(
                    calendar_id=item.get("id", ""),
                    name=item.get("name", ""),
                    access_role=(item.get("canEdit", "") and "writer") or "reader",
                )
            )
        return calendars

    def create_calendar(self, account: str, name: str, color: str | None = None) -> str:
        creds_file = self._resolve_creds(account)
        token = self._get_token(account, creds_file)
        body: dict = {"name": name}
        if color:
            body["color"] = color
        result = self._request("POST", f"{BASE_URL}/me/calendars", token, body)
        return cast(str, result.get("id", ""))
