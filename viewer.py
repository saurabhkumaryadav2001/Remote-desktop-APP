"""SwiftDesk — Viewer mode.

Run:  python viewer.py
"""
import io
import socket
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

import config as C
from ui_kit import (
    TitleBar, Card, SwiftButton, SwiftLabel,
    StatusBar, ActivityLog, EntryField,
)
from protocol import (
    make_sender, recv_message, decode_json,
    MSG_AUTH_REQUEST, MSG_AUTH_RESPONSE, MSG_FRAME_INFO,
    MSG_SCREEN_FRAME, MSG_MOUSE_EVENT, MSG_KEY_EVENT, MSG_DISCONNECT,
)
from relay_client import RelayConnection


# ─── Connect Dialog ──────────────────────────────────────────────────────────

class ConnectDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("New Connection — SwiftDesk")
        self.geometry("380x390")
        self.configure(bg=C.CLR_BG)
        self.resizable(False, False)
        self.result = None

        TitleBar(self, subtitle="Start a remote session", height=54).pack(fill="x")

        body = tk.Frame(self, bg=C.CLR_BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        # mode tabs
        self._mode = tk.StringVar(value="relay")
        mc = Card(body)
        mc.pack(fill="x", pady=(0, 10))
        tabs = tk.Frame(mc, bg=C.CLR_BG,
                        highlightbackground=C.CLR_BORDER,
                        highlightthickness=1)
        tabs.pack(fill="x", padx=12, pady=10)
        for txt, val in [("By ID  (Internet)", "relay"),
                         ("By IP  (LAN / Direct)", "direct")]:
            tk.Radiobutton(tabs, text=txt, variable=self._mode, value=val,
                           bg=C.CLR_BG, fg=C.CLR_TEXT,
                           selectcolor=C.CLR_ACCENT,
                           activebackground=C.CLR_BG,
                           font=("Segoe UI", 9),
                           command=self._refresh,
                           pady=8, padx=12).pack(side="left", expand=True)

        # relay fields
        self._relay_card = Card(body)
        self._id_ef = EntryField(self._relay_card,
                                  label="Host ID",
                                  placeholder="e.g.  123-456-789",
                                  width=26)
        self._id_ef.pack(fill="x", padx=12, pady=(10, 4))
        self._rpw_ef = EntryField(self._relay_card,
                                   label="Password",
                                   show="●", width=26)
        self._rpw_ef.pack(fill="x", padx=12, pady=(0, 12))
        # relay server is internal — not shown to user

        # direct fields
        self._direct_card = Card(body)
        self._ip_ef   = EntryField(self._direct_card,
                                    label="Host IP",
                                    initial="127.0.0.1",
                                    width=22)
        self._ip_ef.pack(fill="x", padx=12, pady=(10, 4))
        self._port_ef = EntryField(self._direct_card,
                                    label="Port",
                                    initial=str(C.DEFAULT_HOST_PORT),
                                    width=10)
        self._port_ef.pack(fill="x", padx=12, pady=(0, 4))
        self._dpw_ef  = EntryField(self._direct_card,
                                    label="Password",
                                    show="●", width=22)
        self._dpw_ef.pack(fill="x", padx=12, pady=(0, 12))

        # connect button
        SwiftButton(body, text="Connect Now",
                    colour=C.CLR_ACCENT, width=200, height=44,
                    command=self._ok).pack(pady=8)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(parent)
        self.grab_set()
        self._refresh()

    def _refresh(self):
        if self._mode.get() == "relay":
            self._direct_card.pack_forget()
            self._relay_card.pack(fill="x", pady=(0, 10))
        else:
            self._relay_card.pack_forget()
            self._direct_card.pack(fill="x", pady=(0, 10))

    def _ok(self):
        if self._mode.get() == "relay":
            self.result = ("relay",
                           self._id_ef.get().strip(),
                           self._rpw_ef.get().strip(),
                           C.RELAY_HOST, C.RELAY_PORT)
        else:
            try:
                port = int(self._port_ef.get())
            except ValueError:
                return
            self.result = ("direct",
                           self._ip_ef.get().strip(),
                           port,
                           self._dpw_ef.get().strip())
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ─── Viewer App ──────────────────────────────────────────────────────────────

class ViewerApp:
    def __init__(self, root):
        self.root = root
        root.title(C.VIEWER_TITLE)
        root.geometry("1000x660")
        root.configure(bg=C.CLR_BG)

        self.sock          = None
        self.send          = None
        self._relay_conn   = None
        self.running       = False
        self.host_w        = 0
        self.host_h        = 0
        self.current_photo = None
        self._last_move    = 0.0

        self._build_ui()

    def _build_ui(self):
        # ── top bar ──
        topbar = tk.Frame(self.root, bg=C.CLR_SURFACE,
                          highlightbackground=C.CLR_BORDER,
                          highlightthickness=1, height=52)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        # logo area
        logo_c = tk.Canvas(topbar, width=160, height=52,
                           bg=C.CLR_SURFACE, highlightthickness=0)
        logo_c.pack(side="left")
        logo_c.bind("<Configure>", lambda _: self._draw_topbar_logo(logo_c))
        self.root.after(80, lambda: self._draw_topbar_logo(logo_c))

        # status
        self._status_bar = StatusBar(topbar)
        self._status_bar.pack(side="left", padx=12)

        # right buttons
        rbf = tk.Frame(topbar, bg=C.CLR_SURFACE)
        rbf.pack(side="right", padx=10, pady=8)
        self._disconnect_btn = SwiftButton(rbf, text="Disconnect",
                                           colour=C.CLR_RED,
                                           width=130, height=36,
                                           command=self.disconnect)
        self._disconnect_btn.pack(side="right", padx=4)
        self._disconnect_btn.configure(state="disabled")

        self._connect_btn = SwiftButton(rbf, text="New Connection",
                                        colour=C.CLR_ACCENT,
                                        width=150, height=36,
                                        command=self.connect)
        self._connect_btn.pack(side="right", padx=4)

        # ── canvas ──
        self.canvas = tk.Canvas(self.root, bg=C.CLR_BG,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # idle screen
        self._draw_idle_screen()

        # bindings
        self.canvas.bind("<Configure>",      self._on_canvas_resize)
        self.canvas.bind("<Motion>",          self._on_motion)
        self.canvas.bind("<ButtonPress-1>",
                         lambda e: self._mouse(e, "down", "left"))
        self.canvas.bind("<ButtonPress-2>",
                         lambda e: self._mouse(e, "down", "middle"))
        self.canvas.bind("<ButtonPress-3>",
                         lambda e: self._mouse(e, "down", "right"))
        self.canvas.bind("<ButtonRelease-1>",
                         lambda e: self._mouse(e, "up", "left"))
        self.canvas.bind("<ButtonRelease-2>",
                         lambda e: self._mouse(e, "up", "middle"))
        self.canvas.bind("<ButtonRelease-3>",
                         lambda e: self._mouse(e, "up", "right"))
        self.canvas.bind("<MouseWheel>",      self._on_wheel)
        self.canvas.bind("<KeyPress>",        self._on_key)
        self.canvas.bind("<KeyRelease>",      self._on_key_release)

    def _draw_topbar_logo(self, canvas):
        canvas.delete("all")
        from ui_kit import draw_logo
        draw_logo(canvas, 12, 12, size=28)
        canvas.create_text(48, 16, text="SwiftDesk",
                           fill=C.CLR_WHITE,
                           font=("Segoe UI", 12, "bold"),
                           anchor="w")
        canvas.create_text(48, 34, text="Remote Viewer",
                           fill=C.CLR_MUTED,
                           font=("Segoe UI", 8),
                           anchor="w")

    def _draw_idle_screen(self):
        self.canvas.delete("screen_bg", "idle_text")
        cw = max(self.canvas.winfo_width(), 400)
        ch = max(self.canvas.winfo_height(), 300)
        cx, cy = cw // 2, ch // 2

        # subtle grid
        for xi in range(0, cw, 40):
            self.canvas.create_line(xi, 0, xi, ch,
                                    fill="#1a1d27", tags="screen_bg")
        for yi in range(0, ch, 40):
            self.canvas.create_line(0, yi, cw, yi,
                                    fill="#1a1d27", tags="screen_bg")

        # lightning icon (big)
        from ui_kit import draw_logo
        draw_logo(self.canvas, cx - 20, cy - 60, size=56)

        self.canvas.create_text(cx, cy + 10,
                                text="SwiftDesk",
                                fill=C.CLR_TEXT,
                                font=("Segoe UI", 22, "bold"),
                                tags="idle_text")
        self.canvas.create_text(cx, cy + 38,
                                text="Click  'New Connection'  to start a session",
                                fill=C.CLR_MUTED,
                                font=("Segoe UI", 11),
                                tags="idle_text")

    def _on_canvas_resize(self, _):
        if not self.running:
            self._draw_idle_screen()

    # ─── connection ──────────────────────────────────────────────────────────
    def connect(self):
        if self.running:
            return
        dlg = ConnectDialog(self.root)
        self.root.wait_window(dlg)
        if not dlg.result:
            return
        mode = dlg.result[0]
        self._connect_btn.configure(state="disabled")
        self._status_bar.update("Connecting…", "waiting")
        if mode == "relay":
            _, host_id, pw, rhost, rport = dlg.result
            threading.Thread(target=self._connect_relay,
                             args=(host_id, pw, rhost, rport),
                             daemon=True).start()
        else:
            _, ip, port, pw = dlg.result
            threading.Thread(target=self._connect_direct,
                             args=(ip, port, pw),
                             daemon=True).start()

    # ── relay ────────────────────────────────────────────────────────────────
    def _connect_relay(self, host_id, pw, rhost, rport):
        rc = RelayConnection()
        self._relay_conn = rc

        def on_ok():
            self.running = True
            self.root.after(0, lambda: self._on_connected(
                f"{host_id}  (relay)"))
            threading.Thread(target=self._relay_recv_loop,
                             args=(rc,), daemon=True).start()

        def on_error(msg):
            self._fail(f"Relay: {msg}")

        rc.connect_as_viewer(host_id, pw, on_ok, on_error)

    def _relay_recv_loop(self, rc: RelayConnection):
        """Receive framed messages from relay and handle them."""
        import struct as _struct
        HDR  = _struct.Struct('!BI')
        HSIZ = HDR.size
        buf  = bytearray()

        from protocol import (MSG_FRAME_INFO, MSG_SCREEN_FRAME,
                               MSG_MOUSE_EVENT, MSG_KEY_EVENT, MSG_DISCONNECT)

        while self.running:
            try:
                chunk = rc.recv_bytes()
            except Exception:
                self.root.after(0, self._on_closed); return
            if chunk == b"__PEER_LEFT__":
                self.root.after(0, self._on_closed); return
            buf.extend(chunk)
            while len(buf) >= HSIZ:
                mtype, length = HDR.unpack_from(bytes(buf[:HSIZ]))
                total = HSIZ + length
                if len(buf) < total:
                    break
                payload = bytes(buf[HSIZ:total])
                del buf[:total]
                if mtype == MSG_FRAME_INFO:
                    info = decode_json(payload)
                    self.host_w = int(info["width"])
                    self.host_h = int(info["height"])
                elif mtype == MSG_SCREEN_FRAME:
                    self._handle_frame(payload)
                elif mtype == MSG_DISCONNECT:
                    self.root.after(0, self._on_closed); return

    def _send_relay(self, mtype, payload):
        """Send a framed message over the relay connection."""
        if not self._relay_conn or not self.running:
            return
        import json as _json
        from protocol import _HEADER
        if isinstance(payload, dict):
            payload = _json.dumps(payload).encode()
        frame = _HEADER.pack(mtype, len(payload)) + payload
        self._relay_conn.send_bytes(frame)

    # ── direct ───────────────────────────────────────────────────────────────
    def _connect_direct(self, ip, port, pw):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(C.CONNECT_TIMEOUT)
            sock.connect((ip, port))
            sock.settimeout(None)
        except Exception as e:
            self._fail(f"Connection failed: {e}"); return
        self.sock    = sock
        self.send    = make_sender(sock)
        self.running = True
        threading.Thread(target=self._handshake_direct,
                         args=(ip, port, pw), daemon=True).start()

    def _handshake_direct(self, ip, port, pw):
        try:
            self.send(MSG_AUTH_REQUEST, {"password": pw})
            msg = recv_message(self.sock)
            if msg is None:
                self._fail("Host closed connection"); return
            mtype, payload = msg
            if mtype != MSG_AUTH_RESPONSE:
                self._fail("Unexpected response"); return
            res = decode_json(payload)
            if not res.get("success"):
                self._fail(res.get("message", "Auth failed")); return
            self.root.after(0, lambda: self._on_connected(f"{ip}:{port}"))
            threading.Thread(target=self._recv_loop, daemon=True).start()
        except Exception as e:
            self._fail(str(e))

    # ── shared ───────────────────────────────────────────────────────────────
    def _on_connected(self, label):
        self._status_bar.update(f"Connected  ·  {label}", "connected")
        self._disconnect_btn.configure(state="normal")
        self.canvas.delete("screen_bg", "idle_text")
        self.canvas.focus_set()

    def _fail(self, msg):
        self.root.after(0, lambda: self._status_bar.update(
            f"Failed: {msg}", "error"))
        self._cleanup_socket()

    def _cleanup_socket(self):
        self.running = False
        if self._relay_conn:
            try: self._relay_conn.close()
            except Exception: pass
            self._relay_conn = None
        if self.sock:
            try: self.sock.close()
            except OSError: pass
            self.sock = None
        self.root.after(0, lambda: self._connect_btn.configure(state="normal"))
        self.root.after(0, lambda: self._disconnect_btn.configure(state="disabled"))

    def _recv_loop(self):
        while self.running:
            msg = recv_message(self.sock)
            if msg is None:
                self.root.after(0, self._on_closed); return
            mtype, payload = msg
            if mtype == MSG_FRAME_INFO:
                info = decode_json(payload)
                self.host_w = int(info["width"])
                self.host_h = int(info["height"])
            elif mtype == MSG_SCREEN_FRAME:
                self._handle_frame(payload)
            elif mtype in (MSG_DISCONNECT, MSG_RELAY_PEER_LEFT):
                self.root.after(0, self._on_closed); return
            elif mtype == MSG_RELAY_HEARTBEAT:
                continue

    def _on_closed(self):
        self._status_bar.update("Disconnected", "idle")
        self._cleanup_socket()
        self._draw_idle_screen()

    def _handle_frame(self, payload):
        try:
            img = Image.open(io.BytesIO(payload))
        except Exception:
            return
        if not self.host_w:
            self.host_w, self.host_h = img.size
        self.root.after(0, lambda: self._draw(img))

    def _draw(self, img):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            cw, ch = img.size
        scale = min(cw / img.width, ch / img.height, 1.0)
        dw = max(1, int(img.width  * scale))
        dh = max(1, int(img.height * scale))
        disp = img.resize((dw, dh))
        x = (cw - dw) // 2
        y = (ch - dh) // 2
        self.current_photo = ImageTk.PhotoImage(disp)
        self.canvas.delete("frame")
        self.canvas.create_image(x, y, anchor="nw",
                                 image=self.current_photo,
                                 tags="frame")

    # ─── input ───────────────────────────────────────────────────────────────
    def _to_host_coords(self, event):
        if not self.host_w:
            return None, None
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        scale = min(cw / self.host_w, ch / self.host_h, 1.0)
        dw = self.host_w * scale
        dh = self.host_h * scale
        ox = (cw - dw) / 2
        oy = (ch - dh) / 2
        if not (ox <= event.x <= ox + dw and oy <= event.y <= oy + dh):
            return None, None
        return (event.x - ox) / scale, (event.y - oy) / scale

    def _on_motion(self, event):
        if not self.running: return
        now = time.monotonic()
        if (now - self._last_move) * 1000 < C.MOVE_THROTTLE_MS: return
        self._last_move = now
        c = self._to_host_coords(event)
        if c[0] is None: return
        self._send_event(MSG_MOUSE_EVENT,
                         {"x": c[0], "y": c[1], "action": "move"})

    def _mouse(self, event, action, button):
        if not self.running: return
        c = self._to_host_coords(event)
        if c[0] is None: return
        self._send_event(MSG_MOUSE_EVENT,
                         {"x": c[0], "y": c[1],
                          "action": action, "button": button})

    def _on_wheel(self, event):
        if not self.running: return
        c = self._to_host_coords(event)
        if c[0] is None: return
        self._send_event(MSG_MOUSE_EVENT,
                         {"x": c[0], "y": c[1], "action": "scroll",
                          "dx": 0, "dy": -1 if event.delta > 0 else 1})

    def _on_key(self, event):
        if not self.running: return
        self._send_event(MSG_KEY_EVENT,
                         {"keysym": event.keysym,
                          "char":   event.char or "",
                          "pressed": True})

    def _on_key_release(self, event):
        if not self.running: return
        self._send_event(MSG_KEY_EVENT,
                         {"keysym": event.keysym,
                          "char":   event.char or "",
                          "pressed": False})

    def _send_event(self, mtype, payload):
        """Route to relay or direct socket depending on connection type."""
        if self._relay_conn:
            self._send_relay(mtype, payload)
        elif self.send:
            self.send(mtype, payload)

    def disconnect(self):
        if self.running:
            if self._relay_conn:
                self._send_relay(MSG_DISCONNECT, {})
            elif self.send:
                try: self.send(MSG_DISCONNECT, {})
                except Exception: pass
        self._on_closed()


def main():
    root = tk.Tk()
    ViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
