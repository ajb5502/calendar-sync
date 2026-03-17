"""Google Calendar provider using the gog CLI."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Union, cast

from providers.base import Calendar, CalendarProvider, Event


def _parse_datetime(dt_obj: dict) -> tuple[datetime, bool]:
    """Parse a Google Calendar datetime object into (UTC datetime, is_all_day).

    Handles both ``dateTime`` (timed events) and ``date`` (all-day events).
    """
    if "dateTime" in dt_obj:
        raw = dt_obj["dateTime"]
        # Python 3.9 fromisoformat doesn't accept trailing 'Z'
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt, False

    # All-day event: "date": "2026-03-20"
    raw = dt_obj["date"]
    dt = datetime.strptime(raw, "%Y-%m-%d")
    return dt, True


def _extract_meeting_link(event_data: dict) -> str:
    """Return the first video-conference link found, or empty string.

    Priority: conferenceData → description → location.
    """
    conf = event_data.get("conferenceData") or {}
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return cast(str, ep["uri"])

    # Fallback: search description and location for known meeting URLs.
    pattern = r"https?://(?:teams\.microsoft\.com|[\w.-]*zoom\.us|meet\.google\.com)/\S+"
    for field in ("description", "location"):
        text = event_data.get(field, "")
        if text:
            match = re.search(pattern, text)
            if match:
                return match.group(0)

    return ""


def _parse_event(data: dict) -> Event:
    """Convert a raw gog JSON event dict into an ``Event`` dataclass."""
    start_dt, is_all_day_start = _parse_datetime(data.get("start", {}))
    end_dt, _ = _parse_datetime(data.get("end", {}))

    attendees_raw: list = data.get("attendees") or []
    attendee_emails = [a["email"] for a in attendees_raw if "email" in a]

    transparency = data.get("transparency", "opaque")
    show_as = "free" if transparency == "transparent" else "busy"

    return Event(
        event_id=data.get("id", ""),
        summary=data.get("summary", ""),
        start=start_dt,
        end=end_dt,
        description=data.get("description", ""),
        location=data.get("location", ""),
        is_all_day=is_all_day_start,
        show_as=show_as,
        status=data.get("status", "confirmed"),
        visibility=data.get("visibility", "default"),
        attendees=attendee_emails,
        meeting_link=_extract_meeting_link(data),
        color_id=data.get("colorId", ""),
        recurring_event_id=data.get("recurringEventId", ""),
        raw=data,
    )


class GoogleProvider(CalendarProvider):
    """Google Calendar provider backed by the ``gog`` CLI."""

    def __init__(self, gog_binary: str = "gog") -> None:
        self._gog = gog_binary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, args: list[str], expect_json: bool = True) -> str | list | dict:
        """Run a gog CLI command, returning parsed JSON or raw stdout.

        Common flags ``--json --results-only --no-input --force`` are appended
        automatically.
        """
        cmd = [self._gog, *args, "--json", "--results-only", "--no-input", "--force"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"gog exited with code {result.returncode}: {result.stderr.strip()}")
        if expect_json:
            return cast(Union[dict, list], json.loads(result.stdout))
        return result.stdout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_events(
        self,
        account: str,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> list[Event]:
        start_rfc = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_rfc = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = self._run(
            [
                "calendar",
                "events",
                calendar_id,
                "--account",
                account,
                "--from",
                start_rfc,
                "--to",
                end_rfc,
                "--all-pages",
                "--max",
                "500",
            ]
        )
        items: list = raw if isinstance(raw, list) else cast(dict, raw).get("items", [])
        return [_parse_event(item) for item in items]

    def create_event(self, account: str, calendar_id: str, event: Event) -> str:
        if event.is_all_day:
            start_rfc = event.start.strftime("%Y-%m-%d")
            end_rfc = event.end.strftime("%Y-%m-%d")
        else:
            start_rfc = event.start.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_rfc = event.end.strftime("%Y-%m-%dT%H:%M:%SZ")

        args = [
            "calendar",
            "create",
            calendar_id,
            "--account",
            account,
            "--summary",
            event.summary,
            "--from",
            start_rfc,
            "--to",
            end_rfc,
        ]

        if event.is_all_day:
            args.append("--all-day")
        if event.description:
            args.extend(["--description", event.description])
        if event.location:
            args.extend(["--location", event.location])
        if event.color_id:
            args.extend(["--event-color", event.color_id])
        if event.visibility and event.visibility != "default":
            args.extend(["--visibility", event.visibility])
        if event.show_as == "free":
            args.extend(["--transparency", "transparent"])
        else:
            args.extend(["--transparency", "opaque"])

        raw = self._run(args)
        if isinstance(raw, dict):
            return cast(str, raw.get("id", ""))
        return ""

    def update_event(
        self,
        account: str,
        calendar_id: str,
        event_id: str,
        event: Event,
    ) -> None:
        if event.is_all_day:
            start_rfc = event.start.strftime("%Y-%m-%d")
            end_rfc = event.end.strftime("%Y-%m-%d")
        else:
            start_rfc = event.start.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_rfc = event.end.strftime("%Y-%m-%dT%H:%M:%SZ")

        args = [
            "calendar",
            "update",
            calendar_id,
            event_id,
            "--account",
            account,
            "--summary",
            event.summary,
            "--from",
            start_rfc,
            "--to",
            end_rfc,
        ]

        if event.is_all_day:
            args.append("--all-day")
        if event.description:
            args.extend(["--description", event.description])
        if event.location:
            args.extend(["--location", event.location])
        if event.color_id:
            args.extend(["--event-color", event.color_id])
        if event.visibility and event.visibility != "default":
            args.extend(["--visibility", event.visibility])
        if event.show_as == "free":
            args.extend(["--transparency", "transparent"])
        else:
            args.extend(["--transparency", "opaque"])

        self._run(args)

    def delete_event(self, account: str, calendar_id: str, event_id: str) -> None:
        self._run(
            ["calendar", "delete", calendar_id, event_id, "--account", account],
            expect_json=False,
        )

    def list_calendars(self, account: str) -> list[Calendar]:
        raw = self._run(["calendar", "calendars", "--account", account])
        items: list = raw if isinstance(raw, list) else cast(dict, raw).get("items", [])
        return [
            Calendar(
                calendar_id=item.get("id", ""),
                name=item.get("summary", ""),
                access_role=item.get("accessRole", ""),
            )
            for item in items
        ]

    def create_calendar(self, account: str, name: str, color: str | None = None) -> str:
        raise NotImplementedError("gog CLI does not support calendar creation")
