"""SwiftDesk relay client helpers — WebSocket based.

Both host.py and viewer.py import from here.
"""
import asyncio
import json
import threading
import base64

try:
    import websockets
except ImportError:
    raise SystemExit("Install websockets:  pip install websockets")

import config as C


def _ws_url():
    host = C.RELAY_HOST
    port = C.RELAY_PORT
    # use wss if connecting to a real domain (not localhost)
    scheme = "ws" if host in ("127.0.0.1", "localhost") else "wss"
    if scheme == "wss":
        return f"wss://{host}"          # Render exposes 443, no port needed
    return f"ws://{host}:{port}"


# ── JSON helpers ──────────────────────────────────────────────────────────────
def _enc(msg: dict) -> str:
    return json.dumps(msg)

def _dec(raw) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


# ── RelayConnection ───────────────────────────────────────────────────────────
class RelayConnection:
    """
    Thread-safe wrapper around a WebSocket relay connection.

    Usage (host):
        rc = RelayConnection()
        rc.connect_as_host(password, on_id, on_viewer_ready, on_error)

    Usage (viewer):
        rc = RelayConnection()
        rc.connect_as_viewer(host_id, password, on_ok, on_error)

    After session starts, use rc.send_bytes(data) / rc.recv_bytes() → bytes.
    """

    def __init__(self):
        self._ws      = None
        self._loop    = None
        self._thread  = None
        self._lock    = threading.Lock()
        self._recv_q  = asyncio.Queue()   # filled in _loop thread
        self._running = False

    # ── public API ────────────────────────────────────────────────────────────
    def connect_as_host(self, password, on_id, on_viewer_ready, on_error):
        """Non-blocking. Callbacks fired from background thread."""
        self._start_loop()
        asyncio.run_coroutine_threadsafe(
            self._host_coro(password, on_id, on_viewer_ready, on_error),
            self._loop)

    def connect_as_viewer(self, host_id, password, on_ok, on_error):
        self._start_loop()
        asyncio.run_coroutine_threadsafe(
            self._viewer_coro(host_id, password, on_ok, on_error),
            self._loop)

    def send_bytes(self, data: bytes):
        """Send raw bytes (JPEG frame, framed control msg, etc.)."""
        if not self._ws or not self._running:
            return
        # encode as base64 so it goes over JSON-friendly WS text frame
        msg = json.dumps({"type": "data",
                          "b64": base64.b64encode(data).decode()})
        asyncio.run_coroutine_threadsafe(
            self._ws.send(msg), self._loop)

    def recv_bytes(self) -> bytes:
        """Blocking recv — call from a dedicated thread."""
        future = asyncio.run_coroutine_threadsafe(
            self._recv_q.get(), self._loop)
        return future.result()          # blocks until data arrives

    def close(self):
        self._running = False
        if self._ws and self._loop:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)

    # ── internals ─────────────────────────────────────────────────────────────
    def _start_loop(self):
        self._loop   = asyncio.new_event_loop()
        self._recv_q = asyncio.Queue()
        self._running = True
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True)
        self._thread.start()

    async def _pump(self):
        """Read incoming messages and route them to recv_q or discard."""
        async for raw in self._ws:
            if not self._running:
                break
            try:
                d = _dec(raw)
            except Exception:
                continue
            t = d.get("type", "")
            if t == "data":
                data = base64.b64decode(d["b64"])
                await self._recv_q.put(data)
            elif t == "peer_left":
                await self._recv_q.put(b"__PEER_LEFT__")
                break
            # heartbeat / other control msgs → ignore

    async def _host_coro(self, password, on_id, on_viewer_ready, on_error):
        url = _ws_url()
        try:
            async with websockets.connect(
                    url, ping_interval=20, ping_timeout=30) as ws:
                self._ws = ws
                await ws.send(_enc({"type": "register",
                                    "password": password}))
                resp = _dec(await ws.recv())
                if resp.get("type") == "error":
                    on_error(resp.get("message", "Relay error"))
                    return
                host_id = resp.get("id", "?")
                on_id(host_id)

                # wait for viewer (heartbeats keep connection open)
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    d   = _dec(raw)
                    if d.get("type") == "connect_ok":
                        on_viewer_ready()
                        break
                    if d.get("type") == "error":
                        on_error(d.get("message", "?"))
                        return
                    # heartbeat → loop

                # session — pump incoming into recv_q
                await self._pump()
        except Exception as e:
            if self._running:
                on_error(str(e))

    async def _viewer_coro(self, host_id, password, on_ok, on_error):
        url = _ws_url()
        try:
            async with websockets.connect(
                    url, ping_interval=20, ping_timeout=30) as ws:
                self._ws = ws
                await ws.send(_enc({"type": "connect",
                                    "id": host_id,
                                    "password": password}))
                resp = _dec(await ws.recv())
                if resp.get("type") == "error":
                    on_error(resp.get("message", "Relay error"))
                    return
                on_ok()
                await self._pump()
        except Exception as e:
            if self._running:
                on_error(str(e))
