"""Notification system — WhatsApp via OpenClaw gateway, or stdout."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

log = logging.getLogger("calendar-sync")


def send_alert(message: str, config: dict[str, Any]) -> None:
    """Send alert via configured method."""
    method = config.get("notifications", {}).get("method", "stdout")
    if method == "whatsapp":
        _send_whatsapp(message, config)
    else:
        print(f"[ALERT] {message}")


def _send_whatsapp(message: str, config: dict[str, Any]) -> None:
    """Send WhatsApp message via OpenClaw gateway."""
    to: str = config.get("notifications", {}).get("whatsapp", {}).get("to", "")
    if not to:
        log.warning("WhatsApp notification configured but no 'to' number set")
        print(f"[ALERT] {message}")
        return
    try:
        subprocess.run(
            ["openclaw", "send", "--channel", "whatsapp", "--to", to, "--message", message],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        log.exception("Failed to send WhatsApp alert")
        print(f"[ALERT] {message}")


def format_reconcile_summary(results: list[dict[str, Any]]) -> str:
    """Format reconcile results for display."""
    lines: list[str] = []
    total_c = total_u = total_d = 0
    errors: list[str] = []
    for r in results:
        if "error" in r:
            errors.append(f"  {r['mapping']}: {r['error']}")
            continue
        c, u, d = r["created"], r["updated"], r["deleted"]
        total_c += c
        total_u += u
        total_d += d
        if c + u + d > 0:
            prefix = "[DRY RUN] " if r.get("dry_run") else ""
            lines.append(f"  {prefix}{r['mapping']}: +{c} ~{u} -{d}")
        if r.get("limit_hit"):
            lines.append(f"  WARNING {r['mapping']}: max changes limit hit")
    summary = f"Calendar sync: +{total_c} ~{total_u} -{total_d}"
    if lines:
        summary += "\n" + "\n".join(lines)
    if errors:
        summary += "\nErrors:\n" + "\n".join(errors)
    return summary
