"""In-memory registry for connected R.I.F.T. devices.

The registry owns device identity, presence and per-device controller state.
It deliberately does not know about aiohttp routing or AWS so those concerns
can evolve independently.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any


@dataclass
class DeviceConnection:
    device_id: str
    name: str
    websocket: Any
    capabilities: list[str] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    controller_client_id: str | None = None
    pending_controller_client_id: str | None = None
    last_seen: int = field(default_factory=lambda: int(time.time()))

    def public_view(self) -> dict[str, Any]:
        return {
            **self.telemetry,
            "deviceId": self.device_id,
            "name": self.name,
            "online": not self.websocket.closed,
            "capabilities": list(self.capabilities),
            "controllerClientId": self.controller_client_id,
            "lastSeen": self.last_seen,
        }


class DeviceRegistry:
    """Tracks online agents and keeps controller state scoped to a device."""

    def __init__(self, db_path: str | None = None) -> None:
        self.devices: dict[str, DeviceConnection] = {}
        self._socket_to_device: dict[Any, str] = {}
        if db_path is None:
            state_root = os.getenv("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
            db_path = os.getenv("RIFT_REGISTRY_DB") or str(Path(state_root) / "RIFT" / "hub-devices.sqlite3")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                telemetry TEXT NOT NULL,
                last_seen INTEGER NOT NULL
            )""")
            db.commit()
            self._known = {
                row[0]: {"deviceId": row[0], "name": row[1],
                         "capabilities": json.loads(row[2]), "telemetry": json.loads(row[3]),
                         "lastSeen": row[4]}
                for row in db.execute("SELECT device_id, name, capabilities, telemetry, last_seen FROM devices")
            }

    def _save(self, device_id: str, name: str, capabilities: list[str],
              telemetry: dict[str, Any], last_seen: int) -> None:
        known = {"deviceId": device_id, "name": name, "capabilities": capabilities,
                 "telemetry": telemetry, "lastSeen": last_seen}
        self._known[device_id] = known
        with sqlite3.connect(self.db_path) as db:
            db.execute("""INSERT INTO devices(device_id, name, capabilities, telemetry, last_seen)
                VALUES(?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET
                name=excluded.name, capabilities=excluded.capabilities,
                telemetry=excluded.telemetry, last_seen=excluded.last_seen""",
                (device_id, name, json.dumps(capabilities), json.dumps(telemetry), last_seen))

    def register(self, device_id: str, websocket: Any, name: str,
                 capabilities: list[str] | None = None) -> DeviceConnection:
        device_id = str(device_id).strip()[:100]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", device_id):
            raise ValueError("deviceId must be 1-100 characters using letters, numbers, dot, underscore, or hyphen")
        current = self.devices.get(device_id)
        if current and current.websocket is not websocket and not current.websocket.closed:
            raise ValueError(f"Device '{device_id}' is already connected")
        if current and current.websocket is not websocket:
            self._socket_to_device.pop(current.websocket, None)
        known = self._known.get(device_id, {})
        capabilities = capabilities if isinstance(capabilities, list) else []
        device = DeviceConnection(
            device_id=device_id,
            name=str(name or known.get("name") or device_id)[:100],
            websocket=websocket,
            capabilities=sorted({str(cap)[:40] for cap in (capabilities or known.get("capabilities", [])) if str(cap).strip()}),
            telemetry=current.telemetry.copy() if current else dict(known.get("telemetry", {})),
        )
        self.devices[device_id] = device
        self._socket_to_device[websocket] = device_id
        self._save(device.device_id, device.name, device.capabilities, device.telemetry, device.last_seen)
        return device

    def unregister(self, websocket: Any) -> DeviceConnection | None:
        device_id = self._socket_to_device.pop(websocket, None)
        if not device_id:
            return None
        device = self.devices.get(device_id)
        # A stale socket's finally block must not disconnect its replacement.
        if device and device.websocket is websocket:
            device.last_seen = int(time.time())
            self._save(device.device_id, device.name, device.capabilities, device.telemetry, device.last_seen)
            return device
        return None

    def device_id_for(self, websocket: Any) -> str | None:
        return self._socket_to_device.get(websocket)

    def get(self, device_id: str | None) -> DeviceConnection | None:
        if not device_id:
            return None
        return self.devices.get(str(device_id))

    def online(self) -> list[DeviceConnection]:
        return [device for device in self.devices.values() if not device.websocket.closed]

    def public_devices(self) -> list[dict[str, Any]]:
        known = []
        for device_id, record in self._known.items():
            device = self.devices.get(device_id)
            if device:
                known.append(device.public_view())
            else:
                known.append({"deviceId": device_id, "name": record["name"], "online": False,
                              "capabilities": record["capabilities"], "controllerClientId": None,
                              "lastSeen": record["lastSeen"], **record["telemetry"]})
        return sorted(known,
                      key=lambda item: (not item["online"], item["name"].casefold(), item["deviceId"]))

    def update_telemetry(self, websocket: Any, telemetry: dict[str, Any]) -> DeviceConnection | None:
        device = self.get(self.device_id_for(websocket))
        if not device:
            return None
        # Keep only simple JSON values from the agent; never let telemetry
        # replace routing or controller fields.
        device.telemetry = {key: value for key, value in telemetry.items()
                            if key not in {"deviceId", "name", "capabilities", "controllerClientId", "online", "lastSeen"}
                            and isinstance(key, str)}
        device.last_seen = int(time.time())
        self._save(device.device_id, device.name, device.capabilities, device.telemetry, device.last_seen)
        return device

