"""CLI command router for calendar-sync."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from notify.alerts import format_reconcile_summary, send_alert
from state import SyncState
from sync.engine import reconcile_mapping
from sync.rules import apply_defaults, get_mapping, load_config, validate_config

DEFAULT_CONFIG = "~/.openclaw/calendar-sync.json"

log = logging.getLogger("calendar-sync")


def _init_providers(config: dict[str, Any]) -> dict[str, Any]:
    """Initialize provider instances from config."""
    providers: dict[str, Any] = {}
    prov_conf = config.get("providers", {})

    if "google" in prov_conf:
        from providers.google import GoogleProvider

        providers["google"] = GoogleProvider(gog_binary=prov_conf["google"].get("gogBinary", "gog"))

    if "msgraph" in prov_conf:
        from providers.msgraph import MSGraphProvider

        mp = MSGraphProvider(
            token_script=prov_conf["msgraph"].get("tokenScript", "~/.openclaw/scripts/ms-graph-auth.py"),
            max_retries=config.get("retry", {}).get("maxRetries", 3),
            backoff=config.get("retry", {}).get("backoffSeconds", [2, 4, 8]),
        )
        creds_map: dict[str, str] = {}
        for acct in prov_conf["msgraph"].get("accounts", []):
            if isinstance(acct, dict):
                creds_map[acct["email"]] = acct["credentialsFile"]
        mp.set_credentials_map(creds_map)
        providers["msgraph"] = mp

    return providers


def cmd_validate(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    errors = validate_config(config)
    if errors:
        print("Config validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"Config valid: {len(config.get('mappings', []))} mappings defined.")


def cmd_reconcile(args: argparse.Namespace) -> None:
    config = apply_defaults(load_config(args.config))
    errors = validate_config(config)
    if errors:
        print("Config invalid:", errors[0])
        sys.exit(1)

    providers = _init_providers(config)
    state = SyncState(config.get("state", {}).get("file", "~/.openclaw/calendar-sync-state.json"))
    dry_run: bool = args.dry_run or config.get("safety", {}).get("dryRun", False)
    max_changes: int = config["safety"]["maxChangesPerRun"]
    if args.force:
        max_changes = 999999

    mappings = config["mappings"]
    if args.mapping:
        m = get_mapping(config, args.mapping)
        if not m:
            print(f"Mapping '{args.mapping}' not found.")
            sys.exit(1)
        mappings = [m]
    elif not args.all:
        print("Specify --mapping <name> or --all")
        sys.exit(1)

    alert_threshold: int = config.get("notifications", {}).get("alertAfterConsecutiveFailures", 3)
    results: list[dict[str, Any]] = []

    for mapping in mappings:
        name = mapping["name"]
        try:
            result = reconcile_mapping(mapping, providers, dry_run=dry_run, max_changes=max_changes)
            results.append(result)
            if not dry_run:
                state.record_sync(
                    name,
                    datetime.now(timezone.utc).isoformat(),
                    created=result["created"],
                    updated=result["updated"],
                    deleted=result["deleted"],
                )
        except Exception as exc:
            log.error("Mapping '%s' failed: %s", name, exc)
            results.append({"mapping": name, "error": str(exc)})
            if not dry_run:
                state.record_error(name, str(exc))
                if state.get_error_count(name) >= alert_threshold:
                    send_alert(
                        f"Calendar sync alert: mapping '{name}' failed {state.get_error_count(name)} times.\n"
                        f"Error: {exc}\nRun 'calendar-sync status' for details.",
                        config,
                    )

    if not dry_run:
        state.save()

    summary = format_reconcile_summary(results)
    print(summary)

    any_limit = any(r.get("limit_hit") for r in results if "error" not in r)
    if any_limit:
        send_alert(f"Calendar sync: max changes limit hit.\n{summary}", config)


def cmd_status(args: argparse.Namespace) -> None:
    config = apply_defaults(load_config(args.config))
    state = SyncState(config.get("state", {}).get("file", "~/.openclaw/calendar-sync-state.json"))
    mappings = config["mappings"]
    if args.mapping:
        m = get_mapping(config, args.mapping)
        if not m:
            print(f"Mapping '{args.mapping}' not found.")
            sys.exit(1)
        mappings = [m]

    for m in mappings:
        name = m["name"]
        last = state.get_last_sync(name)
        errs = state.get_error_count(name)
        if last:
            print(f"{name}: last sync {last['syncedAt']} (+{last['created']} ~{last['updated']} -{last['deleted']})")
        else:
            print(f"{name}: never synced")
        if errs > 0:
            print(f"  WARNING {errs} consecutive errors")


def cmd_diff(args: argparse.Namespace) -> None:
    """Preview changes for a mapping without applying."""
    config = apply_defaults(load_config(args.config))
    errors = validate_config(config)
    if errors:
        print("Config invalid:", errors[0])
        sys.exit(1)

    if not args.mapping:
        print("--mapping is required for diff")
        sys.exit(1)

    m = get_mapping(config, args.mapping)
    if not m:
        print(f"Mapping '{args.mapping}' not found.")
        sys.exit(1)

    providers = _init_providers(config)
    result = reconcile_mapping(m, providers, dry_run=True, max_changes=999999)
    print(f"Diff for '{args.mapping}':")
    print(f"  Would create: {result['created']}")
    print(f"  Would update: {result['updated']}")
    print(f"  Would delete: {result['deleted']}")
    print(f"  Unchanged:    {result['skipped']}")
    if result.get("reflected_skipped"):
        print(f"  Reflections skipped: {result['reflected_skipped']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="calendar-sync", description="Cross-provider calendar synchronization")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("validate", help="Validate config file")

    reconcile = sub.add_parser("reconcile", help="Run sync")
    reconcile.add_argument("--mapping", help="Single mapping name")
    reconcile.add_argument("--all", action="store_true", help="All mappings")
    reconcile.add_argument("--dry-run", action="store_true")
    reconcile.add_argument("--force", action="store_true", help="Bypass maxChangesPerRun")

    status = sub.add_parser("status", help="Show sync status")
    status.add_argument("--mapping", help="Single mapping name")

    diff_parser = sub.add_parser("diff", help="Preview changes")
    diff_parser.add_argument("--mapping", required=True)

    sub.add_parser("setup", help="Interactive setup wizard")
    sub.add_parser("list-mappings", help="List configured mappings")
    sub.add_parser("add-mapping", help="Add a new mapping interactively")
    rm = sub.add_parser("remove-mapping", help="Remove a mapping")
    rm.add_argument("--name", required=True)

    args = parser.parse_args()

    # File logging (always) + console logging (if verbose)
    log_dir = os.path.expanduser("~/.openclaw/logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, "calendar-sync.log"))
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(file_handler)
    if args.verbose:
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG)
        console.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        root.addHandler(console)
        root.setLevel(logging.DEBUG)
    else:
        root.setLevel(logging.WARNING)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands: dict[str, Any] = {
        "validate": cmd_validate,
        "reconcile": cmd_reconcile,
        "status": cmd_status,
        "diff": cmd_diff,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        print(f"Command '{args.command}' not yet implemented.")
        sys.exit(1)


if __name__ == "__main__":
    main()
