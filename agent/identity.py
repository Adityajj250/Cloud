"""Stable per-install device identity for R.I.F.T. agents."""

import json
import os
import re
import socket
import uuid
from pathlib import Path


def _identity_path() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "RIFT" / "device.json"


def load_device_identity() -> tuple[str, str]:
    """Return a stable (device id, display name), honoring explicit overrides."""
    explicit_id = os.getenv("RIFT_DEVICE_ID", "").strip()
    explicit_name = os.getenv("RIFT_DEVICE_NAME", "").strip()
    path = _identity_path()
    identity: dict[str, str] = {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            identity = stored
    except (OSError, ValueError):
        pass

    hostname = socket.gethostname().strip() or "computer"
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", hostname).strip("-").lower()[:32] or "computer"
    device_id = explicit_id or identity.get("deviceId") or f"rift-{slug}-{uuid.uuid4().hex[:10]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", device_id):
        raise ValueError("RIFT_DEVICE_ID must be 1-100 characters using letters, numbers, dot, underscore, or hyphen")
    name = explicit_name or identity.get("name") or hostname

    if not explicit_id or not explicit_name:
        identity.update({"deviceId": device_id, "name": name})
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(identity, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            # An explicitly configured ID remains usable even on a read-only
            # profile; generated IDs will be regenerated next launch.
            pass
    return device_id, name

