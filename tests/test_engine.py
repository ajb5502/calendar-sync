"""Tests for the reconcile engine."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from providers.base import Event
from sync.engine import _hash_event, reconcile_mapping
from sync.metadata import append_syncv2


def make_event(
    eid: str,
    summary: str,
    hours_from_now: int = 1,
    duration_h: int = 1,
    description: str = "",
    show_as: str = "busy",
) -> Event:
    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    return Event(
        event_id=eid,
        summary=summary,
        start=now + timedelta(hours=hours_from_now),
        end=now + timedelta(hours=hours_from_now + duration_h),
        description=description,
        show_as=show_as,
        raw={},
    )


def make_managed_event(
    eid: str,
    src_event: Event,
    mapping_name: str,
    sync_type: str = "full-detail",
    src_account: str = "src@example.com",
) -> Event:
    """Create a target event with SYNCV2 metadata matching a source event."""
    meta = {
        "srcProvider": "google",
        "srcAccount": src_account,
        "srcCalendar": "primary",
        "srcEventId": src_event.event_id,
        "syncType": sync_type,
        "mappingName": mapping_name,
        "srcHash": _hash_event(src_event),
        "syncedAt": "2030-01-01T12:00:00Z",
        "version": 2,
    }
    desc = append_syncv2(src_event.description, meta)
    return Event(
        event_id=eid,
        summary=src_event.summary,
        start=src_event.start,
        end=src_event.end,
        description=desc,
        show_as=src_event.show_as,
    )


# Source = google, target = msgraph (different provider keys so _make_providers works)
MAPPING = {
    "name": "test-mapping",
    "type": "full-detail",
    "source": {"provider": "google", "account": "src@example.com", "calendarId": "primary"},
    "target": {"provider": "msgraph", "account": "hub@gmail.com", "calendarId": "primary"},
    "lookaheadDays": 30,
    "skipDeclined": True,
    "allDayMode": "free",
    "preserveFreeBusy": True,
}

BUSY_MAPPING = {
    "name": "hub-to-work",
    "type": "busy-block",
    "source": {"provider": "google", "account": "hub@gmail.com", "calendarId": "primary"},
    "target": {"provider": "msgraph", "account": "work@company.com", "calendarId": "primary"},
    "lookaheadDays": 30,
    "hold": {"visibility": "private", "showAs": "busy"},
}


class TestReconcileEngine(unittest.TestCase):
    def _make_providers(self, src_events: list[Event], tgt_events: list[Event]) -> dict[str, MagicMock]:
        src_provider: MagicMock = MagicMock()
        src_provider.list_events.return_value = src_events
        tgt_provider: MagicMock = MagicMock()
        tgt_provider.list_events.return_value = tgt_events
        tgt_provider.create_event.return_value = "new_id"
        return {"google": src_provider, "msgraph": tgt_provider}

    def test_create_new_events(self) -> None:
        src = [make_event("s1", "Meeting A"), make_event("s2", "Meeting B", hours_from_now=3)]
        providers = self._make_providers(src, [])
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deleted"], 0)

    def test_skip_unchanged(self) -> None:
        src = [make_event("s1", "Meeting A")]
        tgt = [make_managed_event("t1", src[0], "test-mapping", src_account="src@example.com")]
        providers = self._make_providers(src, tgt)
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_update_changed(self) -> None:
        src_original = make_event("s1", "Meeting A")
        tgt = [make_managed_event("t1", src_original, "test-mapping", src_account="src@example.com")]
        # Source event changed title
        src_changed = make_event("s1", "Meeting A — Updated")
        providers = self._make_providers([src_changed], tgt)
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["updated"], 1)

    def test_delete_stale(self) -> None:
        # Target has a managed event but source no longer has it
        orphan_src = make_event("gone", "Deleted Meeting")
        tgt = [make_managed_event("t1", orphan_src, "test-mapping", src_account="src@example.com")]
        providers = self._make_providers([], tgt)
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["deleted"], 1)

    def test_dry_run_no_mutations(self) -> None:
        src = [make_event("s1", "Meeting")]
        providers = self._make_providers(src, [])
        result = reconcile_mapping(MAPPING, providers, dry_run=True, max_changes=50)
        self.assertEqual(result["created"], 1)
        providers["msgraph"].create_event.assert_not_called()

    def test_max_changes_limit(self) -> None:
        src = [make_event(f"s{i}", f"Meeting {i}", hours_from_now=i) for i in range(10)]
        providers = self._make_providers(src, [])
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=3)
        self.assertEqual(result["created"], 3)
        self.assertTrue(result["limit_hit"])

    def test_reflection_prevention(self) -> None:
        """Busy-block mapping should skip events whose srcAccount matches target account."""
        # Hub has an event that was synced FROM work@company.com
        synced_from_work = make_event("s1", "Work Meeting")
        meta = {
            "srcProvider": "msgraph",
            "srcAccount": "work@company.com",
            "srcCalendar": "primary",
            "srcEventId": "AAMk1",
            "syncType": "full-detail",
            "mappingName": "work-to-hub",
            "srcHash": _hash_event(synced_from_work),
            "syncedAt": "2030-01-01T12:00:00Z",
            "version": 2,
        }
        synced_from_work = Event(
            event_id=synced_from_work.event_id,
            summary=synced_from_work.summary,
            start=synced_from_work.start,
            end=synced_from_work.end,
            description=append_syncv2("", meta),
            show_as=synced_from_work.show_as,
            raw={},
        )

        # Also a native hub event
        native_event = make_event("s2", "Personal Dinner", hours_from_now=5)

        src_provider: MagicMock = MagicMock()
        src_provider.list_events.return_value = [synced_from_work, native_event]
        tgt_provider: MagicMock = MagicMock()
        tgt_provider.list_events.return_value = []
        tgt_provider.create_event.return_value = "new_id"
        providers = {"google": src_provider, "msgraph": tgt_provider}

        result = reconcile_mapping(BUSY_MAPPING, providers, dry_run=False, max_changes=50)
        # Should only create busy block for native event, NOT for the work-synced event
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["reflected_skipped"], 1)

    def test_reflection_prevention_full_detail(self) -> None:
        """Full-detail mapping should also skip events synced FROM the target account."""
        # Source (msgraph work) has a busy block that was synced FROM personal (google hub)
        synced_from_hub = make_event("s1", "Busy")
        meta = {
            "srcProvider": "google",
            "srcAccount": "hub@gmail.com",
            "srcCalendar": "primary",
            "srcEventId": "personal1",
            "syncType": "busy-block",
            "mappingName": "personal-to-work",
            "srcHash": _hash_event(synced_from_hub),
            "syncedAt": "2030-01-01T12:00:00Z",
            "version": 2,
        }
        synced_from_hub = Event(
            event_id=synced_from_hub.event_id,
            summary=synced_from_hub.summary,
            start=synced_from_hub.start,
            end=synced_from_hub.end,
            description=append_syncv2("", meta),
            show_as=synced_from_hub.show_as,
            raw={},
        )

        # Also a real work event
        real_work_event = make_event("s2", "Team Standup", hours_from_now=5)

        providers = self._make_providers([synced_from_hub, real_work_event], [])
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        # Should only create the real work event, NOT the busy block from hub
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["reflected_skipped"], 1)

    def test_busy_block_creates_private_event(self) -> None:
        src = [make_event("s1", "Personal Dinner")]
        src_provider: MagicMock = MagicMock()
        src_provider.list_events.return_value = src
        tgt_provider: MagicMock = MagicMock()
        tgt_provider.list_events.return_value = []
        tgt_provider.create_event.return_value = "new_id"
        providers = {"google": src_provider, "msgraph": tgt_provider}

        reconcile_mapping(BUSY_MAPPING, providers, dry_run=False, max_changes=50)
        # Check the event passed to create_event
        created_event = tgt_provider.create_event.call_args[0][2]
        self.assertEqual(created_event.summary, "Personal Dinner")  # source title, not "Busy"
        self.assertEqual(created_event.visibility, "private")  # private so shared viewers see "Busy"

    def test_ignores_other_mapping_events(self) -> None:
        """Engine should not delete managed events from a different mapping."""
        meta = {
            "srcProvider": "google",
            "srcAccount": "other@example.com",
            "srcCalendar": "primary",
            "srcEventId": "other1",
            "syncType": "full-detail",
            "mappingName": "OTHER-mapping",
            "srcHash": "abc",
            "syncedAt": "2030-01-01T12:00:00Z",
            "version": 2,
        }
        other_managed = make_event("t_other", "Other Mapping Event")
        other_managed = Event(
            event_id=other_managed.event_id,
            summary=other_managed.summary,
            start=other_managed.start,
            end=other_managed.end,
            description=append_syncv2("", meta),
            show_as=other_managed.show_as,
            raw={},
        )

        providers = self._make_providers([], [other_managed])
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["deleted"], 0)

    def test_all_day_event_set_free(self) -> None:
        src = [make_event("s1", "Holiday")]
        src[0] = Event(
            event_id=src[0].event_id,
            summary=src[0].summary,
            start=src[0].start,
            end=src[0].end,
            is_all_day=True,
            raw={},
        )
        providers = self._make_providers(src, [])
        mapping = {**MAPPING, "allDayMode": "free"}
        result = reconcile_mapping(mapping, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["created"], 1)
        created = providers["msgraph"].create_event.call_args[0][2]
        self.assertEqual(created.show_as, "free")

    def test_skip_declined_events(self) -> None:
        """Declined events should not be synced."""
        accepted = make_event("s1", "Accepted Meeting")
        accepted = Event(
            event_id=accepted.event_id,
            summary=accepted.summary,
            start=accepted.start,
            end=accepted.end,
            raw={"attendees": [{"email": "me@gmail.com", "responseStatus": "accepted", "self": True}]},
        )
        declined = make_event("s2", "Declined Meeting", hours_from_now=3)
        declined = Event(
            event_id=declined.event_id,
            summary=declined.summary,
            start=declined.start,
            end=declined.end,
            raw={"attendees": [{"email": "me@gmail.com", "responseStatus": "declined", "self": True}]},
        )
        providers = self._make_providers([accepted, declined], [])
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["created"], 1)  # Only the accepted event

    def test_max_events_per_mapping(self) -> None:
        """Source events capped at maxEventsPerMapping."""
        src = [make_event(f"s{i}", f"Meeting {i}", hours_from_now=i) for i in range(10)]
        providers = self._make_providers(src, [])
        mapping = {**MAPPING, "maxEventsPerMapping": 3}
        result = reconcile_mapping(mapping, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["created"], 3)

    def test_description_change_triggers_update(self) -> None:
        """Hash includes description, so description changes trigger an update."""
        src_original = make_event("s1", "Meeting A")
        src_original = Event(
            event_id=src_original.event_id,
            summary=src_original.summary,
            start=src_original.start,
            end=src_original.end,
            description="Original agenda",
            raw={},
        )
        tgt = [make_managed_event("t1", src_original, "test-mapping", src_account="src@example.com")]
        # Source event description changed
        src_changed = make_event("s1", "Meeting A")
        src_changed = Event(
            event_id=src_changed.event_id,
            summary=src_changed.summary,
            start=src_changed.start,
            end=src_changed.end,
            description="Updated agenda with new items",
            raw={},
        )
        providers = self._make_providers([src_changed], tgt)
        result = reconcile_mapping(MAPPING, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["updated"], 1)

    def test_filtered_copy_prefix(self) -> None:
        matching = make_event("s1", "John: Pick up groceries")
        not_matching = make_event("s2", "Jane dentist")
        mapping = {
            "name": "family-filter",
            "type": "filtered-copy",
            "sources": [{"provider": "google", "account": "family@gmail.com", "calendarId": "primary"}],
            "target": {"provider": "google", "account": "hub@gmail.com", "calendarId": "family-sub"},
            "filter": {"summaryPrefix": "John:"},
            "syncMode": "full-detail",
            "lookaheadDays": 14,
            "skipDeclined": True,
            "allDayMode": "free",
        }
        src_provider: MagicMock = MagicMock()
        src_provider.list_events.return_value = [matching, not_matching]
        tgt_provider: MagicMock = MagicMock()
        tgt_provider.list_events.return_value = []
        tgt_provider.create_event.return_value = "new_id"
        # Both source and target are google — use side_effect to distinguish
        call_count = {"n": 0}

        def list_events_dispatch(*args: object, **kwargs: object) -> list[Event]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [matching, not_matching]
            return []

        src_provider.list_events.side_effect = list_events_dispatch
        providers: dict[str, MagicMock] = {"google": src_provider}

        result = reconcile_mapping(mapping, providers, dry_run=False, max_changes=50)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["filtered_out"], 1)


if __name__ == "__main__":
    unittest.main()
