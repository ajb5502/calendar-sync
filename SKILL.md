---
name: calendar-sync
version: 1.0.0
description: Cross-provider calendar synchronization (Google Calendar + Microsoft 365) using a hub-and-spoke model with SYNCV2 metadata tracking
triggers:
  - "calendar sync"
  - "sync calendars"
  - "calendar status"
  - "calendar diff"
---

# Calendar Sync

Synchronizes events across Google Calendar and Microsoft 365 using a hub-and-spoke model. One calendar (typically personal Gmail) acts as the hub. Work calendars sync inbound with full detail, outbound as privacy-respecting busy blocks. Family/shared calendars use filtered-copy with prefix matching.

## Prerequisites

Before using this skill, the following must be configured in your OpenClaw instance:

### 1. Google Calendar (via `gog` CLI)

- Install `gog` CLI: `brew install gog` (macOS) or see [gogcli docs](https://github.com/gogcli/gog)
- Authenticate each Google account: `gog auth login`
- Verify: `gog auth list` should show all accounts you want to sync

### 2. Microsoft 365 (via MS Graph API)

- Create an Azure AD app registration with `Calendars.ReadWrite` permission
- Set up a token management script that can output a valid access token
- Store credentials JSON files for each M365 account (see sample config for format)
- The default token script path is `~/.openclaw/scripts/ms-graph-auth.py`

### 3. OpenClaw Configuration

Create a config file (default: `~/.openclaw/calendar-sync.json`) with:

- **`hub`**: Your hub calendar (provider, account, calendarId)
- **`providers`**: Google accounts list + msgraph accounts with credential file paths
- **`mappings`**: One or more sync mappings (see Mapping Types below)
- **`notifications`** (optional): WhatsApp alerts via OpenClaw gateway

See `config/sample.config.json` for a complete annotated example.

### 4. OpenClaw Skills Config (openclaw.json)

Add to your `openclaw.json` under `skills.entries`:

```json
{
  "skills": {
    "entries": {
      "calendar-sync": {
        "enabled": true,
        "env": {
          "DISABLE_TELEMETRY": "1"
        }
      }
    }
  }
}
```

## Mapping Types

| Type | Direction | What syncs | Example |
|------|-----------|------------|---------|
| `full-detail` | Work → Hub | Full event details (title, description, location, attendees, meeting links) | Outlook → Gmail |
| `busy-block` | Hub → Work | Private "Busy" placeholder (hides personal details) | Gmail → Outlook |
| `filtered-copy` | Shared → Hub | Only events matching a filter (e.g., summary prefix) | Family cal → Personal sub-calendar |

## Commands

```bash
# Validate config
python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json validate

# Preview changes (dry run)
python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json reconcile --all --dry-run

# Run sync for all mappings
python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json reconcile --all

# Run sync for one mapping
python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json reconcile --mapping work-to-personal

# Preview diff for a mapping
python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json diff --mapping work-to-personal

# Check sync status and error counts
python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json status
```

## Key Features

- **SYNCV2 metadata**: Events tagged with base64-encoded metadata in descriptions for idempotent sync
- **Reflection prevention**: Busy blocks won't echo events back to their origin calendar
- **Skip declined**: Declined meeting invitations are not synced
- **All-day = free**: All-day events sync as "free" (configurable)
- **Max changes safety**: Caps mutations per run to prevent runaway sync
- **Dry run**: Preview all changes before applying
- **WhatsApp alerts**: Notifications on consecutive failures or limit hits

## Cron Setup (Recommended)

```bash
# Business hours: every 3 minutes
openclaw cron create --name "calendar-sync-business" \
  --schedule "*/3 7-20 * * * America/New_York" \
  --command "python3 <skill-path>/scripts/calendar_sync.py reconcile --all --config ~/.openclaw/calendar-sync.json"

# Off hours: every 30 minutes
openclaw cron create --name "calendar-sync-offhours" \
  --schedule "*/30 21-23,0-6 * * * America/New_York" \
  --command "python3 <skill-path>/scripts/calendar_sync.py reconcile --all --config ~/.openclaw/calendar-sync.json"

# Full daily reconcile (bypasses max changes limit)
openclaw cron create --name "calendar-sync-full" \
  --schedule "0 3 * * * America/New_York" \
  --command "python3 <skill-path>/scripts/calendar_sync.py reconcile --all --force --config ~/.openclaw/calendar-sync.json"
```

## Troubleshooting

1. Run `status` to check last sync times and error counts
2. Run `reconcile --mapping <name> --dry-run -v` to diagnose
3. Check logs: `~/.openclaw/logs/calendar-sync.log`
4. Verify Google auth: `gog auth list`
5. Verify MS tokens: `python3 ~/.openclaw/scripts/ms-graph-auth.py token --config <creds-file>`
