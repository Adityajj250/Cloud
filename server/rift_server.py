"""
R.I.F.T. Main HTTPS + WebSocket Server
Serves the PWA over HTTPS (required for phone camera getUserMedia).
All real-time communication goes through WebSocket.
Phone is just a display – no data stored on phone.
"""

import asyncio
import json
import os
import time
import logging
import subprocess
import threading
import base64
import sys
import mimetypes
import hmac
import hashlib
import secrets
import re
import signal
from urllib.parse import quote
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import boto3
from boto3.dynamodb.conditions import Key
from aiohttp import web
import aiohttp

try:
    from .hub.device_registry import DeviceRegistry
except ImportError:  # Running rift_server.py directly from the server folder.
    from hub.device_registry import DeviceRegistry

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s – %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("rift.server")

# ─── Configuration ────────────────────────────────────────────────────────────
ENVIRONMENT  = os.getenv("RIFT_ENV", "development").lower()
PRODUCTION   = ENVIRONMENT == "production"
REGION       = os.getenv("RIFT_AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-southeast-2"
PROFILE      = os.getenv("RIFT_AWS_PROFILE") or os.getenv("AWS_PROFILE") or (None if PRODUCTION else "rift-admin")
LEGACY_DEVICE_ID = os.getenv("RIFT_DEVICE_ID", "rift-win-worker-01")
BUCKET       = os.getenv("RIFT_S3_BUCKET", "rift-storage-903859732456-ap-southeast-2")
QUEUE_URL    = os.getenv("RIFT_SQS_QUEUE_URL", "https://sqs.ap-southeast-2.amazonaws.com/903859732456/rift-chunks-queue")
IOT_ENDPOINT = os.getenv("RIFT_IOT_ENDPOINT", "a30f3sdnp94spe-ats.iot.ap-southeast-2.amazonaws.com")
OLLAMA_MODEL = os.getenv("RIFT_LLM_MODEL", "qwen2.5:3b")
HMAC_SECRET  = os.getenv("RIFT_HMAC_SECRET") or ("" if PRODUCTION else "rift-super-secret-hmac-key-2026")
SERVER_PORT  = int(os.getenv("RIFT_SERVER_PORT", "8765"))
HUB_CLIENT_ID = os.getenv("RIFT_HUB_IOT_CLIENT_ID", f"{LEGACY_DEVICE_ID}-server-listener")
HUB_IOT_CERT = os.getenv("RIFT_HUB_IOT_CERT", "")
HUB_IOT_PRIVATE_KEY = os.getenv("RIFT_HUB_IOT_PRIVATE_KEY", "")
HUB_IOT_ROOT_CA = os.getenv("RIFT_HUB_IOT_ROOT_CA", "")
DEVICES_TABLE = os.getenv("RIFT_DEVICES_TABLE", "rift-devices")
JOBS_TABLE = os.getenv("RIFT_JOBS_TABLE", "rift-jobs")
CHUNKS_TABLE = os.getenv("RIFT_CHUNKS_TABLE", "rift-chunks")
ALLOWED_ORIGINS = {value.strip().rstrip("/") for value in os.getenv("RIFT_ALLOWED_ORIGINS", "").split(",") if value.strip()}
SHUTTING_DOWN = False
SHUTDOWN_EVENT = threading.Event()
_iot_conn = None
PWA_DIR      = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pwa")

if PRODUCTION:
    if len(HMAC_SECRET) < 32:
        raise RuntimeError("RIFT_HMAC_SECRET must be set to a random value of at least 32 characters in production")
    if "REPLACE_" in HMAC_SECRET:
        raise RuntimeError("Replace the example HMAC secret with a randomly generated value before starting production")
    for label, path in (("RIFT_HUB_IOT_CERT", HUB_IOT_CERT), ("RIFT_HUB_IOT_PRIVATE_KEY", HUB_IOT_PRIVATE_KEY),
                        ("RIFT_HUB_IOT_ROOT_CA", HUB_IOT_ROOT_CA)):
        if not path or not os.path.isfile(path):
            raise RuntimeError(f"{label} must point to a readable hub IoT certificate file in production")

# Add agent path so we can import vision detector
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent"))

# ─── AWS Session ──────────────────────────────────────────────────────────────
log.info("[INIT] Connecting to AWS (credential source=%s region=%s)…", PROFILE or "default chain / instance role", REGION)
try:
    aws_session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    dynamo      = aws_session.resource("dynamodb", region_name=REGION)
    sqs_cli     = aws_session.client("sqs",       region_name=REGION)
    s3_cli      = aws_session.client("s3",        region_name=REGION)
    iot_data    = aws_session.client("iot-data",  region_name=REGION)
    log.info("[INIT] ✓ AWS boto3 session ready")
except Exception as e:
    log.error(f"[INIT] ✗ AWS session failed: {e}")
    log.error("[INIT]   → Run: aws login --region ap-southeast-2 --profile rift-admin")
    raise SystemExit(1)

# ─── Singleton Vision Worker ──────────────────────────────────────────────────
try:
    from vision.detector import VisionWorker
    _vision_worker = VisionWorker()
    log.info("[INIT] ✓ VisionWorker singleton ready")
except Exception as e:
    _vision_worker = None
    log.warning(f"[INIT] ⚠ VisionWorker not available: {e}")

# ─── HMAC Signing Helper ──────────────────────────────────────────────────────
def make_signed_payload(command: str, cmd_id: str, params: dict = None) -> dict:
    """Creates an HMAC-SHA256 signed command payload."""
    payload = {"command": command, "cmdId": cmd_id}
    if params:
        payload["params"] = params
    canonical = json.dumps({k: payload[k] for k in sorted(payload) if k != "signature"}, sort_keys=True)
    sig = hmac.new(HMAC_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    payload["signature"] = sig
    log.debug(f"[SIGN] cmd={command} sig={sig[:16]}…")
    return payload

# ─── Connected WebSocket clients (phone browsers) ─────────────────────────────
ws_clients: set = set()
ws_sessions: dict = {}
mobile_connections: dict = {}
device_registry = DeviceRegistry()
pending_pings: dict[str, dict[str, Any]] = {}
pending_pings_lock = threading.Lock()
pending_file_ops: dict[str, asyncio.Future] = {}
video_preview_jobs: dict[str, tuple[str, str, asyncio.Task, float]] = {}
file_access_tokens: dict[tuple[str, str], str] = {}
pending_llm_jobs: dict[str, str] = {}
APP_LOOP = None
MAX_FILE_RANGE_BYTES = 1024 * 1024
MAX_AGENT_READ_BYTES = 1024 * 1024

# ─── WebSocket broadcast helper ───────────────────────────────────────────────
async def broadcast(msg: dict):
    if not ws_clients:
        return
    text = json.dumps(msg)
    dead = set()
    for ws in list(ws_clients):
        try:
            await ws.send_str(text)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


async def broadcast_to_role(role: str, msg: dict):
    text = json.dumps(msg)
    dead = set()
    for ws, session in list(ws_sessions.items()):
        if session.get("role") != role:
            continue
        try:
            await ws.send_str(text)
        except Exception:
            dead.add(ws)
    for ws in dead:
        ws_clients.discard(ws)
        ws_sessions.pop(ws, None)


def connected_mobiles():
    return [{"id": cid, "name": data["name"], "connections": len(data["sockets"])}
            for cid, data in mobile_connections.items() if data["sockets"]]


async def publish_mobile_list():
    await broadcast({"type": "mobile_clients", "clients": connected_mobiles()})


async def publish_device_list():
    payload = {"type": "device_list", "devices": device_registry.public_devices()}
    text = json.dumps(payload)
    dead = set()
    for ws, session in list(ws_sessions.items()):
        if session.get("role") not in ("laptop", "mobile"):
            continue
        try:
            await ws.send_str(text)
        except Exception:
            dead.add(ws)
    for ws in dead:
        ws_clients.discard(ws)
        ws_sessions.pop(ws, None)


def resolve_device_id(requested: str | None = None) -> str | None:
    """Resolve explicit target IDs and keep old one-device clients working."""
    if requested:
        device = device_registry.get(requested)
        return requested if device and not device.websocket.closed else None
    online = device_registry.online()
    if not requested and len(online) == 1:
        return online[0].device_id
    if not requested and device_registry.get(LEGACY_DEVICE_ID):
        return LEGACY_DEVICE_ID
    return None


async def request_agent_file_op(device_id: str, payload: dict[str, Any], timeout: float = 25.0) -> dict[str, Any]:
    device = device_registry.get(device_id)
    if not device or device.websocket.closed or "files" not in device.capabilities:
        raise web.HTTPServiceUnavailable(text="The selected computer's files service is offline.")
    request_id = secrets.token_urlsafe(18)
    future = asyncio.get_running_loop().create_future()
    pending_file_ops[request_id] = future
    try:
        await device.websocket.send_json({**payload, "requestId": request_id})
        result = await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError as error:
        raise web.HTTPGatewayTimeout(text="The computer did not respond to the file request.") from error
    except (aiohttp.ClientError, ConnectionError) as error:
        raise web.HTTPServiceUnavailable(text="The agent connection was interrupted.") from error
    finally:
        pending_file_ops.pop(request_id, None)
    if not result.get("ok"):
        raise web.HTTPBadRequest(text=str(result.get("error", "File request failed")))
    return result


def require_files_access(request, device_id: str):
    device = device_registry.get(device_id)
    client_id = request.headers.get("X-RIFT-Client-ID", "")[:80]
    token = request.headers.get("X-RIFT-FILES-TOKEN", "")
    if not device or device.websocket.closed or "files" not in device.capabilities:
        raise web.HTTPServiceUnavailable(text="That computer is offline or does not provide Files.")
    # Only the phone currently holding this computer's control lease may use files.
    expected_token = file_access_tokens.get((device_id, client_id), "")
    if not client_id or device.controller_client_id != client_id or not token or not hmac.compare_digest(token, expected_token):
        raise web.HTTPForbidden(text="Claim this computer in Screen Share before using its shared files.")
    return device, client_id


def make_file_signature(device_id: str, client_id: str, file_id: str, mode: str,
                        metadata: dict[str, Any] | None = None) -> str:
    payload = {"deviceId": device_id, "clientId": client_id, "fileId": file_id,
               "mode": mode, "expires": int(time.time()) + 600}
    if metadata:
        payload.update({"size": int(metadata.get("size", 0)),
                        "mime": str(metadata.get("mime", "application/octet-stream")),
                        "name": str(metadata.get("name", "file"))[:240]})
    token = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(HMAC_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    return token + "." + signature


def verify_file_signature(signature: str, device_id: str, file_id: str) -> dict[str, Any]:
    try:
        token, supplied = signature.rsplit(".", 1)
        expected = hmac.new(HMAC_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature")
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if data.get("deviceId") != device_id or data.get("fileId") != file_id or int(data.get("expires", 0)) < time.time():
            raise ValueError("expired")
        if data.get("mode") not in ("inline", "attachment"):
            raise ValueError("mode")
        return data
    except Exception as error:
        raise web.HTTPForbidden(text="File link is invalid or expired. Reopen the file to get a new link.") from error


async def handle_files_list(request):
    device, _ = require_files_access(request, request.match_info["deviceId"])
    path_id = request.query.get("pathId", "shared-root")[:100]
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError as error:
        raise web.HTTPBadRequest(text="Invalid folder page offset") from error
    result = await request_agent_file_op(device.device_id, {"type": "files_list_request", "pathId": path_id, "offset": offset})
    try:
        return web.json_response({key: result[key] for key in ("scope", "rootName", "rootPath", "entries", "parentId", "nextOffset") if key in result})
    except Exception:
        log.exception("[FILES] Could not format list response for device %s", device.device_id)
        return web.json_response({"error": "The computer returned an invalid folder listing. Check the agent log and restart the agent."}, status=502)


async def handle_files_link(request):
    device_id = request.match_info["deviceId"]
    file_id = request.match_info["fileId"][:100]
    device, client_id = require_files_access(request, device_id)
    mode = str((await request.json()).get("mode", "inline"))
    if mode not in ("inline", "attachment"):
        raise web.HTTPBadRequest(text="Mode must be inline or attachment")
    stat = await request_agent_file_op(device.device_id, {"type": "files_stat_request", "fileId": file_id})
    signature = make_file_signature(device_id, client_id, file_id, mode, stat)
    return web.json_response({"url": f"/api/files/{quote(device_id, safe='')}/content/{quote(file_id, safe='')}?sig={signature}",
        "name": stat.get("name", "file"), "size": int(stat.get("size", 0)),
        "mime": stat.get("mime", "application/octet-stream"), "expiresIn": 600, "mode": mode})


async def handle_files_video_preview(request):
    device_id = request.match_info["deviceId"]
    file_id = request.match_info["fileId"][:100]
    device, client_id = require_files_access(request, device_id)
    now = time.time()
    for old_id, (_, _, task, created) in list(video_preview_jobs.items()):
        if now - created > 1800:
            if not task.done():
                task.cancel()
            video_preview_jobs.pop(old_id, None)
    async def convert():
        try:
            result = await request_agent_file_op(device.device_id, {
                "type": "files_transcode_request", "fileId": file_id
            }, timeout=1800)
            preview_id = str(result.get("fileId", ""))
            if not preview_id:
                return {"status": "failed", "error": "The computer did not return a converted video."}
            signature = make_file_signature(device_id, client_id, preview_id, "inline", result)
            return {"status": "ready", "url": f"/api/files/{quote(device_id, safe='')}/content/{quote(preview_id, safe='')}?sig={signature}",
                "name": result.get("name", "video-phone.mp4"), "size": int(result.get("size", 0)),
                "mime": "video/mp4", "expiresIn": 600}
        except web.HTTPException as error:
            return {"status": "failed", "error": error.text or "Video conversion failed."}
        except Exception:
            log.exception("[FILES] Video preview conversion failed for %s", device_id)
            return {"status": "failed", "error": "Video conversion failed. Check the computer agent log."}

    job_id = secrets.token_urlsafe(18)
    task = asyncio.create_task(convert())
    video_preview_jobs[job_id] = (device_id, client_id, task, now)
    return web.json_response({"status": "processing", "jobId": job_id}, status=202)


async def handle_files_video_preview_status(request):
    device_id = request.match_info["deviceId"]
    device, client_id = require_files_access(request, device_id)
    job = video_preview_jobs.get(request.match_info["jobId"][:100])
    if not job or job[0] != device.device_id or job[1] != client_id:
        raise web.HTTPNotFound(text="Video preview job expired or belongs to another controller.")
    task = job[2]
    if not task.done():
        return web.json_response({"status": "processing"})
    return web.json_response(task.result())


async def handle_files_content(request):
    device_id, file_id = request.match_info["deviceId"], request.match_info["fileId"]
    signed = verify_file_signature(request.query.get("sig", ""), device_id, file_id)
    # The signed link already carries the metadata obtained when it was created.
    # Avoid a second agent round trip before the browser can receive video bytes.
    metadata = signed if "size" in signed and "mime" in signed else await request_agent_file_op(
        device_id, {"type": "files_stat_request", "fileId": file_id})
    size, offset, end = int(metadata["size"]), 0, int(metadata["size"]) - 1
    status = 200
    range_header = request.headers.get("Range", "")
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or (not match.group(1) and not match.group(2)):
            raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{size}"})
        if not match.group(1):
            suffix = int(match.group(2))
            offset = max(0, size - suffix)
        else:
            offset = int(match.group(1))
            if match.group(2): end = min(end, int(match.group(2)))
        if size == 0 or offset >= size or end < offset:
            raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{size}"})
        status = 206
    end = min(end, size - 1)
    # Media clients commonly request an open-ended range (bytes=0-). Keep
    # each response bounded so playback can start and the browser can request
    # subsequent bytes only as the viewer watches or seeks.
    if status == 206:
        end = min(end, offset + MAX_FILE_RANGE_BYTES - 1)
    length = max(0, end - offset + 1)
    mime = str(metadata.get("mime", "application/octet-stream"))
    # Never render active HTML/SVG content in the hub's origin.
    unsafe_inline = mime in ("text/html", "image/svg+xml", "application/xhtml+xml")
    if unsafe_inline:
        mime = "application/octet-stream"
    filename = quote(str(metadata.get("name", "file")), safe="")
    disposition = "attachment" if signed["mode"] == "attachment" or unsafe_inline else "inline"
    headers = {"Accept-Ranges": "bytes", "Content-Type": mime,
               "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
               "Content-Length": str(length)}
    if status == 206:
        headers["Content-Range"] = f"bytes {offset}-{end}/{size}"
    response = web.StreamResponse(status=status, headers=headers)
    await response.prepare(request)
    remaining = length
    while remaining:
        amount = min(MAX_AGENT_READ_BYTES, remaining)
        result = await request_agent_file_op(device_id, {"type": "files_read_request", "fileId": file_id,
            "offset": offset, "length": amount}, timeout=45)
        payload = base64.b64decode(result.get("data", ""), validate=True)
        if not payload:
            break
        await response.write(payload)
        offset += len(payload)
        remaining -= len(payload)
    if remaining:
        raise web.HTTPBadGateway(text="The computer returned an incomplete file range.")
    await response.write_eof()
    return response


async def handle_files_upload_start(request):
    device, _ = require_files_access(request, request.match_info["deviceId"])
    try:
        data = await request.json()
        name, size = str(data.get("name", "upload"))[:240], int(data.get("size", -1))
    except Exception as error:
        raise web.HTTPBadRequest(text="Expected JSON containing file name and size") from error
    if size < 0 or size > 4 * 1024 * 1024 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=4 * 1024 * 1024 * 1024, actual_size=max(size, 0))
    supplied_id = str(data.get("uploadId", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", supplied_id):
        supplied_id = secrets.token_urlsafe(24)
    result = await request_agent_file_op(device.device_id, {"type": "files_upload_start_request",
        "uploadId": supplied_id, "name": name, "size": size})
    return web.json_response(result)


async def handle_files_upload_chunk(request):
    device, _ = require_files_access(request, request.match_info["deviceId"])
    upload_id = request.match_info["uploadId"][:80]
    try:
        offset = int(request.query.get("offset", "-1"))
    except ValueError as error:
        raise web.HTTPBadRequest(text="Invalid upload offset") from error
    payload = await request.content.read(512 * 1024 + 1)
    if not payload or len(payload) > 512 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=512 * 1024, actual_size=len(payload))
    result = await request_agent_file_op(device.device_id, {"type": "files_upload_chunk_request",
        "uploadId": upload_id, "offset": offset, "data": base64.b64encode(payload).decode("ascii")})
    return web.json_response(result)


async def handle_files_upload_finish(request):
    device, client_id = require_files_access(request, request.match_info["deviceId"])
    result = await request_agent_file_op(device.device_id, {"type": "files_upload_finish_request",
        "uploadId": request.match_info["uploadId"][:80]})
    await broadcast_to_role("laptop", {"type": "files_transfer_complete", "deviceId": device.device_id,
        "clientId": client_id, "name": result.get("name", "file"), "size": result.get("size", 0),
        "quarantined": bool(result.get("quarantined")), "path": result.get("path", "RIFT Shared")})
    return web.json_response(result)


def sync_broadcast(msg: dict):
    """Thread-safe broadcast from sync threads (e.g. MQTT subscriber)."""
    try:
        if APP_LOOP and APP_LOOP.is_running():
            asyncio.run_coroutine_threadsafe(broadcast(msg), APP_LOOP)
    except Exception:
        pass


# ─── Ping ─────────────────────────────────────────────────────────────────────
async def handle_ping(request):
    """
    Sends a signed ping command to the laptop agent via AWS IoT Core MQTT
    and streams step-by-step log messages back to phone via WebSocket.
    """
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        body = {}
    device_id = resolve_device_id(body.get("deviceId"))
    if not device_id:
        return web.json_response({"error": "No online computer is available. Choose an online device first."}, status=409)
    client_id = str(body.get("clientId", ""))[:80]
    steps = []

    def step(msg, ok=True):
        entry = {"ts": time.strftime("%H:%M:%S"), "msg": msg, "ok": ok}
        steps.append(entry)
        log.info(f"[PING] {msg}")
        sync_broadcast({"type": "ping_step", "deviceId": device_id, "clientId": client_id, "step": entry})

    step("① Ping initiated by phone →")
    step(f"② Target device: {device_id}")

    try:
        cmd_id  = f"ping-{device_id}-{secrets.token_hex(6)}"
        now = time.time()
        with pending_pings_lock:
            for old_id in [key for key, value in pending_pings.items() if value["expiresAt"] < now]:
                pending_pings.pop(old_id, None)
            pending_pings[cmd_id] = {"deviceId": device_id, "clientId": client_id, "expiresAt": now + 30}
        payload = make_signed_payload("ping", cmd_id)
        step(f"③ HMAC-SHA256 signature: {payload['signature'][:16]}…")

        topic = f"rift/{device_id}/cmd"
        step(f"④ Publishing to IoT Core topic: {topic}")
        iot_data.publish(topic=topic, qos=1, payload=json.dumps(payload))
        step("⑤ ✓ Message delivered to AWS IoT Core (HTTP 200)")
        step("⑥ → IoT Core will MQTT-push to agent on port 443")
        step("⑦ → Agent will verify HMAC, match 'ping' in allowlist")
        step("⑧ → Agent will reply 'pong' on /result topic (watch log below)")

        sync_broadcast({"type": "ping_done", "deviceId": device_id, "clientId": client_id, "cmdId": cmd_id, "steps": steps})
        return web.json_response({"status": "sent", "deviceId": device_id, "clientId": client_id, "cmdId": cmd_id, "steps": steps})

    except Exception as e:
        step(f"✗ ERROR: {e}", ok=False)
        return web.json_response({"status": "error", "error": str(e), "steps": steps}, status=500)


# ─── Job Submission ────────────────────────────────────────────────────────────
async def handle_submit_job(request):
    data        = await request.json()
    job_type    = data.get("type", "monte_carlo")
    chunk_count = int(data.get("chunks", 4))
    samples     = int(data.get("samples", 200_000))

    import uuid, decimal
    job_id     = f"job-{uuid.uuid4().hex[:8]}"
    jobs_tbl   = dynamo.Table(JOBS_TABLE)
    chunks_tbl = dynamo.Table(CHUNKS_TABLE)

    log.info(f"[JOB] ▶ Submitting {job_type} job {job_id} ({chunk_count} chunks)")
    sync_broadcast({"type": "log", "msg": f"→ Submitting {job_id} ({job_type}, {chunk_count} chunks)…"})

    jobs_tbl.put_item(Item={
        "jobId":       job_id,
        "type":        job_type,
        "status":      "PROCESSING",
        "chunkCount":  chunk_count,
        "chunksDone":  0,
        "createdAt":   int(time.time())
    })

    for i in range(chunk_count):
        chunk_id = f"chunk-{i:03d}"
        params   = {}
        if job_type == "monte_carlo":
            params = {"seed": 1000 + i * 17, "samples": samples}
        elif job_type == "matrix_multiply":
            params = {"row_start": i * 25, "row_end": (i + 1) * 25, "dim": 200}
        elif job_type == "image_tile":
            params = {"tile_id": chunk_id, "filter_type": "edge_enhance", "width": 256, "height": 256}

        chunks_tbl.put_item(Item={
            "jobId":   job_id,
            "chunkId": chunk_id,
            "status":  "QUEUED",
            "attempts": 0,
            "params":  params
        })

        sqs_cli.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({
                "jobId":   job_id,
                "chunkId": chunk_id,
                "type":    job_type,
                "params":  params
            })
        )
        log.info(f"[JOB]   ✓ Enqueued {chunk_id}")

    sync_broadcast({"type": "log", "msg": f"✓ Job {job_id}: {chunk_count} chunks → SQS. Agent will poll and execute."})
    sync_broadcast({"type": "job_created", "jobId": job_id, "jobType": job_type, "chunks": chunk_count})
    return web.json_response({"jobId": job_id, "status": "QUEUED", "chunks": chunk_count})


# ─── Job List ─────────────────────────────────────────────────────────────────
async def handle_list_jobs(request):
    try:
        tbl    = dynamo.Table(JOBS_TABLE)
        result = tbl.scan(Limit=20)
        items  = result.get("Items", [])
        import decimal
        def clean(v):
            if isinstance(v, decimal.Decimal):
                return int(v) if v == int(v) else float(v)
            if isinstance(v, dict):
                return {k: clean(x) for k, x in v.items()}
            if isinstance(v, list):
                return [clean(x) for x in v]
            return v
        items = [clean(i) for i in items]
        items.sort(key=lambda x: x.get("createdAt", 0), reverse=True)
        return web.json_response({"jobs": items})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ─── Delete Job ───────────────────────────────────────────────────────────────
async def handle_delete_job(request):
    job_id = request.match_info["jobId"]
    try:
        dynamo.Table(JOBS_TABLE).delete_item(Key={"jobId": job_id})
        log.info(f"[JOB] ✓ Deleted job {job_id}")
        sync_broadcast({"type": "job_deleted", "jobId": job_id})
        return web.json_response({"status": "deleted", "jobId": job_id})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ─── Camera Frame Processing ──────────────────────────────────────────────────
async def handle_camera_frame(request):
    """
    Receives a single JPEG frame from the phone camera (as base64 JSON).
    Runs OpenCV face detection on the laptop (CPU).
    Returns detections as JSON.
    The phone camera is captured in the browser via getUserMedia with facingMode:'environment'.
    The frame travels over HTTPS to this endpoint on the laptop.
    """
    try:
        data      = await request.json()
        frame_b64 = data.get("frame", "")
        client_id = str(data.get("clientId", "mobile"))[:80]

        if _vision_worker is None:
            return web.json_response({"error": "VisionWorker not available", "count": 0, "detections": []}, status=503)

        results = _vision_worker.detect_objects_in_frame(frame_b64)
        await broadcast_to_role("laptop", {
            "type": "phone_camera_frame", "clientId": client_id,
            "frame": frame_b64, "detections": results.get("detections", []),
            "count": results.get("count", 0), "timestamp": int(time.time())
        })
        log.info(f"[VISION] ← Phone frame received → {results['count']} detections ({results['inference_time_ms']}ms)")
        return web.json_response(results)
    except Exception as e:
        log.error(f"[VISION] ✗ Error: {e}")
        return web.json_response({"error": str(e), "count": 0, "detections": []}, status=500)


# ─── LLM generation ──────────────────────────────────────────────────────────
async def handle_llm_stream(request):
    """Dispatch an LLM prompt to the selected connected computer's Ollama."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Expected a JSON prompt request."}, status=400)
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        return web.json_response({"error": "Empty prompt"}, status=400)
    if len(prompt) > 12000:
        return web.json_response({"error": "Prompt is too long (maximum 12,000 characters)."}, status=413)
    requested_device = str(data.get("deviceId", ""))[:100]
    device_id = resolve_device_id(requested_device)
    device = device_registry.get(device_id)
    if not device or device.websocket.closed:
        return web.json_response({"error": "Select an online computer to run the LLM."}, status=409)
    if "llm" not in device.capabilities:
        return web.json_response({
            "error": f"{device.name} is not advertising LLM. Start Ollama on that computer, install {OLLAMA_MODEL}, then restart its R.I.F.T. agent."
        }, status=503)
    job_id = str(data.get("jobId", ""))[:80]
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", job_id):
        job_id = f"llm-{secrets.token_hex(16)}"
    payload = make_signed_payload("start_llm", job_id, {"jobId": job_id, "prompt": prompt})
    try:
        pending_llm_jobs[job_id] = device.device_id
        topic = f"rift/{device.device_id}/cmd"
        iot_data.publish(topic=topic, qos=1, payload=json.dumps(payload))
        log.info("[LLM] Dispatched job %s to %s", job_id, device.device_id)
        return web.json_response({"jobId": job_id, "deviceId": device.device_id, "status": "accepted"}, status=202)
    except Exception as e:
        pending_llm_jobs.pop(job_id, None)
        log.exception("[LLM] Could not dispatch job %s", job_id)
        return web.json_response({"error": "Could not dispatch the prompt to the selected computer."}, status=502)


# ─── Viewer Link ──────────────────────────────────────────────────────────────
async def handle_viewer_link(request):
    """
    Generates a 60-minute presigned S3 URL pointing to a viewer page
    in the REAL rift-storage bucket (not a non-existent rift-view bucket).
    """
    try:
        viewer_key  = "viewer/index.html"
        viewer_html = """<!DOCTYPE html><html><head><title>R.I.F.T. Viewer</title>
<style>body{background:#0b0f19;color:#e2e8f0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;}
h1{color:#38bdf8;}p{color:#94a3b8;}code{color:#4ade80;background:#1e293b;padding:4px 8px;border-radius:4px;}</style></head>
<body><div><h1>R.I.F.T. Remote Viewer</h1><p>This viewer link is active for 60 minutes.</p>
<p>Bucket: <code>rift-storage-903859732456-ap-southeast-2</code></p>
<p>Connect your second phone to this link to view the live session.</p></div></body></html>"""

        try:
            s3_cli.put_object(
                Bucket=BUCKET,
                Key=viewer_key,
                Body=viewer_html.encode(),
                ContentType="text/html"
            )
            log.info(f"[VIEWER] ✓ Uploaded viewer page → s3://{BUCKET}/{viewer_key}")
        except Exception as e:
            log.warning(f"[VIEWER] ⚠ Could not upload viewer HTML: {e}")

        url = s3_cli.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": viewer_key},
            ExpiresIn=3600
        )
        log.info(f"[VIEWER] ✓ Presigned URL generated ({len(url)} chars)")
        return web.json_response({"url": url, "expires_in": 3600, "bucket": BUCKET})
    except Exception as e:
        log.error(f"[VIEWER] ✗ Error: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ─── Laptop Status ────────────────────────────────────────────────────────────
async def handle_laptop_status(request):
    """Compatibility endpoint for old clients; new clients use /api/devices."""
    requested = request.query.get("deviceId")
    device_id = resolve_device_id(requested) or requested or LEGACY_DEVICE_ID
    live = device_registry.get(device_id)
    if live:
        return web.json_response({"online": not live.websocket.closed, **live.public_view()})
    try:
        tbl  = dynamo.Table(DEVICES_TABLE)
        item = tbl.get_item(Key={"deviceId": device_id}).get("Item")
        if item:
            import decimal
            def clean(v):
                if isinstance(v, decimal.Decimal):
                    return int(v) if v == int(v) else float(v)
                return v
            last_seen = int(item.get("lastSeen", 0))
            online    = (time.time() - last_seen) < 90
            return web.json_response({
                "online":        online,
                "deviceId":      device_id,
                "name":          item.get("deviceName", device_id),
                "cpu":           float(item.get("cpu_percent", 0)),
                "ram_free_mb":   float(item.get("ram_free_mb", 0)),
                "last_seen_sec": int(time.time() - last_seen)
            })
        return web.json_response({
            "online":   False,
            "deviceId": device_id,
            "reason":   "No heartbeat in DynamoDB yet. Is the agent running?"
        })
    except Exception as e:
        return web.json_response({"online": False, "error": str(e)}, status=200)


async def handle_list_devices(request):
    """Return the live fleet known to this hub without querying AWS."""
    return web.json_response({"devices": device_registry.public_devices()})


# ─── WebSocket Handler ────────────────────────────────────────────────────────
async def handle_ws(request):
    ws = web.WebSocketResponse(heartbeat=15, max_msg_size=5 * 1024 * 1024, protocols=("rift.v1",))
    await ws.prepare(request)
    ws_clients.add(ws)
    client_ip = request.remote
    log.info(f"[WS] ✓ Phone connected: {client_ip} (total={len(ws_clients)})")
    await ws.send_str(json.dumps({
        "type": "welcome",
        "msg":  "Connected to R.I.F.T. laptop server ✓",
        "devices": device_registry.public_devices()
    }))

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    mtype = data.get("type", "?")
                    if mtype not in ("control_cursor", "control_frame", "control_input"):
                        log.info(f"[WS] ← Phone sent: {mtype}")
                    if mtype == "identify":
                        role = data.get("role") if data.get("role") in ("laptop", "mobile", "agent") else "mobile"
                        client_id = str(data.get("clientId", "unknown"))[:80]
                        session = {"role": role, "clientId": client_id,
                                   "name": str(data.get("name", "Mobile device"))[:80]}
                        if role == "agent":
                            device_id = str(data.get("deviceId") or client_id or LEGACY_DEVICE_ID)[:100]
                            try:
                                device = device_registry.register(device_id, ws, session["name"], data.get("capabilities", []))
                            except ValueError as error:
                                await ws.send_str(json.dumps({"type": "device_error", "message": str(error)}))
                                await ws.close(code=4009, message=b"duplicate device id")
                                continue
                            session["deviceId"] = device.device_id
                            ws_sessions[ws] = session
                            log.info("[HUB] Agent online: %s (%s)", device.device_id, device.name)
                            await ws.send_str(json.dumps({"type": "device_update", "device": device.public_view()}))
                            await publish_device_list()
                        elif role == "mobile":
                            ws_sessions[ws] = session
                            entry = mobile_connections.setdefault(client_id, {"name": session["name"], "sockets": set()})
                            entry["name"] = session["name"]
                            entry["sockets"].add(ws)
                            await publish_mobile_list()
                            await ws.send_str(json.dumps({"type": "device_list", "devices": device_registry.public_devices()}))
                        else:
                            ws_sessions[ws] = session
                            await ws.send_str(json.dumps({"type": "mobile_clients", "clients": connected_mobiles()}))
                            await ws.send_str(json.dumps({"type": "device_list", "devices": device_registry.public_devices()}))
                    elif mtype == "ping_laptop":
                        await ws.send_str(json.dumps({"type": "laptop_pong", "sentAt": data.get("sentAt")}))
                    elif mtype == "ping_mobile":
                        for cid, entry in mobile_connections.items():
                            if data.get("clientId") in (None, cid):
                                for phone_ws in list(entry["sockets"]):
                                    await phone_ws.send_str(json.dumps({"type": "ping_from_laptop", "clientId": cid, "pingId": data.get("pingId")}))
                    elif mtype == "mobile_pong":
                        await broadcast_to_role("laptop", {"type": "mobile_pong", "clientId": data.get("clientId"), "pingId": data.get("pingId"), "name": data.get("name")})
                    elif mtype == "control_ready" and ws_sessions.get(ws, {}).get("role") == "agent":
                        log.info("[CONTROL] Screen control is available for %s", ws_sessions[ws].get("deviceId"))
                    elif mtype == "device_telemetry" and ws_sessions.get(ws, {}).get("role") == "agent":
                        telemetry = data.get("telemetry", {})
                        if isinstance(telemetry, dict):
                            device = device_registry.update_telemetry(ws, telemetry)
                            if device:
                                await broadcast({"type": "device_update", "device": device.public_view()})
                    elif mtype.startswith("files_") and mtype.endswith("_result") and ws_sessions.get(ws, {}).get("role") == "agent":
                        request_id = str(data.get("requestId", ""))
                        pending = pending_file_ops.get(request_id)
                        if pending and not pending.done():
                            pending.set_result(data)
                    elif mtype == "control_request" and ws_sessions.get(ws, {}).get("role") == "mobile":
                        device_id = resolve_device_id(data.get("deviceId"))
                        device = device_registry.get(device_id)
                        client_id = ws_sessions[ws]["clientId"]
                        if not device or device.websocket.closed:
                            await ws.send_str(json.dumps({"type": "control_status", "deviceId": device_id,
                                "ok": False, "message": "That computer is not connected to the hub."}))
                        elif (device.controller_client_id and device.controller_client_id != client_id) or (
                                device.pending_controller_client_id and device.pending_controller_client_id != client_id):
                            await ws.send_str(json.dumps({"type": "control_status", "deviceId": device_id,
                                "ok": False, "message": "Another phone has claimed this computer. Ask them to drop it first."}))
                        elif device.controller_client_id == client_id:
                            file_token = file_access_tokens.get((device_id, client_id)) or secrets.token_urlsafe(32)
                            file_access_tokens[(device_id, client_id)] = file_token
                            await send_control_status(client_id, True, "Computer claimed. You can control it until you drop it.", device_id, file_token)
                        else:
                            device.pending_controller_client_id = client_id
                            await device.websocket.send_str(json.dumps({"type": "control_request", "deviceId": device_id,
                                "clientId": client_id}))
                    elif mtype == "control_status" and ws_sessions.get(ws, {}).get("role") == "agent":
                        device_id = device_registry.device_id_for(ws)
                        device = device_registry.get(device_id)
                        if not device:
                            continue
                        client_id = str(data.get("clientId", ""))
                        if data.get("ok"):
                            device.controller_client_id = client_id
                            device.pending_controller_client_id = None
                            file_token = secrets.token_urlsafe(32)
                            file_access_tokens[(device_id, client_id)] = file_token
                        elif device.pending_controller_client_id == client_id or device.controller_client_id == client_id:
                            device.controller_client_id = None
                            device.pending_controller_client_id = None
                            file_access_tokens.pop((device_id, client_id), None)
                            file_token = None
                        else:
                            file_token = None
                        await send_control_status(client_id, bool(data.get("ok")), str(data.get("message", "")), device_id, file_token)
                        await broadcast_to_role("laptop", {"type": "control_status", "deviceId": device_id,
                            "clientId": client_id, "ok": bool(data.get("ok")), "message": str(data.get("message", ""))})
                        await publish_device_list()
                    elif mtype == "control_frame" and ws_sessions.get(ws, {}).get("role") == "agent":
                        device_id = device_registry.device_id_for(ws)
                        device = device_registry.get(device_id)
                        if device and device.controller_client_id:
                            await send_to_mobile(device.controller_client_id, {"type": "control_frame", "deviceId": device_id, "frame": data.get("frame", ""),
                                "width": data.get("width", 0), "height": data.get("height", 0),
                                "cursorX": data.get("cursorX", 0.5), "cursorY": data.get("cursorY", 0.5),
                                "mon": data.get("mon")})
                    elif mtype == "control_cursor" and ws_sessions.get(ws, {}).get("role") == "agent":
                        device_id = device_registry.device_id_for(ws)
                        device = device_registry.get(device_id)
                        if device and device.controller_client_id:
                            await send_to_mobile(device.controller_client_id, {"type": "control_cursor", "deviceId": device_id,
                                "cursorX": data.get("cursorX", 0.5), "cursorY": data.get("cursorY", 0.5),
                                "mon": data.get("mon")})
                    elif mtype == "control_input" and ws_sessions.get(ws, {}).get("role") == "mobile":
                        client_id = ws_sessions[ws]["clientId"]
                        device = device_registry.get(resolve_device_id(data.get("deviceId")))
                        if device and device.controller_client_id == client_id and not device.websocket.closed:
                            event = data.get("event", {})
                            if isinstance(event, dict) and event.get("type") in ("move", "click", "down", "up", "key", "keydown", "keyup", "hotkey", "text"):
                                await device.websocket.send_str(json.dumps({"type": "control_input", "deviceId": device.device_id, "event": event}))
                    elif mtype == "control_stop" and ws_sessions.get(ws, {}).get("role") == "mobile":
                        client_id = ws_sessions[ws]["clientId"]
                        device = device_registry.get(resolve_device_id(data.get("deviceId")))
                        if device and client_id in (device.controller_client_id, device.pending_controller_client_id):
                            device.controller_client_id = None
                            device.pending_controller_client_id = None
                            file_access_tokens.pop((device.device_id, client_id), None)
                            if not device.websocket.closed:
                                await device.websocket.send_str(json.dumps({"type": "control_stop", "deviceId": device.device_id, "clientId": client_id}))
                            await send_control_status(client_id, False, "Remote control stopped.", device.device_id)
                            await broadcast_to_role("laptop", {"type": "control_status", "deviceId": device.device_id,
                                "clientId": client_id, "ok": False, "message": "Remote control stopped."})
                            await publish_device_list()
                    if mtype == "ping_test":
                        await ws.send_str(json.dumps({"type": "pong_test", "echo": data}))
                except Exception as e:
                    log.error(f"[WS] Parse error: {e}")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                log.warning(f"[WS] Error: {ws.exception()}")
    finally:
        ws_clients.discard(ws)
        session = ws_sessions.pop(ws, None)
        device = device_registry.unregister(ws) if session and session.get("role") == "agent" else None
        if device:
            log.info("[HUB] Agent offline: %s", device.device_id)
            previous_controller = device.controller_client_id or device.pending_controller_client_id
            if previous_controller:
                file_access_tokens.pop((device.device_id, previous_controller), None)
                await send_control_status(previous_controller, False, "Computer disconnected from the hub.", device.device_id)
                await broadcast_to_role("laptop", {"type": "control_status", "deviceId": device.device_id,
                    "clientId": previous_controller, "ok": False, "message": "Computer disconnected from the hub."})
            device.controller_client_id = None
            device.pending_controller_client_id = None
            await publish_device_list()
        if session and session["role"] == "mobile":
            entry = mobile_connections.get(session["clientId"])
            last_mobile_socket = not entry or len(entry["sockets"]) <= 1
            if last_mobile_socket:
                for device in device_registry.online():
                    if session["clientId"] in (device.controller_client_id, device.pending_controller_client_id):
                        device.controller_client_id = None
                        device.pending_controller_client_id = None
                        file_access_tokens.pop((device.device_id, session["clientId"]), None)
                        if not device.websocket.closed:
                            await device.websocket.send_str(json.dumps({"type": "control_stop", "deviceId": device.device_id,
                                "clientId": session["clientId"]}))
                        await broadcast_to_role("laptop", {"type": "control_status", "deviceId": device.device_id,
                            "clientId": session["clientId"], "ok": False, "message": "Phone disconnected; remote control stopped."})
            if entry:
                entry["sockets"].discard(ws)
                if not entry["sockets"]:
                    mobile_connections.pop(session["clientId"], None)
                await publish_mobile_list()
        log.info(f"[WS] Phone disconnected: {client_ip} (remaining={len(ws_clients)})")
    return ws


async def send_to_mobile(client_id: str, payload: dict):
    entry = mobile_connections.get(client_id)
    if not entry:
        return
    text = json.dumps(payload)
    for phone_ws in list(entry["sockets"]):
        if not phone_ws.closed:
            await phone_ws.send_str(text)


async def send_control_status(client_id: str, ok: bool, message: str, device_id: str | None = None,
                              files_token: str | None = None):
    await send_to_mobile(client_id, {"type": "control_status", "deviceId": device_id,
                                     "ok": ok, "message": message, "filesToken": files_token})


# ─── Static File Server ────────────────────────────────────────────────────────
async def handle_static(request):
    path  = request.match_info.get("path", "index.html") or "index.html"
    fpath = os.path.join(PWA_DIR, path)
    if not os.path.exists(fpath):
        fpath = os.path.join(PWA_DIR, "index.html")
    ctype, _ = mimetypes.guess_type(fpath)
    return web.FileResponse(fpath, headers={"Content-Type": ctype or "text/html"})


# ─── IoT MQTT Subscriber (agent result listener) ──────────────────────────────
def _start_iot_subscriber():
    """
    Subscribe to the agent's result topic using AWS IoT SDK so the server
    receives the agent's 'pong' replies and broadcasts them to the phone.
    Runs in a background thread.
    """
    try:
        if PRODUCTION and not (HUB_IOT_CERT and HUB_IOT_PRIVATE_KEY):
            log.error("[IOT-SUB] Production requires a hub-specific IoT certificate and private key; not using a computer agent certificate")
            return
        # Add agent certs path
        agent_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent")
        sys.path.insert(0, agent_dir)

        from awscrt import mqtt
        from awsiot import mqtt_connection_builder

        cert_path = HUB_IOT_CERT or os.path.join(agent_dir, "certs", "device.pem.crt")
        key_path  = HUB_IOT_PRIVATE_KEY or os.path.join(agent_dir, "certs", "device.private.key")
        ca_path   = HUB_IOT_ROOT_CA or os.path.join(agent_dir, "certs", "AmazonRootCA1.pem")

        if not all(os.path.exists(p) for p in [cert_path, key_path, ca_path]):
            log.warning("[IOT-SUB] ⚠ Agent certs not found — IoT subscriber disabled")
            return

        conn = mqtt_connection_builder.mtls_from_path(
            endpoint=IOT_ENDPOINT,
            cert_filepath=cert_path,
            pri_key_filepath=key_path,
            ca_filepath=ca_path,
            client_id=HUB_CLIENT_ID,
            clean_session=True,
            port=443,
            http_proxy_options=None
        )

        future = conn.connect()
        future.result(timeout=10)
        log.info("[IOT-SUB] ✓ Server connected to IoT Core (listening for agent results)")

        global _iot_conn
        _iot_conn = conn

        result_topic = "rift/+/result"
        tokens_topic = f"rift/+/tokens"

        def on_message(topic, payload, dup, qos, retain, **kwargs):
            try:
                data = json.loads(payload.decode())
                device_id = data.get("deviceId") or (topic.split("/")[1] if len(topic.split("/")) > 2 else None)
                cmd  = data.get("command", "?")
                stat = data.get("status", "?")
                log.info(f"[IOT-SUB] ← Agent result: device={device_id} topic={topic} cmd={cmd} status={stat}")

                if cmd == "ping" and stat == "SUCCESS":
                    with pending_pings_lock:
                        ping = pending_pings.pop(data.get("cmdId"), {})
                    sync_broadcast({
                        "type": "log",
                        "deviceId": device_id,
                        "targetClientId": ping.get("clientId"),
                        "msg":  f"🟢 Pong! Agent {device_id or 'unknown'} replied at {time.strftime('%H:%M:%S')} — round trip via AWS IoT ✓"
                    })
                    sync_broadcast({"type": "agent_pong", "deviceId": device_id,
                                    "targetClientId": ping.get("clientId"), "data": data})
                elif data.get("jobId") and ("token" in data or "error" in data or data.get("done")):
                    job_id = str(data.get("jobId"))
                    device_id = data.get("deviceId") or pending_llm_jobs.get(job_id) or device_id
                    sync_broadcast({"type": "llm_token", "deviceId": device_id, "jobId": job_id,
                                    "token": data.get("token", ""), "error": data.get("error"),
                                    "done": data.get("done", False)})
                    if data.get("done"):
                        pending_llm_jobs.pop(job_id, None)
                else:
                    sync_broadcast({"type": "agent_result", "deviceId": device_id, "data": data})
            except Exception as e:
                log.error(f"[IOT-SUB] ✗ Message parse error: {e}")

        sub1, _ = conn.subscribe(topic=result_topic, qos=mqtt.QoS.AT_LEAST_ONCE, callback=on_message)
        sub1.result(timeout=10)
        sub2, _ = conn.subscribe(topic=tokens_topic, qos=mqtt.QoS.AT_LEAST_ONCE, callback=on_message)
        sub2.result(timeout=10)
        log.info(f"[IOT-SUB] ✓ Subscribed & confirmed to {result_topic}")
        log.info(f"[IOT-SUB] ✓ Subscribed & confirmed to {tokens_topic}")

        while not SHUTTING_DOWN and not SHUTDOWN_EVENT.wait(timeout=1):
            pass
        try:
            conn.disconnect().result(timeout=5)
        except Exception:
            log.exception("[IOT-SUB] Error while disconnecting")

    except Exception as e:
        log.warning(f"[IOT-SUB] ⚠ IoT subscriber not started: {e}")
        log.warning("[IOT-SUB]   → Agent pong replies won't show in real-time (polling still works)")


# ─── Background Polling Loops ──────────────────────────────────────────────────
async def job_polling_loop():
    """Polls DynamoDB every 5 seconds and pushes job status updates to phone."""
    import decimal
    log.info("[POLL] Job status polling loop started")
    while True:
        await asyncio.sleep(5)
        try:
            tbl    = dynamo.Table(JOBS_TABLE)
            result = tbl.scan(FilterExpression="attribute_exists(jobId)", Limit=10)
            items  = result.get("Items", [])
            chunks_tbl = dynamo.Table(CHUNKS_TABLE)
            for job in items:
                if job.get("status") != "PROCESSING" or int(job.get("chunksDone", 0)) < int(job.get("chunkCount", 0)):
                    continue
                try:
                    chunk_rows = chunks_tbl.query(
                        KeyConditionExpression=Key("jobId").eq(job["jobId"])
                    ).get("Items", [])
                    if len(chunk_rows) < int(job.get("chunkCount", 0)) or any(c.get("status") != "COMPLETED" for c in chunk_rows):
                        continue
                    partial = []
                    for chunk in chunk_rows:
                        key = chunk.get("outputKey")
                        if not key:
                            continue
                        obj = s3_cli.get_object(Bucket=BUCKET, Key=key)
                        partial.append(json.loads(obj["Body"].read().decode("utf-8")).get("result", {}))
                    if len(partial) != int(job.get("chunkCount", 0)):
                        continue
                    if job.get("type") == "monte_carlo":
                        samples = sum(int(r.get("samples", 0)) for r in partial)
                        inside = sum(int(r.get("inside_circle", 0)) for r in partial)
                        pi = 4.0 * inside / max(samples, 1)
                        final = {"type": "monte_carlo", "total_samples": samples,
                                 "total_inside_circle": inside, "final_estimated_pi": pi}
                    elif job.get("type") == "matrix_multiply":
                        final = {"type": "matrix_multiply", "chunks_merged": len(partial),
                                 "consolidated_checksum": round(sum(float(r.get("checksum", 0)) for r in partial), 4)}
                    else:
                        final = {"type": job.get("type"), "tiles_processed": len(partial),
                                 "outputs": [r.get("output_size_bytes", 0) for r in partial]}
                    from decimal import Decimal
                    final = {k: Decimal(str(v)) if isinstance(v, float) else v for k, v in final.items()}
                    tbl.update_item(Key={"jobId": job["jobId"]},
                        UpdateExpression="SET #s = :done, finalResult = :result, completedAt = :now",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":done": "COMPLETED", ":result": final, ":now": int(time.time())})
                    job["status"] = "COMPLETED"
                    job["finalResult"] = final
                except Exception as finalize_error:
                    log.error(f"[POLL] Could not finalize job {job.get('jobId')}: {finalize_error}")
            def clean(v):
                if isinstance(v, decimal.Decimal):
                    return int(v) if v == int(v) else float(v)
                if isinstance(v, dict):
                    return {k: clean(x) for k, x in v.items()}
                if isinstance(v, list):
                    return [clean(x) for x in v]
                return v
            items = [clean(i) for i in items]
            if ws_clients:
                await broadcast({"type": "jobs_update", "jobs": items})
        except Exception as e:
            log.error(f"[POLL] Error: {e}")


# ─── CORS Middleware ──────────────────────────────────────────────────────────
def _origin_allowed(request, origin: str) -> bool:
    origin = origin.rstrip("/")
    if origin in ALLOWED_ORIGINS:
        return True
    if ALLOWED_ORIGINS:
        return False
    forwarded_host = request.headers.get("X-Forwarded-Host", request.host)
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    return origin == f"{forwarded_proto}://{forwarded_host}"


async def handle_health(request):
    return web.json_response({"ok": True, "service": "rift-hub", "environment": ENVIRONMENT,
                             "authRequired": False})


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        origin = request.headers.get("Origin", "")
        if PRODUCTION and origin and not _origin_allowed(request, origin):
            raise web.HTTPForbidden(text="Origin is not allowed")
        return web.Response(headers={
            "Access-Control-Allow-Origin":  origin if PRODUCTION and origin else "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, X-RIFT-Client-ID, X-RIFT-FILES-TOKEN, Range",
            "Vary": "Origin"
        })
    if PRODUCTION:
        origin = request.headers.get("Origin", "").rstrip("/")
        if origin and not _origin_allowed(request, origin):
            raise web.HTTPForbidden(text="Origin is not allowed")
    try:
        response = await handler(request)
    except web.HTTPException as error:
        if request.path.startswith("/api/files/"):
            headers = {key: value for key, value in error.headers.items()
                       if key.lower() not in ("content-type", "content-length")}
            response = web.json_response({"error": error.text or error.reason}, status=error.status,
                                         headers=headers)
        else:
            raise
    except Exception:
        if request.path.startswith("/api/files/"):
            log.exception("[FILES] Unhandled file API error for %s %s", request.method, request.path)
            response = web.json_response({"error": "File operation failed on the hub. Check the hub log and retry."}, status=500)
        else:
            raise
    origin = request.headers.get("Origin", "")
    if not PRODUCTION:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and _origin_allowed(request, origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Content-Disposition"
    return response


# ─── App Setup & Entry ────────────────────────────────────────────────────────
async def main():
    global APP_LOOP
    APP_LOOP = asyncio.get_running_loop()
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/healthz",             handle_health)
    app.router.add_get("/ws",                 handle_ws)
    app.router.add_post("/api/ping",          handle_ping)
    app.router.add_post("/api/job",           handle_submit_job)
    app.router.add_get("/api/jobs",           handle_list_jobs)
    app.router.add_delete("/api/job/{jobId}", handle_delete_job)
    app.router.add_post("/api/camera_frame",  handle_camera_frame)
    app.router.add_post("/api/llm",           handle_llm_stream)
    app.router.add_get("/api/viewer",         handle_viewer_link)
    app.router.add_get("/api/status",         handle_laptop_status)
    app.router.add_get("/api/devices",        handle_list_devices)
    app.router.add_get("/api/files/{deviceId}/entries", handle_files_list)
    app.router.add_post("/api/files/{deviceId}/link/{fileId}", handle_files_link)
    app.router.add_post("/api/files/{deviceId}/preview/{fileId}", handle_files_video_preview)
    app.router.add_get("/api/files/{deviceId}/preview-jobs/{jobId}", handle_files_video_preview_status)
    app.router.add_get("/api/files/{deviceId}/content/{fileId}", handle_files_content)
    app.router.add_post("/api/files/{deviceId}/uploads", handle_files_upload_start)
    app.router.add_put("/api/files/{deviceId}/uploads/{uploadId}", handle_files_upload_chunk)
    app.router.add_post("/api/files/{deviceId}/uploads/{uploadId}/finish", handle_files_upload_finish)
    app.router.add_get("/",                   handle_static)
    app.router.add_get("/{path:.*}",          handle_static)

    runner = web.AppRunner(app, access_log=log)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", SERVER_PORT)

    # Start IoT subscriber in background thread (non-blocking)
    threading.Thread(target=_start_iot_subscriber, daemon=True).start()

    # Start async background polling tasks
    polling_task = asyncio.create_task(job_polling_loop())

    print("\n" + "=" * 64)
    print(">>> R.I.F.T. LOCAL SERVER RUNNING <<<")
    print("=" * 64)
    print(f"  Local origin: http://127.0.0.1:{SERVER_PORT} (loopback only)")
    print()
    print("  Run scripts/start_public_test.ps1 in another terminal and open its public URL.")
    print()
    print(f"  AWS:  profile={PROFILE}  region={REGION}")
    print(f"  IoT:  {IOT_ENDPOINT}")
    print(f"  LLM:  agents run {OLLAMA_MODEL} via local Ollama")
    print("=" * 64 + "\n")

    await site.start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop_event.wait()
    finally:
        global SHUTTING_DOWN
        SHUTTING_DOWN = True
        SHUTDOWN_EVENT.set()
        polling_task.cancel()
        await asyncio.gather(polling_task, return_exceptions=True)
        for ws in list(ws_clients):
            if not ws.closed:
                await ws.close(code=1001, message=b"hub shutting down")
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
