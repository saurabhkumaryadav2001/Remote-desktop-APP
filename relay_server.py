"""Relay / Rendezvous server — the heart of the AnyDesk-style system.

Each host registers and receives a unique 9-digit ID.
Viewers connect by that ID; the relay pipes data between both sockets.

Run this on any internet-accessible machine (VPS, Railway, Render, etc.):
    python relay_server.py

Environment variables (optional, override defaults):
    PORT      TCP port to listen on (default 5052)
    DEBUG     set to "1" for verbose logging
"""

import os
import random
import socket
import struct
import threading
import time
import json
import logging

# ── logging ──────────────────────────────────────────────────────────────────
_lvl = logging.DEBUG if os.environ.get("DEBUG") == "1" else logging.INFO
logging.basicConfig(
    level=_lvl,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("relay")

# ── protocol constants (must match protocol.py) ───────────────────────────────
_HEADER = struct.Struct('!BI')
HEADER_SIZE = _HEADER.size

MSG_RELAY_REGISTER   = 0x10
MSG_RELAY_REGISTERED = 0x11
MSG_RELAY_CONNECT    = 0x12
MSG_RELAY_CONNECT_OK = 0x13
MSG_RELAY_ERROR      = 0x14
MSG_RELAY_HEARTBEAT  = 0x15
MSG_RELAY_PEER_LEFT  = 0x16

# pass-through types (just piped between host ↔ viewer unchanged)
PASSTHROUGH_TYPES = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07}


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _recv_msg(sock):
    hdr = _recv_exact(sock, HEADER_SIZE)
    if hdr is None:
        return None
    mtype, length = _HEADER.unpack(hdr)
    payload = _recv_exact(sock, length) if length else b''
    if payload is None:
        return None
    return mtype, payload


def _send_msg(sock, mtype, payload):
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
    elif isinstance(payload, str):
        payload = payload.encode()
    elif payload is None:
        payload = b''
    frame = _HEADER.pack(mtype, len(payload)) + payload
    try:
        sock.sendall(frame)
        return True
    except OSError:
        return False


def _decode(payload):
    if not payload:
        return {}
    try:
        return json.loads(payload.decode())
    except Exception:
        return {}


# ── ID generation ─────────────────────────────────────────────────────────────
def _fmt_id(n):
    s = str(n).zfill(9)
    return f"{s[:3]}-{s[3:6]}-{s[6:]}"


def _gen_id(existing):
    for _ in range(10000):
        n = random.randint(100_000_000, 999_999_999)
        fid = _fmt_id(n)
        if fid not in existing:
            return fid
    raise RuntimeError("ID space exhausted")


# ── Registry ──────────────────────────────────────────────────────────────────
class Registry:
    """Thread-safe map of ID → HostEntry."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hosts = {}   # id -> HostEntry

    def register(self, entry):
        with self._lock:
            entry.id = _gen_id(self._hosts)
            self._hosts[entry.id] = entry
            log.info("Registered host %s from %s", entry.id, entry.addr)

    def unregister(self, entry_id):
        with self._lock:
            self._hosts.pop(entry_id, None)
            log.info("Unregistered host %s", entry_id)

    def get(self, entry_id):
        with self._lock:
            return self._hosts.get(entry_id)

    def count(self):
        with self._lock:
            return len(self._hosts)


registry = Registry()


# ── HostEntry ────────────────────────────────────────────────────────────────
class HostEntry:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.id = None
        self.password = None
        self.viewer_sock = None          # set when a viewer connects
        self._viewer_lock = threading.Lock()

    def set_viewer(self, vsock):
        with self._viewer_lock:
            if self.viewer_sock is not None:
                return False            # already in session
            self.viewer_sock = vsock
            return True

    def clear_viewer(self):
        with self._viewer_lock:
            self.viewer_sock = None


# ── Pipe thread ──────────────────────────────────────────────────────────────
def _pipe(src, dst, label, stop_event):
    """Forward raw bytes from src to dst until closed."""
    try:
        while not stop_event.is_set():
            hdr = _recv_exact(src, HEADER_SIZE)
            if hdr is None:
                break
            mtype, length = _HEADER.unpack(hdr)
            payload = _recv_exact(src, length) if length else b''
            if payload is None:
                break
            if mtype == MSG_RELAY_HEARTBEAT:
                continue                # swallow heartbeats
            frame = hdr + (payload if payload else b'')
            try:
                dst.sendall(frame)
            except OSError:
                break
    except Exception as e:
        log.debug("pipe %s error: %s", label, e)
    finally:
        stop_event.set()
        log.debug("pipe %s done", label)


# ── Handler: host ─────────────────────────────────────────────────────────────
def handle_host(sock, addr):
    log.debug("host candidate from %s", addr)
    sock.settimeout(30)

    # expect REGISTER
    msg = _recv_msg(sock)
    if msg is None or msg[0] != MSG_RELAY_REGISTER:
        _send_msg(sock, MSG_RELAY_ERROR, {"message": "Expected REGISTER"})
        sock.close()
        return
    data = _decode(msg[1])
    password = str(data.get("password", ""))
    if not password:
        _send_msg(sock, MSG_RELAY_ERROR, {"message": "Password required"})
        sock.close()
        return

    entry = HostEntry(sock, addr)
    entry.password = password
    registry.register(entry)

    _send_msg(sock, MSG_RELAY_REGISTERED,
              {"id": entry.id, "success": True,
               "message": f"Your ID is {entry.id}"})
    log.info("Host %s registered (addr=%s, hosts_total=%d)",
             entry.id, addr, registry.count())

    sock.settimeout(None)

    # wait for a viewer to be patched in — heartbeat loop
    try:
        while True:
            msg = _recv_msg(sock)
            if msg is None:
                break
            mtype, _ = msg
            if mtype == MSG_RELAY_HEARTBEAT:
                continue
            # any other message before viewer arrives → ignore
    except Exception:
        pass
    finally:
        registry.unregister(entry.id)
        with entry._viewer_lock:
            vsock = entry.viewer_sock
        if vsock:
            _send_msg(vsock, MSG_RELAY_PEER_LEFT, {})
            try:
                vsock.close()
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass
        log.info("Host %s disconnected", entry.id)


# ── Handler: viewer ───────────────────────────────────────────────────────────
def handle_viewer(sock, addr):
    log.debug("viewer candidate from %s", addr)
    sock.settimeout(20)

    # expect CONNECT
    msg = _recv_msg(sock)
    if msg is None or msg[0] != MSG_RELAY_CONNECT:
        _send_msg(sock, MSG_RELAY_ERROR, {"message": "Expected CONNECT"})
        sock.close()
        return
    data = _decode(msg[1])
    target_id = str(data.get("id", "")).strip()
    password = str(data.get("password", ""))

    entry = registry.get(target_id)
    if entry is None:
        _send_msg(sock, MSG_RELAY_ERROR,
                  {"message": f"ID {target_id} not found or offline"})
        sock.close()
        return
    if entry.password != password:
        _send_msg(sock, MSG_RELAY_ERROR, {"message": "Wrong password"})
        sock.close()
        return
    if not entry.set_viewer(sock):
        _send_msg(sock, MSG_RELAY_ERROR,
                  {"message": "Host already in a session"})
        sock.close()
        return

    # notify both sides
    _send_msg(entry.sock, MSG_RELAY_CONNECT_OK,
              {"message": f"Viewer {addr[0]} connected"})
    _send_msg(sock, MSG_RELAY_CONNECT_OK,
              {"message": f"Connected to host {target_id}"})
    log.info("Session started: host=%s viewer=%s", target_id, addr)

    sock.settimeout(None)
    entry.sock.settimeout(None)

    # bidirectional pipe
    stop = threading.Event()
    t1 = threading.Thread(
        target=_pipe, args=(entry.sock, sock, f"H→V({target_id})", stop),
        daemon=True)
    t2 = threading.Thread(
        target=_pipe, args=(sock, entry.sock, f"V→H({target_id})", stop),
        daemon=True)
    t1.start()
    t2.start()
    stop.wait()

    entry.clear_viewer()
    # notify surviving side
    try:
        _send_msg(entry.sock, MSG_RELAY_PEER_LEFT, {})
    except OSError:
        pass
    try:
        _send_msg(sock, MSG_RELAY_PEER_LEFT, {})
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
    log.info("Session ended: host=%s", target_id)


# ── Handshake dispatcher ──────────────────────────────────────────────────────
# First message decides: REGISTER → host, CONNECT → viewer.
def dispatch(sock, addr):
    sock.settimeout(30)
    try:
        msg = _recv_msg(sock)
    except OSError:
        sock.close()
        return
    if msg is None:
        sock.close()
        return
    mtype, payload = msg

    # Re-wrap the already-read message so handlers can re-read it.
    # We do this by injecting a small buffer socket-like wrapper.
    class _Prepend:
        """Socket wrapper that prepends one already-read frame."""
        def __init__(self, raw_sock, frame):
            self._s = raw_sock
            self._buf = bytearray(frame)

        def recv(self, n):
            if self._buf:
                chunk = bytes(self._buf[:n])
                del self._buf[:n]
                return chunk
            return self._s.recv(n)

        def sendall(self, data):
            return self._s.sendall(data)

        def settimeout(self, t):
            self._s.settimeout(t)

        def close(self):
            self._s.close()

        @property
        def _viewer_lock(self):
            return self._s._viewer_lock if hasattr(self._s, '_viewer_lock') else None

    # Rebuild the original frame bytes
    if isinstance(payload, dict):
        payload_bytes = json.dumps(payload).encode()
    else:
        payload_bytes = payload if payload else b''
    frame = _HEADER.pack(mtype, len(payload_bytes)) + payload_bytes
    wrapped = _Prepend(sock, frame)

    if mtype == MSG_RELAY_REGISTER:
        handle_host(wrapped, addr)
    elif mtype == MSG_RELAY_CONNECT:
        handle_viewer(wrapped, addr)
    else:
        _send_msg(sock, MSG_RELAY_ERROR,
                  {"message": "Unknown first message"})
        sock.close()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    port = int(os.environ.get("PORT", 5052))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(50)
    log.info("Relay server listening on 0.0.0.0:%d", port)

    while True:
        try:
            conn, addr = srv.accept()
        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        threading.Thread(target=dispatch, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
