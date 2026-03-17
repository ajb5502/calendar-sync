"""Reconcile engine — the core sync logic for calendar-sync."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from providers.base import Event
from sync.metadata import append_syncv2, compute_hash, decode_syncv2, strip_syncv2


def _hash_event(event: Event) -> str:
    """Compute hash of event fields that matter for sync."""
    fields = {
        "summary": event.summary,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "location": event.location,
        "description": strip_syncv2(event.description),  # exclude SYNCV2 tag from hash
        "show_as": event.show_as,
        "is_all_day": str(event.is_all_day),
        "attendees": ",".join(sorted(event.attendees)),
        "meeting_link": event.meeting_link,
        "event_type": event.raw.get("eventType", "default"),
    }
    return compute_hash(fields)


def _matches_filter(event: Event, filt: dict[str, Any]) -> bool:
    """Check if event matches a filter config."""
    if "summaryPrefix" in filt:
        prefix: str = filt["summaryPrefix"]
        if not event.summary.startswith(prefix):
            return False
    return not ("summaryRegex" in filt and not re.search(filt["summaryRegex"], event.summary))


def _build_target_event(source: Event, mapping: dict[str, Any]) -> Event:
    """Build the event to create/update on the target calendar."""
    sync_type = mapping.get("type", "full-detail")
    if mapping.get("type") == "filtered-copy":
        sync_type = mapping.get("syncMode", "full-detail")

    if sync_type == "busy-block":
        hold = mapping.get("hold", {})
        is_task = source.raw.get("eventType") == "focusTime"
        if hold.get("labelTasks") and is_task:
            summary = f"Task: {source.summary}"
            show_as: str = source.show_as
        else:
            summary = hold.get("summary") or source.summary
            show_as = hold.get("showAs", "busy")
        return Event(
            event_id="",
            summary=summary,
            start=source.start,
            end=source.end,
            visibility=hold.get("visibility", "private"),
            show_as=show_as,
            is_all_day=source.is_all_day,
        )

    # full-detail or filtered-copy
    all_day_mode = mapping.get("allDayMode", "free")
    show_as = source.show_as
    if source.is_all_day:
        show_as = all_day_mode

    description = source.description
    if source.meeting_link and source.meeting_link not in (description or ""):
        description = f"Join: {source.meeting_link}\n---\n{description or ''}"

    color = mapping.get("color", {})
    target_provider = mapping.get("target", {}).get("provider", "")
    color_id: str = color.get(target_provider, color.get("google", ""))

    return Event(
        event_id="",
        summary=source.summary,
        start=source.start,
        end=source.end,
        description=description,
        location=source.location,
        is_all_day=source.is_all_day,
        show_as=show_as,
        attendees=source.attendees,
        meeting_link=source.meeting_link,
        color_id=color_id,
    )


def reconcile_mapping(
    mapping: dict[str, Any],
    providers: dict[str, Any],
    dry_run: bool,
    max_changes: int,
) -> dict[str, Any]:
    """Run reconcile for one mapping. Returns stats dict."""
    now_utc = datetime.now(timezone.utc)
    now = now_utc.replace(tzinfo=None)  # naive UTC — providers return naive datetimes
    lookahead = timedelta(days=mapping.get("lookaheadDays", 30))
    start = now
    end = now + lookahead

    mapping_name: str = mapping["name"]
    sync_type: str = mapping["type"]

    # Resolve source configs (single "source" or multi "sources")
    sources_config: list[dict[str, Any]] = []
    if "source" in mapping:
        sources_config = [mapping["source"]]
    elif "sources" in mapping:
        sources_config = mapping["sources"]

    max_events: int = mapping.get("maxEventsPerMapping", 500)
    source_events: list[Event] = []
    # Track source info per event_id (provider, account, calendarId)
    src_info: dict[str, tuple[str, str, str]] = {}

    for src_conf in sources_config:
        provider = providers[src_conf["provider"]]
        events: list[Event] = provider.list_events(src_conf["account"], src_conf["calendarId"], start, end)
        for e in events:
            src_info[e.event_id] = (src_conf["provider"], src_conf["account"], src_conf["calendarId"])
        source_events.extend(events)

    # Cap source events per mapping (safety valve)
    if len(source_events) > max_events:
        source_events = source_events[:max_events]

    # Skip declined events
    if mapping.get("skipDeclined", True):
        source_events = [
            e
            for e in source_events
            if e.status != "declined"
            and not any(a.get("responseStatus") == "declined" and a.get("self") for a in e.raw.get("attendees", []))
        ]

    # Apply filter for filtered-copy
    filt = mapping.get("filter")
    filtered_out = 0
    if filt:
        filtered: list[Event] = []
        for e in source_events:
            if _matches_filter(e, filt):
                filtered.append(e)
            else:
                filtered_out += 1
        source_events = filtered

    # Resolve target events
    tgt_conf: dict[str, str] = mapping["target"]
    tgt_provider = providers[tgt_conf["provider"]]
    target_events: list[Event] = tgt_provider.list_events(tgt_conf["account"], tgt_conf["calendarId"], start, end)

    # Parse SYNCV2 metadata from target events — only ours
    managed: dict[str, tuple[Event, dict[str, Any]]] = {}
    for te in target_events:
        meta = decode_syncv2(te.description)
        if meta and meta.get("mappingName") == mapping_name:
            managed[meta["srcEventId"]] = (te, meta)

    # Reflection prevention — skip source events that were synced FROM the target
    # account (prevents A→B→A echo loops). Applies to ALL mapping types.
    reflected_skipped = 0
    tgt_account = tgt_conf["account"]
    non_reflected: list[Event] = []
    for se in source_events:
        se_meta = decode_syncv2(se.description)
        if se_meta and se_meta.get("srcAccount") == tgt_account:
            reflected_skipped += 1
            continue
        non_reflected.append(se)
    source_events = non_reflected

    # Build diff
    to_create: list[tuple[Event, str]] = []
    to_update: list[tuple[Event, Event, str]] = []
    to_delete: list[Event] = []
    skipped = 0

    source_by_id = {se.event_id: se for se in source_events}

    for se in source_events:
        src_hash = _hash_event(se)
        if se.event_id in managed:
            tgt_event, meta = managed[se.event_id]
            if meta.get("srcHash") == src_hash:
                skipped += 1
            else:
                to_update.append((se, tgt_event, src_hash))
        else:
            to_create.append((se, src_hash))

    # Stale detection: managed events whose source is gone AND within lookahead
    for src_event_id, (tgt_event, _meta) in managed.items():
        tgt_end = tgt_event.end.replace(tzinfo=None) if tgt_event.end.tzinfo else tgt_event.end
        if src_event_id not in source_by_id and tgt_end >= start:
            to_delete.append(tgt_event)

    # Apply changes respecting max_changes
    total_changes = len(to_create) + len(to_update) + len(to_delete)
    limit_hit = total_changes > max_changes

    created = 0
    updated = 0
    deleted = 0
    budget = max_changes

    # Resolve default source config for metadata fallback
    default_src_conf: dict[str, str] = {}
    if "source" in mapping:
        default_src_conf = mapping["source"]
    elif sources_config:
        default_src_conf = sources_config[0]

    # Creates first
    for se, src_hash in to_create:
        if budget <= 0:
            break
        target_event = _build_target_event(se, mapping)
        info = src_info.get(se.event_id)
        meta_dict = {
            "srcProvider": info[0] if info else default_src_conf.get("provider", ""),
            "srcAccount": info[1] if info else default_src_conf.get("account", ""),
            "srcCalendar": info[2] if info else default_src_conf.get("calendarId", ""),
            "srcEventId": se.event_id,
            "syncType": sync_type if sync_type != "filtered-copy" else mapping.get("syncMode", "full-detail"),
            "mappingName": mapping_name,
            "srcHash": src_hash,
            "syncedAt": now_utc.isoformat(),
            "version": 2,
        }
        target_event.description = append_syncv2(target_event.description, meta_dict)
        if not dry_run:
            tgt_provider.create_event(tgt_conf["account"], tgt_conf["calendarId"], target_event)
        created += 1
        budget -= 1

    # Updates
    for se, tgt_event, src_hash in to_update:
        if budget <= 0:
            break
        target_event = _build_target_event(se, mapping)
        info = src_info.get(se.event_id)
        meta_dict = {
            "srcProvider": info[0] if info else default_src_conf.get("provider", ""),
            "srcAccount": info[1] if info else default_src_conf.get("account", ""),
            "srcCalendar": info[2] if info else default_src_conf.get("calendarId", ""),
            "srcEventId": se.event_id,
            "syncType": sync_type if sync_type != "filtered-copy" else mapping.get("syncMode", "full-detail"),
            "mappingName": mapping_name,
            "srcHash": src_hash,
            "syncedAt": now_utc.isoformat(),
            "version": 2,
        }
        target_event.description = append_syncv2(target_event.description, meta_dict)
        if not dry_run:
            tgt_provider.update_event(tgt_conf["account"], tgt_conf["calendarId"], tgt_event.event_id, target_event)
        updated += 1
        budget -= 1

    # Deletes
    for tgt_event in to_delete:
        if budget <= 0:
            break
        if not dry_run:
            tgt_provider.delete_event(tgt_conf["account"], tgt_conf["calendarId"], tgt_event.event_id)
        deleted += 1
        budget -= 1

    return {
        "mapping": mapping_name,
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "skipped": skipped,
        "filtered_out": filtered_out,
        "reflected_skipped": reflected_skipped,
        "limit_hit": limit_hit,
        "dry_run": dry_run,
    }
