# calendar-sync

Cross-provider calendar synchronization skill for [OpenClaw](https://openclaw.ai). Syncs events between Google Calendar and Microsoft 365 using a hub-and-spoke model.

## What It Does

- **Inbound sync** (full-detail): Work calendars → personal hub with full event details, color-coded by source
- **Outbound sync** (busy-block): Personal hub → work calendars with private "Busy" placeholders
- **Filtered copy**: Shared/family calendars → personal sub-calendar, filtered by event prefix
- **Reflection prevention**: Busy blocks never echo events back to their origin
- **SYNCV2 metadata**: Idempotent sync via base64-encoded tags in event descriptions

## Prerequisites

| Dependency | Purpose | Install |
|-----------|---------|---------|
| Python 3.9+ | Runtime | Pre-installed on macOS |
| [OpenClaw](https://openclaw.ai) | Agent framework | See docs |
| [`gog` CLI](https://github.com/gogcli/gog) | Google Calendar access | `brew install gog` |
| MS Graph token script | Microsoft 365 access | See [setup guide](#microsoft-365-setup) |

## Quick Start

1. **Install the skill:**
   ```bash
   npx clawhub install calendar-sync
   ```
   Or clone this repo into `~/.openclaw/skills/calendar-sync/`

2. **Copy and edit the sample config:**
   ```bash
   cp ~/.openclaw/skills/calendar-sync/config/sample.config.json ~/.openclaw/calendar-sync.json
   chmod 600 ~/.openclaw/calendar-sync.json
   # Edit with your accounts, calendar IDs, and credential paths
   ```

3. **Authenticate your accounts:**
   ```bash
   # Google (repeat for each account)
   gog auth login

   # Microsoft 365 (see ms-graph-auth.py setup)
   python3 ~/.openclaw/scripts/ms-graph-auth.py login --config ~/.openclaw/credentials/your-azure.json
   ```

4. **Validate and dry-run:**
   ```bash
   cd ~/.openclaw/skills/calendar-sync
   PYTHONPATH=scripts python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json validate
   PYTHONPATH=scripts python3 scripts/calendar_sync.py --config ~/.openclaw/calendar-sync.json reconcile --all --dry-run
   ```

5. **Set up cron jobs** (see SKILL.md for recommended schedules)

## OpenClaw Config Requirements

Your `~/.openclaw/calendar-sync.json` must include:

| Section | Required | Description |
|---------|----------|-------------|
| `hub` | Yes | Your hub calendar (provider, account, calendarId) |
| `providers.google` | If using Google | List of Google account emails + gog binary path |
| `providers.msgraph` | If using M365 | List of M365 accounts with credential file paths |
| `mappings` | Yes | One or more sync mapping definitions |
| `safety` | No | Max changes per run (default: 250), dry-run mode. **Note:** `maxEventsPerMapping` must be set on each mapping — the engine does not fall back to the global value |
| `notifications` | No | WhatsApp alerts via OpenClaw gateway |
| `state` | No | State file path (default: `~/.openclaw/calendar-sync-state.json`) |

See [`config/sample.config.json`](config/sample.config.json) for a complete annotated example.

## Microsoft 365 Setup

1. Register an app in [Azure AD](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Add `Calendars.ReadWrite` delegated permission
3. Create a client secret
4. Save credentials as JSON:
   ```json
   {
     "client_id": "your-client-id",
     "client_secret": "your-client-secret",
     "tenant_id": "your-tenant-id",
     "redirect_uri": "http://localhost:8400/callback"
   }
   ```
5. Run initial login: `python3 ms-graph-auth.py login --config <creds-file>`
6. Set up token refresh cron (tokens expire in ~1 hour)

## Development

```bash
# Install dev dependencies
pip3 install pytest pytest-subprocess syrupy ruff mypy

# Run full QA
make qa        # ruff check + format + mypy strict + pytest

# Run just tests
make test

# Run just linting
make lint
```

## Architecture

```
Personal Calendar (hub)
    ↑ full-detail              ↓ busy-block
Work Outlook A ─────→ Hub ─────→ Work Outlook A
Work Outlook B ────→ Hub ─────→ Work Outlook B
Family Calendar ───→ Hub (filtered-copy, prefix match)
```

## Known Issues & Lessons Learned

These were discovered during production deployment and are now fixed:

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| **MS Graph 400 on calendarView** | `datetime.isoformat()` on UTC-aware datetime produces `+00:00`, not `Z` | Use `strftime("%Y-%m-%dT%H:%M:%SZ")` |
| **gog 400 on all-day events** | All-day events need `--from`/`--to` in `YYYY-MM-DD` format, not datetime | Check `is_all_day` and use date-only format |
| **SYNCV2 tag lost on long descriptions** | Google Calendar truncates descriptions at ~8192 chars; HTML from MS Graph is huge | Prepend SYNCV2 tag instead of appending |
| **Echo loops (A→B→A duplication)** | Reflection prevention only applied to busy-block mappings | Apply to all mapping types |
| **Tilde paths not expanding** | `subprocess.run` doesn't expand `~` in arguments | `os.path.expanduser()` on all config paths |
| **Naive vs aware datetime comparison** | gog returns naive datetimes, engine uses aware `datetime.now(timezone.utc)` | Normalize to naive UTC in engine |
| **Sub-calendars missing from outbound sync** | Single `source` only reads one `calendarId`; Google sub-calendars are separate from `primary` | Use `sources` (array) to include multiple calendars per mapping |
| **maxEventsPerMapping ignored from global config** | Engine reads `maxEventsPerMapping` from mapping dict only, hardcodes 500 default | Set `maxEventsPerMapping` explicitly on each mapping (code fix pending) |
| **maxChangesPerRun too conservative** | Default of 50 causes multi-cycle delays when adding new source calendars or running initial sync | Bump to 250 for production; use 50 only during initial testing |

### Tips for Deployers

- **Disconnect existing sync tools first** (e.g., Calendar Bridge) before running initial sync — overlapping sync tools create duplicates
- **Run dry-run before live sync** to verify event counts look reasonable
- **Check idempotency** after first sync: run `reconcile --all --dry-run` — should show `+0 ~0 -0`
- **Set cron timeout to 120s+** — the agent needs time to spawn, load context, and run the script
- **All-day events from MS Graph** come with `00:00:00` times — the provider handles format conversion automatically

### Multi-Source Mappings

If your outbound (busy-block) mappings need to include events from sub-calendars — e.g., a family calendar alongside your primary — use `sources` instead of `source`:

```json
{
  "name": "personal-to-work",
  "type": "busy-block",
  "sources": [
    { "provider": "google", "account": "you@gmail.com", "calendarId": "primary" },
    { "provider": "google", "account": "you@gmail.com", "calendarId": "your-subcal-id@group.calendar.google.com" }
  ],
  "target": { "provider": "msgraph", "account": "you@company.com", "calendarId": "primary" },
  "hold": { "visibility": "private", "showAs": "busy" }
}
```

Without this, events on Google sub-calendars (filtered-copy targets, shared calendars, etc.) will **not** appear as busy blocks on your work calendar.

### Auth for Headless / Cron Environments

Never use macOS Keychain for authentication in cron or headless contexts — the keychain locks without a GUI session and silently fails. Use file-based token storage instead:

- **gog CLI**: Set keyring backend to `file` and provide `GOG_KEYRING_PASSWORD` via env var
- **MS Graph**: Store tokens as JSON files, refresh via cron (tokens expire in ~1 hour)
- **Cron wrapper**: Source `~/.openclaw/.env` to inject env vars for all cron jobs

### Safety Tuning

| Setting | Initial Testing | Production |
|---------|----------------|------------|
| `maxChangesPerRun` | 25–50 | 250 |
| `maxEventsPerMapping` | 500 | 2000 |
| `lookaheadDays` | 30 | 365 |
| `dryRun` | `true` | `false` |

Start conservative, bump after verifying idempotency (`diff` shows `+0 ~0 -0`).

## License

MIT — see [LICENSE](LICENSE)
