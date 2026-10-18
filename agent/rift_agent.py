"""
R.I.F.T. Windows Worker Agent (Main Daemon)
Runs on Windows CPU machines across separate LANs.
Connects outbound to AWS IoT Core over TLS port 443.
Pulls compute chunks from SQS, executes allowed commands, reports heartbeats.
"""

import os
import sys
import time
import json
import ctypes
from ctypes import wintypes

def enable_capture_dpi_awareness():
    """Make GetCursorPos / monitor rects / ImageGrab all use physical pixels.
    Must run before any library creates a window or sets its own DPI mode."""
    if os.name != "nt":
        return "n/a"
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return "per-monitor"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unchanged (already set by another library, or unavailable)"


DPI_MODE = enable_capture_dpi_awareness()

# Which display is streamed to the phone:
#   follow  (default) - the monitor the cursor is currently on, so the arrow is always visible
#   all               - the whole virtual desktop (every monitor in one frame)
#   primary           - primary monitor only (old behaviour)
CAPTURE_MODE = os.getenv("RIFT_CAPTURE_MODE", "follow").strip().lower()
import asyncio
import base64
import io
import logging
import psutil
from PIL import ImageGrab

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import threading
import boto3
from decimal import Decimal
from typing import Dict, Any

from config import AgentConfig
from comms.mqtt_client import RiftMqttClient
from security.verifier import CommandVerifier
from workers.math_worker import MathWorker
from workers.image_worker import ImageWorker
from workers.llm_worker import LLMWorker
from vision.detector import VisionWorker
from files_service import FilesService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("rift.agent")


class _KeybdInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeybdInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ii", _InputUnion)]




def to_decimal(obj):
    """Recursively convert float → Decimal for DynamoDB writes."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(v) for v in obj]
    return obj


class RiftAgent:
    def __init__(self, config: AgentConfig = None):
        self.config = config or AgentConfig()
        logger.info(f"[INIT] Device ID : {self.config.device_id}")
        logger.info(f"[INIT] IoT endpoint: {self.config.iot_endpoint}")
        logger.info(f"[INIT] SQS queue  : {self.config.sqs_queue_url}")
        logger.info(f"[INIT] S3 bucket  : {self.config.s3_bucket}")
        logger.info(f"[INIT] LLM model  : {self.config.default_llm_model}")

        self.verifier = CommandVerifier(
            secret_key=self.config.command_hmac_secret,
            allowed_commands=self.config.allowed_commands
        )
        self.llm_worker = LLMWorker(
            base_url=self.config.ollama_url,
            default_model=self.config.default_llm_model
        )
        self.vision_worker = VisionWorker()
        try:
            self.files_service = FilesService()
            logger.info("[FILES] Shared folder: %s", self.files_service.shared_root)
            logger.info("[FILES] Uploads and downloads use the shared folder: %s", self.files_service.shared_root)
        except Exception as files_error:
            self.files_service = None
            logger.exception("[FILES] File service could not start: %s", files_error)

        # AWS clients (uses default credential chain — run `aws login --profile rift-admin` first)
        try:
            session = boto3.Session(profile_name=self.config.aws_profile, region_name=self.config.aws_region)
            self.dynamo    = session.resource("dynamodb", region_name=self.config.aws_region)
            self.sqs_cli   = session.client("sqs", region_name=self.config.aws_region)
            self.s3_cli    = session.client("s3", region_name=self.config.aws_region)
            self.devices_tbl = self.dynamo.Table("rift-devices")
            self.jobs_tbl    = self.dynamo.Table("rift-jobs")
            self.chunks_tbl  = self.dynamo.Table("rift-chunks")
            logger.info("[INIT] ✓ AWS boto3 session ready (profile=%s)", self.config.aws_profile)
        except Exception as e:
            logger.error(f"[INIT] ✗ AWS session failed: {e}")
            logger.error("[INIT]   → Sign in with: aws login --region ap-southeast-2 --profile %s", self.config.aws_profile)
            self.dynamo = self.sqs_cli = self.s3_cli = None
            self.devices_tbl = self.jobs_tbl = self.chunks_tbl = None

        self.running = False
        self.control_client_id = None
        self.control_held_keys = set()
        self.capture_rect = None  # (left, top, right, bottom) in virtual-desktop pixels
        self.cmd_topic    = f"rift/{self.config.device_id}/cmd"
        self.status_topic = f"rift/{self.config.device_id}/status"
        self.result_topic = f"rift/{self.config.device_id}/result"

        self.mqtt = RiftMqttClient(
            endpoint=self.config.iot_endpoint,
            cert_path=self.config.cert_path,
            key_path=self.config.key_path,
            root_ca_path=self.config.root_ca_path,
            client_id=self.config.device_id,
            status_topic=self.status_topic,
            on_message_callback=self._handle_mqtt_message
        )

    # ─── Telemetry ────────────────────────────────────────────────────────────
    def get_system_telemetry(self) -> Dict[str, Any]:
        """Collects Windows CPU, RAM, and system state via psutil."""
        cpu_pct = psutil.cpu_percent(interval=None)
        mem     = psutil.virtual_memory()

        battery_info = {}
        try:
            battery = psutil.sensors_battery()
            if battery:
                battery_info = {
                    "percent":      battery.percent,
                    "power_plugged": battery.power_plugged
                }
        except Exception:
            pass

        return {
            "deviceId":    self.config.device_id,
            "deviceName":  self.config.device_name,
            "status":      "online",
            "cpu_percent": cpu_pct,
            "cpu_cores":   psutil.cpu_count(logical=True),
            "ram_used_mb": round(mem.used   / (1024 * 1024), 1),
            "ram_free_mb": round(mem.available / (1024 * 1024), 1),
            "ram_percent": mem.percent,
            "battery":     battery_info,
            "llm_loaded":  self.llm_worker.is_available(),
            "timestamp":   int(time.time())
        }

    # ─── Startup ──────────────────────────────────────────────────────────────
    def start(self):
        logger.info(f"[START] R.I.F.T. Agent starting on Windows [{self.config.device_id}]…")
        self.running = True

        self.control_thread = threading.Thread(target=self._control_relay_thread, daemon=True)
        self.control_thread.start()

        # Connect to AWS IoT Core via MQTT over TLS port 443
        logger.info("[START] Connecting to AWS IoT Core (MQTT/TLS port 443)…")
        if not self.mqtt.connect():
            logger.error("[START] ✗ MQTT connect failed. Will retry in background.")
        else:
            logger.info("[START] ✓ MQTT connected to IoT Core")

        # Subscribe to command topic and vision input
        self.mqtt.subscribe(self.cmd_topic)
        logger.info(f"[START] ✓ Subscribed to {self.cmd_topic}")
        self.mqtt.subscribe(f"rift/{self.config.device_id}/vision_in")
        logger.info(f"[START] ✓ Subscribed to rift/{self.config.device_id}/vision_in")

        # Background threads
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        logger.info("[START] ✓ Heartbeat thread started")

        self.worker_thread = threading.Thread(target=self._sqs_worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info("[START] ✓ SQS worker thread started")

        logger.info("[START] ═══ R.I.F.T. Agent is LIVE and listening ═══")

    def _control_relay_thread(self):
        """Maintains a loopback-only WebSocket used for consented remote screen control."""
        asyncio.run(self._control_relay_loop())

    async def _control_relay_loop(self):
        import aiohttp
        from urllib.parse import urlsplit, urlunsplit

        parsed = urlsplit(self.config.server_url)
        scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(parsed.scheme)
        if not scheme or not parsed.netloc:
            raise ValueError("RIFT_SERVER_URL must be an http(s) or ws(s) URL with a host")
        url = urlunsplit((scheme, parsed.netloc, parsed.path.rstrip("/") + "/ws", "", ""))
        while self.running:
            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=3)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(url, heartbeat=15,
                                                  max_msg_size=2 * 1024 * 1024) as ws:
                        capabilities = ["telemetry", "compute", "vision"]
                        if self.files_service:
                            capabilities.append("files")
                        if os.name == "nt":
                            capabilities.append("screen")
                        if self.llm_worker.is_available():
                            capabilities.append("llm")
                        await ws.send_json({"type": "identify", "role": "agent", "clientId": self.config.device_id,
                                            "deviceId": self.config.device_id, "name": self.config.device_name,
                                            "capabilities": capabilities})
                        await ws.send_json({"type": "control_ready"})
                        logger.info("[HUB] Connected to %s as %s", url, self.config.device_id)
                        last_presence_sent = 0.0
                        last_frame = 0.0
                        last_cursor, last_cursor_sent, size_warned = None, 0.0, False
                        self.capture_rect = None
                        logger.info("[CONTROL] DPI mode: %s | capture mode: %s", DPI_MODE, CAPTURE_MODE)
                        while self.running and not ws.closed:
                            now = time.monotonic()
                            if now - last_presence_sent >= 30.0:
                                try:
                                    await ws.send_json({"type": "device_telemetry", "telemetry": self.get_system_telemetry()})
                                    last_presence_sent = now
                                except Exception as telemetry_error:
                                    logger.debug("[HUB] Telemetry send failed: %s", telemetry_error)
                            try:
                                msg = await asyncio.wait_for(ws.receive(), timeout=0.03)
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    data = json.loads(msg.data)
                                    kind = data.get("type")
                                    if kind == "device_error":
                                        logger.error("[HUB] Device registration failed: %s", data.get("message", "unknown hub error"))
                                    elif kind and kind.startswith("files_") and kind.endswith("_request") and self.files_service:
                                        await self._handle_files_request(ws, data)
                                    elif kind == "control_request":
                                        client_id = str(data.get("clientId", ""))[:80]
                                        available = self.control_client_id in (None, client_id)
                                        ok = bool(client_id) and available
                                        if ok:
                                            self.control_client_id = client_id
                                            logger.info("[CONTROL] Computer claimed by %s", client_id)
                                        message = ("Computer claimed. You can control it until you drop it." if ok else
                                            "Another phone currently controls this computer.")
                                        await ws.send_json({"type": "control_status", "clientId": client_id, "ok": ok,
                                            "message": message})
                                    elif kind == "control_stop":
                                        if data.get("clientId") == self.control_client_id:
                                            logger.info("[CONTROL] Screen control stopped")
                                            self._release_control_keys()
                                            self.control_client_id = None
                                    elif kind == "control_input" and self.control_client_id:
                                        self._apply_control_input(data.get("event", {}))
                                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                            except asyncio.TimeoutError:
                                pass

                            # ── Live cursor stream (~30 Hz, tiny messages) ──
                            if self.control_client_id and os.name == "nt":
                                try:
                                    point = wintypes.POINT()
                                    if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                                        rect = self._capture_rect(point)
                                        if rect != self.capture_rect:
                                            self.capture_rect = rect
                                            last_frame = 0.0  # cursor changed display: send its frame right away
                                        cx, cy = self._normalise_cursor(point, rect)
                                        now = time.monotonic()
                                        if (cx, cy) != last_cursor or now - last_cursor_sent >= 0.5:
                                            last_cursor, last_cursor_sent = (cx, cy), now
                                            await ws.send_json({"type": "control_cursor", "cursorX": cx,
                                                                "cursorY": cy, "mon": list(rect)})
                                except Exception as cursor_error:
                                    logger.debug("[CONTROL] Cursor read failed: %s", cursor_error)

                            # ── Screen frames (~5 fps) ──
                            if self.control_client_id and time.monotonic() - last_frame >= 0.2:
                                last_frame = time.monotonic()
                                try:
                                    rect = self.capture_rect or self._capture_rect(None)
                                    image = ImageGrab.grab(bbox=rect, all_screens=True)
                                    point = wintypes.POINT()
                                    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                                    cursor_x, cursor_y = self._normalise_cursor(point, rect)
                                    expected = (rect[2] - rect[0], rect[3] - rect[1])
                                    if image.size != expected and not size_warned:
                                        size_warned = True
                                        logger.warning("[CONTROL] Captured image is %s but display rect is %s (DPI mode: %s). "
                                                       "Cursor may be offset; check Windows display scaling.",
                                                       image.size, expected, DPI_MODE)
                                    image.thumbnail((1280, 720))
                                    buffer = io.BytesIO()
                                    image.convert("RGB").save(buffer, format="JPEG", quality=72, optimize=True)
                                    await ws.send_json({"type": "control_frame", "width": image.width, "height": image.height,
                                        "cursorX": cursor_x, "cursorY": cursor_y, "mon": list(rect),
                                        "frame": base64.b64encode(buffer.getvalue()).decode("ascii")})
                                except Exception as capture_error:
                                    logger.warning("[CONTROL] Screen capture failed: %s", capture_error)
                                    await ws.send_json({"type": "control_status", "clientId": self.control_client_id, "ok": False,
                                        "message": f"Screen capture failed: {capture_error}"})
                                    self._release_control_keys()
                                    self.control_client_id = None
                        if self.control_client_id:
                            # Never leave a game key stuck if the server relay drops.
                            self._release_control_keys()
                            self.control_client_id = None
                if self.running:
                    await asyncio.sleep(2)
            except Exception as e:
                if self.running:
                    self._release_control_keys()
                    self.control_client_id = None
                    logger.debug("[CONTROL] Local screen relay reconnecting: %s", e)
                    await asyncio.sleep(2)

    async def _handle_files_request(self, ws, data):
        """Handle a bounded hub file request and return its correlated result."""
        kind = data.get("type", "")
        request_id = str(data.get("requestId", ""))
        op = kind.removeprefix("files_").removesuffix("_request")
        response_type = f"files_{op}_result"
        try:
            service = self.files_service
            if op == "list":
                result = await asyncio.to_thread(service.list_entries, str(data.get("pathId", "shared-root")),
                    200, int(data.get("offset", 0)))
            elif op == "stat":
                result = await asyncio.to_thread(service.stat_file, str(data.get("fileId", "")))
            elif op == "read":
                payload = await asyncio.to_thread(service.read_file, str(data.get("fileId", "")),
                    int(data.get("offset", 0)), int(data.get("length", 0)))
                result = {"data": base64.b64encode(payload).decode("ascii"), "length": len(payload)}
            elif op == "upload_start":
                result = await asyncio.to_thread(service.start_upload, str(data.get("uploadId", "")),
                    str(data.get("name", "upload")), int(data.get("size", -1)))
            elif op == "upload_chunk":
                chunk = base64.b64decode(str(data.get("data", "")), validate=True)
                result = await asyncio.to_thread(service.write_upload_chunk, str(data.get("uploadId", "")),
                    int(data.get("offset", -1)), chunk)
            elif op == "upload_finish":
                result = await asyncio.to_thread(service.finish_upload, str(data.get("uploadId", "")))
            else:
                raise ValueError("Unsupported files operation")
            await ws.send_json({"type": response_type, "requestId": request_id, "ok": True, **result})
        except Exception as error:
            logger.warning("[FILES] %s failed: %s", op, error)
            await ws.send_json({"type": response_type, "requestId": request_id,
                                "ok": False, "error": str(error)[:300]})

    def _capture_rect(self, cursor=None):
        """Rectangle (virtual-desktop pixels) that is streamed to the phone."""
        user32 = ctypes.windll.user32
        if CAPTURE_MODE == "all":
            left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
            width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
            if width > 0 and height > 0:
                return (left, top, left + width, top + height)
        elif CAPTURE_MODE == "follow" and cursor is not None:
            try:
                user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
                user32.MonitorFromPoint.restype = ctypes.c_void_p
                user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]
                hmon = user32.MonitorFromPoint(cursor, 2)  # MONITOR_DEFAULTTONEAREST
                info = _MonitorInfo()
                info.cbSize = ctypes.sizeof(_MonitorInfo)
                if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                    r = info.rcMonitor
                    if r.right > r.left and r.bottom > r.top:
                        return (r.left, r.top, r.right, r.bottom)
            except Exception:
                pass
        return (0, 0, max(1, user32.GetSystemMetrics(0)), max(1, user32.GetSystemMetrics(1)))

    @staticmethod
    def _normalise_cursor(cursor, rect):
        """Cursor as 0..1 inside the streamed rectangle (inverse of the input mapping)."""
        left, top, right, bottom = rect
        span_x, span_y = max(1, right - left - 1), max(1, bottom - top - 1)
        return (round(max(0.0, min(1.0, (cursor.x - left) / span_x)), 5),
                round(max(0.0, min(1.0, (cursor.y - top) / span_y)), 5))

    def _apply_control_input(self, event: Dict[str, Any]):
        """Apply narrowly shaped mouse and keyboard events from the paired phone."""
        if os.name != "nt":
            return
        kind = event.get("type")
        user32 = ctypes.windll.user32
        left, top, right, bottom = self.capture_rect or self._capture_rect(None)
        def position(payload):
            x = max(0.0, min(1.0, float(payload.get("x", 0))))
            y = max(0.0, min(1.0, float(payload.get("y", 0))))
            user32.SetCursorPos(left + round(x * max(0, right - left - 1)),
                                top + round(y * max(0, bottom - top - 1)))
        try:
            if kind == "move":
                position(event)
            elif kind == "click":
                position(event)
                button = event.get("button", "left")
                flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}.get(button)
                if flags:
                    user32.mouse_event(flags[0], 0, 0, 0, 0)
                    user32.mouse_event(flags[1], 0, 0, 0, 0)
            elif kind == "down":
                position(event)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
            elif kind == "up":
                position(event)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
            elif kind == "key":
                key = str(event.get("key", ""))[:20].lower()
                virtual_keys = {"enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
                    "esc": 0x1B, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
                    "↑": 0x26, "↓": 0x28, "←": 0x25, "→": 0x27,
                    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
                    "ctrl": 0x11, "alt": 0x12, "shift": 0x10}
                vk = virtual_keys.get(key)
                if vk is None and len(key) == 1 and key.isascii() and key.isalnum():
                    vk = ord(key.upper())
                if vk is not None:
                    user32.keybd_event(vk, 0, 0, 0)
                    user32.keybd_event(vk, 0, 0x0002, 0)
            elif kind in ("keydown", "keyup"):
                key = str(event.get("key", ""))[:20].lower()
                virtual_keys = {"enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08,
                    "delete": 0x2E, "esc": 0x1B, "up": 0x26, "down": 0x28,
                    "left": 0x25, "right": 0x27, "shift": 0x10, "ctrl": 0x11, "alt": 0x12}
                vk = virtual_keys.get(key)
                if vk is None and len(key) == 1 and key.isascii() and key.isalnum():
                    vk = ord(key.upper())
                if vk is not None:
                    if kind == "keydown":
                        if vk not in self.control_held_keys:
                            self._send_scan_code(vk, key_up=False)
                            self.control_held_keys.add(vk)
                    elif vk in self.control_held_keys:
                        self._send_scan_code(vk, key_up=True)
                        self.control_held_keys.discard(vk)
            elif kind == "hotkey":
                names = event.get("keys", [])
                if not isinstance(names, list) or not 1 <= len(names) <= 3:
                    return
                virtual_keys = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "tab": 0x09, "enter": 0x0D,
                    "esc": 0x1B, "space": 0x20, "delete": 0x2E, "left": 0x25, "right": 0x27,
                    "up": 0x26, "down": 0x28}
                keys = []
                for name in names:
                    key = str(name).lower()[:10]
                    vk = virtual_keys.get(key)
                    if vk is None and len(key) == 1 and key.isascii() and key.isalnum():
                        vk = ord(key.upper())
                    if vk is None:
                        return
                    keys.append(vk)
                for vk in keys:
                    user32.keybd_event(vk, 0, 0, 0)
                for vk in reversed(keys):
                    user32.keybd_event(vk, 0, 0x0002, 0)
            elif kind == "text":
                value = str(event.get("text", ""))[:160]
                encoded = value.encode("utf-16-le", errors="replace")
                for offset in range(0, len(encoded), 2):
                    unit = int.from_bytes(encoded[offset:offset + 2], "little")
                    user32.keybd_event(0, unit, 0x0004, 0)
                    user32.keybd_event(0, unit, 0x0004 | 0x0002, 0)
        except Exception as e:
            logger.debug("[CONTROL] Ignored input event: %s", e)

    @staticmethod
    def _send_scan_code(vk: int, key_up: bool):
        """Send hardware-style scan-code events for games that ignore plain VK injection."""
        user32 = ctypes.windll.user32
        scan = user32.MapVirtualKeyW(vk, 4)  # MAPVK_VK_TO_VSC_EX
        flags = 0x0008  # KEYEVENTF_SCANCODE
        if (scan >> 8) == 0xE0 or vk in {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}:
            flags |= 0x0001  # KEYEVENTF_EXTENDEDKEY
        if key_up:
            flags |= 0x0002  # KEYEVENTF_KEYUP
        scan &= 0xFF
        packet = _Input(type=1, ii=_InputUnion(ki=_KeybdInput(0, scan, flags, 0, 0)))
        sent = user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(_Input))
        if sent != 1:
            # Preserve basic keyboard control on systems where scan-code SendInput
            # is unavailable; elevated games still require an equally elevated agent.
            logger.debug("[CONTROL] SendInput scan-code injection failed for VK %#x", vk)
            user32.keybd_event(vk, 0, 0x0002 if key_up else 0, 0)

    def _release_control_keys(self):
        if os.name != "nt":
            self.control_held_keys.clear()
            return
        user32 = ctypes.windll.user32
        for vk in tuple(self.control_held_keys):
            try:
                self._send_scan_code(vk, key_up=True)
            except Exception:
                pass
        self.control_held_keys.clear()

    # ─── Heartbeat ────────────────────────────────────────────────────────────
    def _heartbeat_loop(self):
        """Publishes health telemetry to IoT Core AND writes it to DynamoDB rift-devices."""
        while self.running:
            try:
                telemetry = self.get_system_telemetry()

                # Step 1: Publish to MQTT so the IoT Rule (if any) picks it up
                self.mqtt.publish(self.status_topic, telemetry)
                logger.info(
                    f"[HB] ✓ MQTT heartbeat → CPU {telemetry['cpu_percent']}% | "
                    f"RAM free {telemetry['ram_free_mb']} MB | "
                    f"LLM: {'ready' if telemetry['llm_loaded'] else 'not loaded'}"
                )

                # Step 2: Write directly to DynamoDB so server reads online status
                if self.devices_tbl:
                    item = to_decimal({**telemetry, "lastSeen": int(time.time())})
                    self.devices_tbl.put_item(Item=item)
                    logger.debug("[HB] ✓ DynamoDB rift-devices updated")
                else:
                    logger.warning("[HB] ⚠ DynamoDB unavailable — skipping DB write")

            except Exception as e:
                logger.error(f"[HB] ✗ Heartbeat error: {e}")

            time.sleep(self.config.heartbeat_interval_sec)

    # ─── MQTT message handler ─────────────────────────────────────────────────
    def _handle_mqtt_message(self, topic: str, raw_payload: str):
        """Processes all incoming MQTT messages."""
        logger.info(f"[MQTT] ← Received on [{topic}]: {raw_payload[:140]}")

        # Vision frames
        if topic.endswith("/vision_in"):
            logger.info("[MQTT] → Routing to vision handler")
            self._handle_vision_frame(raw_payload)
            return

        # Commands (Phase 3 strict allowlist + HMAC verification)
        is_valid, msg, payload = self.verifier.verify_and_parse(raw_payload)
        if not is_valid:
            logger.warning(f"[MQTT] ✗ SECURITY ALERT: Rejected command. Reason: {msg}")
            self.mqtt.publish(self.result_topic, {
                "deviceId":    self.config.device_id,
                "status":      "REJECTED",
                "reason":      msg,
                "raw_command": payload.get("command") if isinstance(payload, dict) else None,
                "timestamp":   int(time.time())
            })
            return

        cmd    = payload["command"]
        cmd_id = payload.get("cmdId", str(int(time.time())))
        params = payload.get("params", {})

        logger.info(f"[MQTT] ✓ Authorized command: cmd={cmd} cmdId={cmd_id}")
        self._execute_command(cmd, cmd_id, params)

    # ─── Command execution ────────────────────────────────────────────────────
    def _execute_command(self, cmd: str, cmd_id: str, params: Dict[str, Any]):
        result = {
            "cmdId":    cmd_id,
            "command":  cmd,
            "deviceId": self.config.device_id,
            "status":   "SUCCESS"
        }

        if cmd == "ping":
            logger.info("[CMD] ▶ ping — replying pong")
            result["reply"] = "pong"
            result["time"]  = time.time()

        elif cmd == "status":
            logger.info("[CMD] ▶ status — collecting telemetry")
            tel = self.get_system_telemetry()
            result["telemetry"] = tel
            logger.info(f"[CMD]   CPU={tel['cpu_percent']}% RAM_free={tel['ram_free_mb']}MB")

        elif cmd == "run_chunk":
            chunk_type = params.get("type", "monte_carlo")
            logger.info(f"[CMD] ▶ run_chunk — type={chunk_type} params={params}")
            if chunk_type == "monte_carlo":
                result["chunk_result"] = MathWorker.run_monte_carlo(params)
            elif chunk_type == "matrix_multiply":
                result["chunk_result"] = MathWorker.run_matrix_multiply(params)
            elif chunk_type == "image_tile":
                result["chunk_result"] = ImageWorker.process_tile(params)
            else:
                result["status"] = "ERROR"
                result["error"]  = f"Unknown chunk type: {chunk_type}"
            logger.info(f"[CMD]   run_chunk done: status={result['status']}")

        elif cmd == "start_llm":
            job_id = params.get("jobId", f"llm-{int(time.time())}")
            prompt = params.get("prompt", "Hello from R.I.F.T.!")
            logger.info(f"[CMD] ▶ start_llm — job={job_id} prompt={prompt[:60]}")
            threading.Thread(target=self._stream_llm, args=(job_id, prompt), daemon=True).start()
            result["reply"] = f"LLM streaming started for job {job_id}"

        elif cmd == "stop_llm":
            logger.info("[CMD] ▶ stop_llm — (no persistent process to stop with Ollama CLI mode)")
            result["reply"] = "stop_llm acknowledged (Ollama subprocess will exit naturally)"

        elif cmd == "restart_agent":
            logger.info("[CMD] ▶ restart_agent — publishing result then restarting…")
            result["reply"] = "Restarting now…"
            self.mqtt.publish(self.result_topic, result)
            self.stop()
            os.execv(sys.executable, ['python'] + sys.argv)
            return

        self.mqtt.publish(self.result_topic, result)
        logger.info(f"[CMD] ✓ Result published to {self.result_topic}")

    # ─── LLM streaming ────────────────────────────────────────────────────────
    def _stream_llm(self, job_id: str, prompt: str):
        token_topic = f"rift/{job_id}/tokens"
        logger.info(f"[LLM] Streaming {self.config.default_llm_model} → {token_topic}")
        token_count = 0
        try:
            for token in self.llm_worker.stream_generate(prompt):
                self.mqtt.publish(token_topic, {
                    "deviceId":  self.config.device_id,
                    "jobId":     job_id,
                    "token":     token,
                    "timestamp": time.time()
                })
                token_count += 1
        except Exception as error:
            logger.exception("[LLM] Generation failed for job %s", job_id)
            self.mqtt.publish(token_topic, {
                "deviceId":  self.config.device_id,
                "jobId":     job_id,
                "error":     str(error),
                "done":      True,
                "timestamp": time.time()
            })
            return
        self.mqtt.publish(token_topic, {
            "deviceId":  self.config.device_id,
            "jobId":     job_id,
            "done":      True,
            "timestamp": time.time()
        })
        logger.info(f"[LLM] ✓ Streaming complete. {token_count} tokens sent.")

    # ─── Vision ───────────────────────────────────────────────────────────────
    def _handle_vision_frame(self, raw_payload: str):
        """Processes camera frame from phone and publishes detections back."""
        try:
            data      = json.loads(raw_payload)
            frame_b64 = data.get("frame_b64", "")
            frame_id  = data.get("frameId", 0)
            logger.info(f"[VISION] Processing frame #{frame_id} ({len(frame_b64)} b64 chars)")

            results   = self.vision_worker.detect_objects_in_frame(frame_b64)
            det_topic = f"rift/{self.config.device_id}/detections"
            self.mqtt.publish(det_topic, {
                "deviceId":  self.config.device_id,
                "frameId":   frame_id,
                "results":   results,
                "timestamp": time.time()
            })
            logger.info(f"[VISION] ✓ {results['count']} detections → {det_topic}")
        except Exception as e:
            logger.error(f"[VISION] ✗ Error: {e}")

    # ─── SQS Chunk Worker ─────────────────────────────────────────────────────
    def _sqs_worker_loop(self):
        """
        Long-polls SQS for chunk tasks, executes them locally, uploads results to S3,
        updates DynamoDB, then deletes the SQS message.
        """
        if not self.sqs_cli:
            logger.warning("[SQS] ⚠ No SQS client — worker loop disabled (AWS credentials missing)")
            return

        logger.info(f"[SQS] Worker loop started. Polling: {self.config.sqs_queue_url}")

        while self.running:
            try:
                # Long-poll: waits up to 20s for a message (saves API calls)
                logger.debug("[SQS] Waiting for messages (long-poll 20s)…")
                resp = self.sqs_cli.receive_message(
                    QueueUrl=self.config.sqs_queue_url,
                    WaitTimeSeconds=20,
                    MaxNumberOfMessages=1,
                    MessageAttributeNames=["All"]
                )

                messages = resp.get("Messages", [])
                if not messages:
                    logger.debug("[SQS] No messages available — polling again")
                    continue

                for msg in messages:
                    receipt_handle = msg["ReceiptHandle"]
                    body = json.loads(msg["Body"])
                    job_id   = body.get("jobId", "unknown")
                    chunk_id = body.get("chunkId", "unknown")
                    job_type = body.get("type", "monte_carlo")
                    params   = body.get("params", {})

                    logger.info(f"[SQS] ▶ Received chunk: job={job_id} chunk={chunk_id} type={job_type}")

                    # Mark chunk as RUNNING in DynamoDB
                    if self.chunks_tbl:
                        try:
                            self.chunks_tbl.update_item(
                                Key={"jobId": job_id, "chunkId": chunk_id},
                                UpdateExpression="SET #s = :s, assignedDevice = :d, attempts = attempts + :one",
                                ExpressionAttributeNames={"#s": "status"},
                                ExpressionAttributeValues={
                                    ":s":   "RUNNING",
                                    ":d":   self.config.device_id,
                                    ":one": 1
                                }
                            )
                            logger.info(f"[SQS]   DynamoDB chunk {chunk_id} → RUNNING")
                        except Exception as e:
                            logger.warning(f"[SQS]   Could not mark chunk RUNNING: {e}")

                    # Execute the chunk
                    t0 = time.time()
                    try:
                        if job_type == "monte_carlo":
                            chunk_result = MathWorker.run_monte_carlo(params)
                        elif job_type == "matrix_multiply":
                            chunk_result = MathWorker.run_matrix_multiply(params)
                        elif job_type == "image_tile":
                            chunk_result = ImageWorker.process_tile(params)
                        else:
                            raise ValueError(f"Unknown job type: {job_type}")

                        elapsed = round(time.time() - t0, 3)
                        logger.info(f"[SQS]   ✓ Chunk {chunk_id} computed in {elapsed}s: {str(chunk_result)[:80]}")

                        # Upload result to S3
                        result_key = f"results/{job_id}/{chunk_id}.json"
                        result_payload = json.dumps({
                            "jobId":       job_id,
                            "chunkId":     chunk_id,
                            "deviceId":    self.config.device_id,
                            "result":      chunk_result,
                            "elapsed_sec": elapsed
                        })

                        if self.s3_cli:
                            self.s3_cli.put_object(
                                Bucket=self.config.s3_bucket,
                                Key=result_key,
                                Body=result_payload.encode(),
                                ContentType="application/json"
                            )
                            logger.info(f"[SQS]   ✓ Result uploaded → s3://{self.config.s3_bucket}/{result_key}")

                        # Mark chunk COMPLETED in DynamoDB
                        if self.chunks_tbl:
                            self.chunks_tbl.update_item(
                                Key={"jobId": job_id, "chunkId": chunk_id},
                                UpdateExpression="SET #s = :s, outputKey = :k",
                                ExpressionAttributeNames={"#s": "status"},
                                ExpressionAttributeValues={":s": "COMPLETED", ":k": result_key}
                            )
                            logger.info(f"[SQS]   ✓ DynamoDB chunk {chunk_id} → COMPLETED")

                        # Increment chunksDone on job
                        if self.jobs_tbl:
                            self.jobs_tbl.update_item(
                                Key={"jobId": job_id},
                                UpdateExpression="SET chunksDone = chunksDone + :one",
                                ExpressionAttributeValues={":one": 1}
                            )
                            logger.info(f"[SQS]   ✓ Job {job_id} chunksDone incremented")

                    except Exception as e:
                        logger.error(f"[SQS]   ✗ Chunk {chunk_id} FAILED: {e}")
                        if self.chunks_tbl:
                            try:
                                self.chunks_tbl.update_item(
                                    Key={"jobId": job_id, "chunkId": chunk_id},
                                    UpdateExpression="SET #s = :s",
                                    ExpressionAttributeNames={"#s": "status"},
                                    ExpressionAttributeValues={":s": "FAILED"}
                                )
                            except Exception:
                                pass
                        # Don't delete the SQS message — let it return to queue for retry
                        continue

                    # Delete the SQS message (chunk is done)
                    self.sqs_cli.delete_message(
                        QueueUrl=self.config.sqs_queue_url,
                        ReceiptHandle=receipt_handle
                    )
                    logger.info(f"[SQS]   ✓ SQS message deleted (chunk {chunk_id} complete)")

            except Exception as e:
                logger.error(f"[SQS] ✗ Worker loop error: {e}")
                time.sleep(5)

    # ─── Shutdown ─────────────────────────────────────────────────────────────
    def stop(self):
        logger.info("[STOP] Shutting down R.I.F.T. Agent…")
        self.running = False
        self.mqtt.disconnect()
        logger.info("[STOP] Disconnected from IoT Core.")

        # Mark device offline in DynamoDB
        if self.devices_tbl:
            try:
                self.devices_tbl.update_item(
                    Key={"deviceId": self.config.device_id},
                    UpdateExpression="SET #s = :s",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "offline"}
                )
                logger.info("[STOP] ✓ Device marked offline in DynamoDB")
            except Exception:
                pass


if __name__ == "__main__":
    agent = RiftAgent()
    agent.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        agent.stop()
