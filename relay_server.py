"""SwiftDesk Relay Server — WebSocket based.

Works on Render/Railway/any platform that exposes HTTP/443.
Each host registers → gets a 9-digit ID.
Viewers connect by that ID + password → relay pipes data between them.

Run locally:   python relay_server.py
Deploy:        set PORT env var (Render sets it automatically)
"""

import os
import asyncio
import json
import random
import logging

try:
    import websockets
    from websockets.server import serve
except ImportError:
    raise SystemExit("Install websockets:  pip install websockets")

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("relay")

# ── ID helpers ────────────────────────────────────────────────────────────────
def _fmt(n):
    s = str(n).zfill(9)
    return f"{s[:3]}-{s[3:6]}-{s[6:]}"

def _gen_id(existing):
    for _ in range(10000):
        fid = _fmt(random.randint(100_000_000, 999_999_999))
        if fid not in existing:
            return fid
    raise RuntimeError("ID space exhausted")

# ── Registry ─────────────────────────────────────────────────────────────────
# host_id -> {"ws": websocket, "password": str, "queue": asyncio.Queue}
registry = {}

# ── helpers ──────────────────────────────────────────────────────────────────
async def _send(ws, msg: dict):
    await ws.send(json.dumps(msg))

async def _recv(ws):
    raw = await ws.recv()
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)

# ── host handler ─────────────────────────────────────────────────────────────
async def handle_host(ws, data):
    password = str(data.get("password", "")).strip()
    if not password:
        await _send(ws, {"type": "error", "message": "Password required"})
        return

    host_id = _gen_id(registry)
    queue   = asyncio.Queue()          # viewer websocket arrives via queue
    registry[host_id] = {"ws": ws, "password": password, "queue": queue}
    log.info("Host registered: %s  (total=%d)", host_id, len(registry))

    await _send(ws, {"type": "registered", "id": host_id,
                     "success": True,
                     "message": f"Your SwiftDesk ID is {host_id}"})
    try:
        # wait for a viewer — heartbeats keep connection alive
        viewer_ws = None
        while True:
            try:
                viewer_ws = await asyncio.wait_for(queue.get(), timeout=25)
                break
            except asyncio.TimeoutError:
                # send heartbeat to keep Render from closing idle WS
                try:
                    await _send(ws, {"type": "heartbeat"})
                except Exception:
                    return

        # notify host
        await _send(ws, {"type": "connect_ok",
                          "message": "Viewer connected"})
        log.info("Session start: host=%s", host_id)
        await _pipe_session(ws, viewer_ws, host_id)
    finally:
        registry.pop(host_id, None)
        log.info("Host unregistered: %s", host_id)

# ── viewer handler ────────────────────────────────────────────────────────────
async def handle_viewer(ws, data):
    target_id = str(data.get("id", "")).strip()
    password  = str(data.get("password", "")).strip()

    entry = registry.get(target_id)
    if not entry:
        await _send(ws, {"type": "error",
                         "message": f"ID {target_id} not found or offline"})
        return
    if entry["password"] != password:
        await _send(ws, {"type": "error", "message": "Wrong password"})
        return

    # deliver viewer ws to host's queue
    await entry["queue"].put(ws)
    # notify viewer
    await _send(ws, {"type": "connect_ok",
                     "message": f"Connected to {target_id}"})
    log.info("Viewer connected to %s", target_id)
    # viewer just stays alive — piping is handled by host coroutine
    try:
        while True:
            await asyncio.sleep(30)
    except Exception:
        pass

# ── bidirectional pipe ────────────────────────────────────────────────────────
async def _pipe_session(host_ws, viewer_ws, host_id):
    """Forward raw frames between host and viewer until one disconnects."""

    async def fwd(src, dst, label):
        try:
            async for msg in src:
                if isinstance(msg, str):
                    try:
                        d = json.loads(msg)
                        if d.get("type") in ("heartbeat", "ping"):
                            continue
                    except Exception:
                        pass
                try:
                    await dst.send(msg)
                except Exception:
                    break
        except Exception as e:
            log.debug("pipe %s ended: %s", label, e)

    t1 = asyncio.create_task(fwd(host_ws,   viewer_ws, f"H→V({host_id})"))
    t2 = asyncio.create_task(fwd(viewer_ws, host_ws,   f"V→H({host_id})"))
    done, pending = await asyncio.wait(
        [t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()

    # notify surviving side
    for ws in (host_ws, viewer_ws):
        try:
            await _send(ws, {"type": "peer_left"})
        except Exception:
            pass
    log.info("Session ended: host=%s", host_id)

# ── main dispatcher ───────────────────────────────────────────────────────────
async def dispatcher(ws):
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=30)
    except Exception:
        return
    try:
        data = json.loads(first if isinstance(first, str) else first.decode())
    except Exception:
        return

    msg_type = data.get("type", "")
    if msg_type == "register":
        await handle_host(ws, data)
    elif msg_type == "connect":
        await handle_viewer(ws, data)
    else:
        await _send(ws, {"type": "error",
                         "message": "First message must be register or connect"})

# ── entry point ───────────────────────────────────────────────────────────────
async def main():
    port = int(os.environ.get("PORT", 5052))
    log.info("SwiftDesk relay starting on 0.0.0.0:%d  (WebSocket)", port)
    async with serve(dispatcher, "0.0.0.0", port,
                     ping_interval=20, ping_timeout=30):
        log.info("Relay server listening on 0.0.0.0:%d", port)
        await asyncio.Future()   # run forever

if __name__ == "__main__":
    asyncio.run(main())
