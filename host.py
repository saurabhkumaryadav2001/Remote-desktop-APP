"""SwiftDesk — Host mode.

Run:  python host.py
"""
import socket
import threading
import time
import random
import string
import tkinter as tk

import config as C
from ui_kit import (
    TitleBar, Card, SwiftButton,
    IDDisplay, StatusBar, ActivityLog, PasswordBadge, EntryField,
)
from protocol import (
    make_sender, recv_message, decode_json,
    MSG_AUTH_REQUEST, MSG_AUTH_RESPONSE, MSG_FRAME_INFO,
    MSG_SCREEN_FRAME, MSG_MOUSE_EVENT, MSG_KEY_EVENT, MSG_DISCONNECT,
)
from relay_client import RelayConnection
from screen_capture import ScreenCapture
from input_control import InputController


def gen_password(n=6):
    return ''.join(random.choices(string.digits, k=n))


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class HostApp:
    def __init__(self, root):
        self.root = root
        root.title(C.HOST_TITLE)
        root.geometry("460x640")
        root.configure(bg=C.CLR_BG)
        root.resizable(False, False)

        self.password = C.AUTH_PASSWORD or gen_password()

        # relay state
        self._relay_conn    = None
        self._relay_running = False
        self._my_id         = ""

        # direct state
        self._direct_sock    = None
        self._direct_running = False

        self._mode = tk.StringVar(value="relay")
        self._build_ui()

    # ─── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # title bar
        TitleBar(self.root, subtitle="Share your screen securely",
                 height=60).pack(fill="x")

        # scrollable body
        outer = tk.Frame(self.root, bg=C.CLR_BG)
        outer.pack(fill="both", expand=True)

        self._body = tk.Frame(outer, bg=C.CLR_BG)
        self._body.pack(fill="both", expand=True, padx=16, pady=8)

        PAD = dict(fill="x", pady=5)

        # ── mode card ──
        mc = Card(self._body)
        mc.pack(**PAD)
        tk.Label(mc, text="CONNECTION MODE", bg=C.CLR_SURFACE,
                 fg=C.CLR_MUTED, font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=12, pady=(10, 2))
        tabs = tk.Frame(mc, bg=C.CLR_BG,
                        highlightbackground=C.CLR_BORDER,
                        highlightthickness=1)
        tabs.pack(fill="x", padx=12, pady=(0, 10))
        for txt, val in [("Relay  (Internet)", "relay"),
                         ("Direct  (LAN / IP)", "direct")]:
            tk.Radiobutton(
                tabs, text=txt, variable=self._mode, value=val,
                bg=C.CLR_BG, fg=C.CLR_TEXT,
                selectcolor=C.CLR_ACCENT,
                activebackground=C.CLR_BG,
                font=("Segoe UI", 9),
                command=self._on_mode_change,
                pady=8, padx=10,
            ).pack(side="left", expand=True)

        # ── relay card ──
        self._relay_card = Card(self._body)
        # pack order: relay card placed right after mode card
        self._relay_card.pack(**PAD)

        tk.Label(self._relay_card, text="YOUR SWIFTDESK ID",
                 bg=C.CLR_SURFACE, fg=C.CLR_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=12, pady=(10, 4))
        self._id_disp = IDDisplay(self._relay_card)
        self._id_disp.pack(fill="x", padx=12, pady=(0, 12))
        self._id_disp.bind_copy(self._copy_id)

        # relay server stored internally, not shown to user
        self._relay_ef_var = tk.StringVar(
            value=f"{C.RELAY_HOST}")

        # ── direct card (hidden by default) ──
        self._direct_card = Card(self._body)
        # Do NOT pack it yet — _on_mode_change manages visibility

        tk.Label(self._direct_card, text="DIRECT / LAN",
                 bg=C.CLR_SURFACE, fg=C.CLR_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=12, pady=(10, 4))
        info_row = tk.Frame(self._direct_card, bg=C.CLR_SURFACE)
        info_row.pack(fill="x", padx=12, pady=(0, 12))
        self._lan_ip_var = tk.StringVar(value=get_lan_ip())
        self._port_ef = EntryField(info_row, label="Port",
                                   initial=str(C.DEFAULT_HOST_PORT),
                                   width=10)
        self._port_ef.pack(side="left", padx=(0, 20))
        ip_box = tk.Frame(info_row, bg=C.CLR_SURFACE)
        ip_box.pack(side="left")
        tk.Label(ip_box, text="Your LAN IP",
                 bg=C.CLR_SURFACE, fg=C.CLR_MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(ip_box, textvariable=self._lan_ip_var,
                 bg=C.CLR_SURFACE, fg=C.CLR_ACCENT,
                 font=("Consolas", 12, "bold")).pack(anchor="w")

        # ── password card ──
        self._pw_card = Card(self._body)
        self._pw_card.pack(**PAD)
        tk.Label(self._pw_card, text="SESSION PASSWORD",
                 bg=C.CLR_SURFACE, fg=C.CLR_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(
                     anchor="w", padx=12, pady=(10, 4))
        self._pw_badge = PasswordBadge(self._pw_card,
                                        initial=self.password,
                                        on_regen=self._regen_pw)
        self._pw_badge.pack(fill="x", padx=12, pady=(0, 12))

        # ── status ──
        sc = Card(self._body)
        sc.pack(**PAD)
        self._status_bar = StatusBar(sc)
        self._status_bar.pack(fill="x")

        # ── buttons ──
        btns = tk.Frame(self._body, bg=C.CLR_BG)
        btns.pack(fill="x", pady=6)
        self._start_btn = SwiftButton(btns, text="Start Hosting",
                                      colour=C.CLR_ACCENT,
                                      width=170, height=44,
                                      command=self.start)
        self._start_btn.pack(side="left", padx=(0, 10))
        self._stop_btn = SwiftButton(btns, text="Stop",
                                     colour=C.CLR_RED,
                                     width=100, height=44,
                                     command=self.stop)
        self._stop_btn.pack(side="left")
        self._stop_btn.configure(state="disabled")

        # ── log ──
        lc = Card(self._body)
        lc.pack(fill="x", pady=(2, 12))
        self._log = ActivityLog(lc, height=6)
        self._log.pack(fill="both", expand=True)

        # set initial mode visibility
        self._on_mode_change()

    # ─── mode switching ───────────────────────────────────────────────────────
    def _on_mode_change(self):
        """Show relay or direct card below the mode selector."""
        if self._mode.get() == "relay":
            self._direct_card.pack_forget()
            # Insert relay card between mode card and pw card
            self._relay_card.pack(fill="x", pady=5,
                                   before=self._pw_card)
        else:
            self._relay_card.pack_forget()
            self._direct_card.pack(fill="x", pady=5,
                                    before=self._pw_card)

    # ─── helpers ─────────────────────────────────────────────────────────────
    def _copy_id(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._my_id)
        self._log_ok("ID copied to clipboard")

    def _regen_pw(self):
        self.password = gen_password()
        self._pw_badge.set(self.password)
        self._log_ok("Password regenerated")

    def _log_ok(self,   m): self.root.after(0, lambda: self._log.append(m, "ok"))
    def _log_err(self,  m): self.root.after(0, lambda: self._log.append(m, "err"))
    def _log_info(self, m): self.root.after(0, lambda: self._log.append(m, "info"))
    def _log_warn(self, m): self.root.after(0, lambda: self._log.append(m, "warn"))

    def _set_status(self, text, state="idle"):
        self.root.after(0, lambda: self._status_bar.update(text, state))

    # ─── start / stop ────────────────────────────────────────────────────────
    def start(self):
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        if self._mode.get() == "relay":
            threading.Thread(target=self._relay_connect, daemon=True).start()
        else:
            self._start_direct()

    def stop(self):
        self._relay_running  = False
        self._direct_running = False
        if self._relay_conn:
            try: self._relay_conn.close()
            except Exception: pass
            self._relay_conn = None
        if self._direct_sock:
            try: self._direct_sock.close()
            except OSError: pass
            self._direct_sock = None
        self.root.after(0, lambda: self._id_disp.set("--- --- ---"))
        self._my_id = ""
        self._set_status("Stopped", "idle")
        self._log_warn("Hosting stopped")
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")

    # ─── RELAY ───────────────────────────────────────────────────────────────
    def _relay_connect(self):
        self._log_info("Connecting to SwiftDesk relay...")
        self._set_status("Connecting...", "waiting")
        self._relay_running = True

        rc = RelayConnection()
        self._relay_conn = rc

        def on_id(host_id):
            self._my_id = host_id
            self.root.after(0, lambda: self._id_disp.set(host_id))
            self._log_ok(f"Online! ID = {host_id}")
            self._set_status("Waiting for viewer...", "waiting")

        def on_viewer_ready():
            self._log_ok("Viewer connected!")
            self._set_status("In session", "connected")
            threading.Thread(target=self._run_relay_session,
                             args=(rc,), daemon=True).start()

        def on_error(msg):
            self._log_err(f"Relay error: {msg}")
            self._set_status("Relay error", "error")
            self._relay_running = False
            self.root.after(0, lambda: (
                self._start_btn.configure(state="normal"),
                self._stop_btn.configure(state="disabled"),
                self._id_disp.set("--- --- ---")))

        rc.connect_as_host(self.password, on_id, on_viewer_ready, on_error)

    def _run_relay_session(self, rc: RelayConnection):
        """Stream screen + receive controls over WebSocket relay."""
        import struct, io
        from PIL import Image

        cap  = ScreenCapture()
        ctrl = InputController()
        w, h = cap.size
        stop = threading.Event()

        # send FRAME_INFO first
        import json as _json
        from protocol import _HEADER, MSG_FRAME_INFO, MSG_SCREEN_FRAME
        from protocol import MSG_MOUSE_EVENT, MSG_KEY_EVENT, MSG_DISCONNECT

        def _make_frame(mtype, payload):
            if isinstance(payload, dict):
                payload = _json.dumps(payload).encode()
            elif payload is None:
                payload = b''
            return _HEADER.pack(mtype, len(payload)) + payload

        rc.send_bytes(_make_frame(MSG_FRAME_INFO,
                                  {"width": w, "height": h}))

        def send_frames():
            interval = 1.0 / C.FPS
            while not stop.is_set() and self._relay_running:
                try:
                    img = cap.grab()
                    if C.SCALE != 1.0:
                        img = img.resize((max(1, int(w * C.SCALE)),
                                          max(1, int(h * C.SCALE))))
                    jpeg = ScreenCapture.to_jpeg(img, C.JPEG_QUALITY)
                    rc.send_bytes(_make_frame(MSG_SCREEN_FRAME, jpeg))
                except Exception:
                    break
                time.sleep(interval)
            try:
                rc.send_bytes(_make_frame(MSG_DISCONNECT, {}))
            except Exception:
                pass

        def recv_controls():
            import struct as _struct
            HDR = _struct.Struct('!BI')
            HSIZ = HDR.size
            buf = bytearray()
            while not stop.is_set():
                try:
                    chunk = rc.recv_bytes()
                except Exception:
                    stop.set(); break
                if chunk == b"__PEER_LEFT__":
                    stop.set(); break
                buf.extend(chunk)
                while len(buf) >= HSIZ:
                    mtype, length = HDR.unpack_from(bytes(buf[:HSIZ]))
                    total = HSIZ + length
                    if len(buf) < total:
                        break
                    payload = bytes(buf[HSIZ:total])
                    del buf[:total]
                    if mtype == MSG_MOUSE_EVENT:
                        ctrl.mouse_event(decode_json(payload))
                    elif mtype == MSG_KEY_EVENT:
                        ctrl.key_event(decode_json(payload))
                    elif mtype == MSG_DISCONNECT:
                        stop.set(); break

        sf = threading.Thread(target=send_frames,   daemon=True)
        rc_t = threading.Thread(target=recv_controls, daemon=True)
        sf.start(); rc_t.start()
        rc_t.join()
        stop.set()
        cap.close()
        self._log_info("Session ended")
        self._set_status("Session ended — waiting for new viewer", "waiting")
        # reconnect to relay for next viewer
        if self._relay_running:
            threading.Thread(target=self._relay_connect, daemon=True).start()

    # ─── DIRECT ──────────────────────────────────────────────────────────────
    def _start_direct(self):
        port = int(self._port_ef.get() or C.DEFAULT_HOST_PORT)
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(1)
            srv.settimeout(0.5)
        except Exception as e:
            self._log_err(f"Bind failed: {e}")
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            return
        self._direct_sock    = srv
        self._direct_running = True
        self._set_status(
            f"Listening on {self._lan_ip_var.get()}:{port}", "waiting")
        self._log_ok(f"Direct mode on port {port}")
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self._direct_running:
            try:
                conn, addr = self._direct_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._log_info(f"Incoming from {addr[0]}")
            threading.Thread(target=self._handle_direct_client,
                             args=(conn, addr), daemon=True).start()

    def _handle_direct_client(self, conn, addr):
        send = make_sender(conn)
        msg  = recv_message(conn)
        if msg is None:
            conn.close(); return
        mtype, payload = msg
        if mtype != MSG_AUTH_REQUEST:
            send(MSG_AUTH_RESPONSE,
                 {"success": False, "message": "Expected auth"})
            conn.close(); return
        creds = decode_json(payload)
        if creds.get("password") != self.password:
            send(MSG_AUTH_RESPONSE,
                 {"success": False, "message": "Wrong password"})
            self._log_err(f"Rejected {addr[0]}: bad password")
            conn.close(); return
        send(MSG_AUTH_RESPONSE, {"success": True, "message": "Welcome"})
        self._log_ok(f"Authorized: {addr[0]}")
        self._set_status(f"In session with {addr[0]}", "connected")

        cap  = ScreenCapture()
        ctrl = InputController()
        w, h = cap.size
        send(MSG_FRAME_INFO, {"width": w, "height": h})
        stop = threading.Event()

        def send_frames():
            interval = 1.0 / C.FPS
            while not stop.is_set() and self._direct_running:
                try:
                    img = cap.grab()
                    if C.SCALE != 1.0:
                        img = img.resize((max(1, int(w * C.SCALE)),
                                          max(1, int(h * C.SCALE))))
                    send(MSG_SCREEN_FRAME,
                         ScreenCapture.to_jpeg(img, C.JPEG_QUALITY))
                except Exception:
                    break
                time.sleep(interval)
            try: send(MSG_DISCONNECT, {})
            except Exception: pass

        def recv_controls():
            while not stop.is_set():
                msg = recv_message(conn)
                if msg is None:
                    stop.set(); break
                mtype, payload = msg
                if mtype == MSG_MOUSE_EVENT:
                    ctrl.mouse_event(decode_json(payload))
                elif mtype == MSG_KEY_EVENT:
                    ctrl.key_event(decode_json(payload))
                elif mtype == MSG_DISCONNECT:
                    stop.set(); break

        sf = threading.Thread(target=send_frames,   daemon=True)
        rc = threading.Thread(target=recv_controls, daemon=True)
        sf.start(); rc.start()
        rc.join()
        stop.set()
        try: conn.close()
        except OSError: pass
        cap.close()
        self._log_info(f"Session ended: {addr[0]}")
        self._set_status(
            "Listening" if self._direct_running else "Idle",
            "waiting" if self._direct_running else "idle")


def main():
    root = tk.Tk()
    HostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
