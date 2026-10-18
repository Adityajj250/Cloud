"""Verify public agent/phone registration and screen-control relay on a hub URL."""

import argparse
import asyncio
import json
import uuid

import aiohttp


async def next_type(ws: aiohttp.ClientWebSocketResponse, wanted: str) -> dict:
    while True:
        message = await ws.receive(timeout=15)
        if message.type == aiohttp.WSMsgType.TEXT:
            payload = json.loads(message.data)
            if payload.get("type") == wanted:
                return payload
            if payload.get("type") == "device_error":
                raise RuntimeError(payload.get("message", "device registration rejected"))
        elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            raise RuntimeError(f"WebSocket closed before receiving {wanted}")


async def run(base_url: str) -> None:
    base_url = base_url.rstrip("/")
    ws_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"
    test_id = uuid.uuid4().hex[:12]
    device_id = f"rift-smoke-{test_id}"
    phone_id = f"smoke-phone-{test_id}"
    print(f"Temporary smoke-test device ID: {device_id}")

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(base_url + "/healthz") as response:
            if response.status != 200:
                raise RuntimeError(f"health check returned HTTP {response.status}")
            print("PASS public HTTPS health check")

        async with session.get(base_url + "/api/devices") as response:
            if response.status != 200:
                raise RuntimeError(f"public API returned HTTP {response.status}")
            await response.read()
            print("PASS unauthenticated public API access")

        async with session.ws_connect(ws_url, heartbeat=10) as agent, \
                session.ws_connect(ws_url, heartbeat=10, protocols=["rift.v1"]) as phone, \
                session.ws_connect(ws_url, heartbeat=10, protocols=["rift.v1"]) as laptop:
            await agent.send_json({"type": "identify", "role": "agent", "clientId": device_id,
                                   "deviceId": device_id, "name": "Temporary smoke-test agent", "capabilities": []})
            await next_type(agent, "device_update")
            print("PASS agent WebSocket registration without hub token")

            await phone.send_json({"type": "identify", "role": "mobile", "clientId": phone_id,
                                   "name": "Temporary smoke-test phone"})
            devices = await next_type(phone, "device_list")
            if not any(item.get("deviceId") == device_id or item.get("id") == device_id
                       for item in devices.get("devices", [])):
                raise RuntimeError("phone device list did not include the registered agent")
            print("PASS phone sees the registered agent")

            await laptop.send_json({"type": "identify", "role": "laptop", "clientId": f"smoke-laptop-{test_id}",
                                    "name": "Temporary smoke-test laptop"})
            await next_type(laptop, "mobile_clients")
            await next_type(laptop, "device_list")
            await phone.send_json({"type": "ping_laptop", "sentAt": 1})
            await next_type(phone, "laptop_pong")
            ping_id = f"smoke-ping-{test_id}"
            await laptop.send_json({"type": "ping_mobile", "clientId": phone_id, "pingId": ping_id})
            phone_ping = await next_type(phone, "ping_from_laptop")
            await phone.send_json({"type": "mobile_pong", "clientId": phone_id,
                                   "pingId": phone_ping["pingId"], "name": "Temporary smoke-test phone"})
            await next_type(laptop, "mobile_pong")
            print("PASS bidirectional phone/laptop ping")

            await phone.send_json({"type": "control_request", "deviceId": device_id, "pin": "000000"})
            request = await next_type(agent, "control_request")
            await agent.send_json({"type": "control_status", "deviceId": device_id,
                                   "clientId": request["clientId"], "ok": True, "message": "smoke test"})
            await next_type(phone, "control_status")
            print("PASS control request and grant route")

            await agent.send_json({"type": "control_frame", "deviceId": device_id, "frame": "smoke-frame",
                                   "width": 1280, "height": 720, "cursorX": 0.4, "cursorY": 0.6})
            frame = await next_type(phone, "control_frame")
            if frame.get("frame") != "smoke-frame":
                raise RuntimeError("screen-frame relay payload did not match")
            print("PASS agent-to-phone screen-frame relay")

            await phone.send_json({"type": "control_input", "deviceId": device_id,
                                   "event": {"type": "keydown", "key": "w"}})
            input_event = await next_type(agent, "control_input")
            if input_event.get("event", {}).get("key") != "w":
                raise RuntimeError("phone-to-agent input relay payload did not match")
            print("PASS phone-to-agent keyboard-input relay")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Public or local hub base URL")
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == "__main__":
    main()
