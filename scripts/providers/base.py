"""Abstract CalendarProvider interface and shared data types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    event_id: str
    summary: str
    start: datetime  # UTC
    end: datetime  # UTC
    description: str = ""
    location: str = ""
    is_all_day: bool = False
    show_as: str = "busy"  # "busy" | "free"
    status: str = "confirmed"  # "confirmed" | "tentative" | "cancelled"
    visibility: str = "default"  # "default" | "private" | "public"
    attendees: list[str] = field(default_factory=list)
    meeting_link: str = ""
    color_id: str = ""
    recurring_event_id: str = ""
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Calendar:
    calendar_id: str
    name: str
    access_role: str = ""  # "owner" | "writer" | "reader"


class CalendarProvider(ABC):
    @abstractmethod
    def list_events(self, account: str, calendar_id: str, start: datetime, end: datetime) -> list[Event]: ...

    @abstractmethod
    def create_event(self, account: str, calendar_id: str, event: Event) -> str:
        """Create event, return new event ID."""
        ...

    @abstractmethod
    def update_event(self, account: str, calendar_id: str, event_id: str, event: Event) -> None: ...

    @abstractmethod
    def delete_event(self, account: str, calendar_id: str, event_id: str) -> None: ...

    @abstractmethod
    def list_calendars(self, account: str) -> list[Calendar]: ...

    @abstractmethod
    def create_calendar(self, account: str, name: str, color: str | None = None) -> str:
        """Create a sub-calendar, return calendar ID."""
        ...
