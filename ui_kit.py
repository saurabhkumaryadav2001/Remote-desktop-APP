"""SwiftDesk UI Kit — reusable themed widgets built on tkinter.

All widgets read colours from config so the entire app theme lives in one place.
"""
import tkinter as tk
import math
import config as C


# ─── helpers ─────────────────────────────────────────────────────────────────

def _hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r, g, b):
    return '#%02x%02x%02x' % (int(r), int(g), int(b))


def lerp_colour(a, b, t):
    """Interpolate between two hex colours. t=0 → a, t=1 → b."""
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return _rgb_to_hex(ar + (br-ar)*t, ag + (bg-ag)*t, ab + (bb-ab)*t)


# ─── SwiftLabel ──────────────────────────────────────────────────────────────

class SwiftLabel(tk.Label):
    def __init__(self, parent, text="", size=10, bold=False, muted=False,
                 colour=None, **kw):
        weight = "bold" if bold else "normal"
        fg = colour or (C.CLR_MUTED if muted else C.CLR_TEXT)
        super().__init__(parent, text=text, bg=C.CLR_BG,
                         fg=fg, font=("Segoe UI", size, weight), **kw)


# ─── SwiftButton ─────────────────────────────────────────────────────────────

class SwiftButton(tk.Button):
    """Themed flat button — drop-in replacement for the old Canvas button.

    Accepts the same keyword arguments as before (colour, text_colour,
    width, height, icon) so all callers work without changes.
    width/height are treated as padx/pady hints, not pixel sizes, because
    tk.Button sizing is font-driven.
    """

    def __init__(self, parent, text="", command=None,
                 colour=None, width=140, height=38,
                 text_colour=C.CLR_WHITE, icon="", **kw):
        # strip Canvas-only kwargs that tk.Button doesn't understand
        kw.pop("highlightthickness", None)

        bg    = colour or C.CLR_ACCENT
        hover = lerp_colour(bg, "#ffffff", 0.12)

        super().__init__(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=text_colour,
            activebackground=hover,
            activeforeground=text_colour,
            relief="flat",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=8,
            cursor="hand2",
            **kw,
        )
        self._base_bg = bg
        self._hover   = hover
        self._enabled = True

        self.bind("<Enter>", lambda _: self.config(bg=self._hover)
                  if self._enabled else None)
        self.bind("<Leave>", lambda _: self.config(bg=self._base_bg)
                  if self._enabled else None)

    def configure(self, **kw):
        state = kw.pop("state", None)
        if state == "disabled":
            self._enabled = False
            kw.setdefault("bg",    C.CLR_BORDER)
            kw.setdefault("fg",    C.CLR_MUTED)
            kw.setdefault("state", "disabled")
        elif state == "normal":
            self._enabled = True
            kw.setdefault("bg",    self._base_bg)
            kw.setdefault("fg",    C.CLR_WHITE)
            kw.setdefault("state", "normal")
        super().configure(**kw)


# ─── Card ────────────────────────────────────────────────────────────────────

class Card(tk.Frame):
    """A surface-coloured rounded card (simulated with tk.Frame + border)."""
    def __init__(self, parent, **kw):
        super().__init__(parent,
                         bg=C.CLR_SURFACE,
                         highlightbackground=C.CLR_BORDER,
                         highlightthickness=1,
                         **kw)


# ─── IDDisplay ───────────────────────────────────────────────────────────────

class IDDisplay(tk.Frame):
    """Large segmented ID display like AnyDesk.  e.g.  752 · 895 · 890"""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C.CLR_SURFACE, **kw)
        self._var = tk.StringVar(value="--- --- ---")

        inner = tk.Frame(self, bg=C.CLR_BG,
                         highlightbackground=C.CLR_BORDER,
                         highlightthickness=1)
        inner.pack(fill="x", padx=0, pady=0)

        self._lbl = tk.Label(inner, textvariable=self._var,
                             bg=C.CLR_BG, fg=C.CLR_ACCENT,
                             font=("Consolas", 28, "bold"),
                             pady=10, padx=20)
        self._lbl.pack(side="left")

        self._copy_btn = tk.Button(inner, text="Copy ID",
                                    bg=C.CLR_BORDER,
                                    fg=C.CLR_TEXT,
                                    activebackground=C.CLR_ACCENT,
                                    activeforeground=C.CLR_WHITE,
                                    relief="flat",
                                    font=("Segoe UI", 9, "bold"),
                                    padx=10, pady=4,
                                    bd=0, cursor="hand2")
        self._copy_btn.pack(side="right", padx=12, pady=10)

    def set(self, raw_id):
        """Accept '752-895-890' or '752895890' and format nicely."""
        clean = raw_id.replace("-", "").replace(" ", "")
        if len(clean) == 9:
            fmt = f"{clean[:3]}-{clean[3:6]}-{clean[6:]}"
        else:
            fmt = raw_id
        self._var.set(fmt)

    def get(self):
        return self._var.get()

    def bind_copy(self, callback):
        self._copy_btn.configure(command=callback)


# ─── StatusDot ───────────────────────────────────────────────────────────────

class StatusDot(tk.Canvas):
    """Animated pulsing dot — green=connected, orange=waiting, red=error."""

    COLOURS = {
        "idle":       C.CLR_MUTED,
        "waiting":    C.CLR_ORANGE,
        "connected":  C.CLR_GREEN,
        "error":      C.CLR_RED,
    }

    def __init__(self, parent, size=12, **kw):
        super().__init__(parent, width=size, height=size,
                         bg=C.CLR_SURFACE, highlightthickness=0, **kw)
        self._size  = size
        self._state = "idle"
        self._pulse = 0.0
        self._dir   = 1
        self._job   = None
        self._draw()

    def _draw(self):
        self.delete("all")
        col = self.COLOURS.get(self._state, C.CLR_MUTED)
        if self._state in ("waiting", "connected"):
            # outer pulse ring
            t = (math.sin(self._pulse) + 1) / 2
            ring = lerp_colour(col, C.CLR_SURFACE, 1 - t * 0.55)
            s = self._size
            self.create_oval(1, 1, s-1, s-1, fill=ring, outline="")
        s = self._size
        m = s // 4
        self.create_oval(m, m, s-m, s-m, fill=col, outline="")

    def _animate(self):
        self._pulse += 0.18
        self._draw()
        if self._state in ("waiting", "connected"):
            self._job = self.after(50, self._animate)

    def set_state(self, state):
        self._state = state
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self._pulse = 0.0
        self._draw()
        if state in ("waiting", "connected"):
            self._animate()


# ─── StatusBar ───────────────────────────────────────────────────────────────

class StatusBar(tk.Frame):
    """Dot + text status indicator."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C.CLR_SURFACE, **kw)
        self._dot = StatusDot(self, size=12)
        self._dot.pack(side="left", padx=(10, 6), pady=8)
        self._var = tk.StringVar(value="Idle")
        tk.Label(self, textvariable=self._var,
                 bg=C.CLR_SURFACE, fg=C.CLR_TEXT,
                 font=("Segoe UI", 9)).pack(side="left", pady=8)

    def update(self, text, state="idle"):
        self._var.set(text)
        self._dot.set_state(state)


# ─── ActivityLog ─────────────────────────────────────────────────────────────

class ActivityLog(tk.Frame):
    """Scrollable dark log area."""

    def __init__(self, parent, height=7, **kw):
        super().__init__(parent, bg=C.CLR_SURFACE, **kw)
        tk.Label(self, text="Activity", bg=C.CLR_SURFACE,
                 fg=C.CLR_MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=10, pady=(6, 0))

        self._txt = tk.Text(self, height=height,
                            bg=C.CLR_BG, fg="#a0aab4",
                            insertbackground=C.CLR_TEXT,
                            relief="flat", font=("Consolas", 8),
                            state="disabled", wrap="word",
                            selectbackground=C.CLR_BORDER)
        sb = tk.Scrollbar(self, command=self._txt.yview,
                          bg=C.CLR_SURFACE, troughcolor=C.CLR_BG)
        self._txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=(0, 6))
        self._txt.pack(fill="both", expand=True,
                       padx=(10, 0), pady=(2, 8))

        # colour tags
        self._txt.configure(state="normal")
        self._txt.tag_configure("ok",   foreground=C.CLR_GREEN)
        self._txt.tag_configure("warn", foreground=C.CLR_ORANGE)
        self._txt.tag_configure("err",  foreground=C.CLR_RED)
        self._txt.tag_configure("info", foreground="#a0aab4")
        self._txt.configure(state="disabled")

    def append(self, msg, level="info"):
        import time as _t
        ts = _t.strftime("%H:%M:%S")
        self._txt.configure(state="normal")
        self._txt.insert("end", f"[{ts}] ", "info")
        self._txt.insert("end", msg + "\n", level)
        self._txt.see("end")
        self._txt.configure(state="disabled")


# ─── Logo ────────────────────────────────────────────────────────────────────

def draw_logo(canvas, x, y, size=28):
    """Draw the SwiftDesk lightning-bolt logo on a canvas."""
    # bolt polygon (scaled)
    s = size / 28
    pts = [
        x+16*s, y+2*s,
        x+8*s,  y+14*s,
        x+13*s, y+14*s,
        x+6*s,  y+26*s,
        x+20*s, y+12*s,
        x+14*s, y+12*s,
    ]
    canvas.create_polygon(pts, fill=C.CLR_ACCENT, outline="", smooth=False)


# ─── TitleBar ────────────────────────────────────────────────────────────────

class TitleBar(tk.Canvas):
    """Branded top bar with logo + app name + subtitle."""

    def __init__(self, parent, subtitle="", height=56, **kw):
        super().__init__(parent, height=height,
                         bg=C.CLR_SURFACE,
                         highlightthickness=0, **kw)
        self.bind("<Configure>", self._redraw)
        self._subtitle = subtitle
        self._height   = height

    def _redraw(self, event=None):
        self.delete("all")
        w = self.winfo_width() or 400
        h = self._height
        # gradient-ish separator line at bottom
        self.create_line(0, h-1, w, h-1, fill=C.CLR_BORDER)
        # logo
        draw_logo(self, 14, (h-28)//2, size=28)
        # app name
        self.create_text(52, h//2 - 5,
                         text=f"SwiftDesk",
                         fill=C.CLR_WHITE,
                         font=("Segoe UI", 14, "bold"),
                         anchor="w")
        # subtitle
        if self._subtitle:
            self.create_text(52, h//2 + 11,
                             text=self._subtitle,
                             fill=C.CLR_MUTED,
                             font=("Segoe UI", 8),
                             anchor="w")


# ─── PasswordBadge ───────────────────────────────────────────────────────────

class PasswordBadge(tk.Frame):
    """Shows password with show/hide toggle + regenerate button."""

    def __init__(self, parent, initial="", on_regen=None, **kw):
        super().__init__(parent, bg=C.CLR_SURFACE, **kw)
        self._real_pw = initial          # must be set BEFORE _mask()
        self._var    = tk.StringVar(value=initial)
        self._shown  = False
        self._on_regen = on_regen

        row = tk.Frame(self, bg=C.CLR_SURFACE)
        row.pack(fill="x")

        tk.Label(row, text="Password", bg=C.CLR_SURFACE,
                 fg=C.CLR_MUTED, font=("Segoe UI", 8)).pack(anchor="w",
                                                              padx=(0, 0))

        inner = tk.Frame(row, bg=C.CLR_BG,
                         highlightbackground=C.CLR_BORDER,
                         highlightthickness=1)
        inner.pack(fill="x")

        self._lbl = tk.Label(inner, textvariable=self._var,
                             bg=C.CLR_BG, fg=C.CLR_ORANGE,
                             font=("Consolas", 18, "bold"),
                             pady=6, padx=14)
        self._lbl.pack(side="left")

        btn_frame = tk.Frame(inner, bg=C.CLR_BG)
        btn_frame.pack(side="right", padx=6)

        self._eye_btn = tk.Button(btn_frame, text="Show",
                                   bg=C.CLR_BORDER, fg=C.CLR_TEXT,
                                   activebackground=C.CLR_ACCENT,
                                   activeforeground=C.CLR_WHITE,
                                   relief="flat", bd=0,
                                   font=("Segoe UI", 9),
                                   padx=8, pady=3,
                                   cursor="hand2",
                                   command=self._toggle)
        self._eye_btn.pack(side="left", padx=2, pady=6)

        if on_regen:
            tk.Button(btn_frame, text="New",
                      bg=C.CLR_BORDER, fg=C.CLR_TEXT,
                      activebackground=C.CLR_ACCENT,
                      activeforeground=C.CLR_WHITE,
                      relief="flat", bd=0,
                      font=("Segoe UI", 9),
                      padx=8, pady=3,
                      cursor="hand2",
                      command=on_regen).pack(side="left", padx=2, pady=6)

        self._mask()

    def _mask(self):
        raw = self._var.get()
        if not self._shown:
            self._lbl.config(fg=C.CLR_MUTED)
            self._display_var = "●" * len(raw)
            self._var.set("●" * len(raw))
        else:
            self._lbl.config(fg=C.CLR_ORANGE)

    def _toggle(self):
        self._shown = not self._shown
        if self._shown:
            self._var.set(self._real_pw)
            self._lbl.config(fg=C.CLR_ORANGE)
            self._eye_btn.config(text="Hide")
        else:
            self._var.set("●" * len(self._real_pw))
            self._lbl.config(fg=C.CLR_MUTED)
            self._eye_btn.config(text="Show")

    def set(self, pw):
        self._real_pw = pw
        if self._shown:
            self._var.set(pw)
        else:
            self._var.set("●" * len(pw))

    def get(self):
        return self._real_pw


# ─── EntryField ──────────────────────────────────────────────────────────────

class EntryField(tk.Frame):
    """Labelled dark entry with optional placeholder."""

    def __init__(self, parent, label="", placeholder="",
                 show=None, width=200, initial="", **kw):
        super().__init__(parent, bg=C.CLR_SURFACE, **kw)
        tk.Label(self, text=label, bg=C.CLR_SURFACE,
                 fg=C.CLR_MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        self._var = tk.StringVar(value=initial)
        frame = tk.Frame(self, bg=C.CLR_BG,
                         highlightbackground=C.CLR_BORDER,
                         highlightthickness=1)
        frame.pack(fill="x")
        self._entry = tk.Entry(frame, textvariable=self._var,
                               show=show or "",
                               bg=C.CLR_BG, fg=C.CLR_TEXT,
                               insertbackground=C.CLR_TEXT,
                               relief="flat",
                               font=("Segoe UI", 10),
                               width=width,
                               bd=6)
        self._entry.pack(fill="x")
        if placeholder and not initial:
            self._ph = placeholder
            self._entry.insert(0, placeholder)
            self._entry.config(fg=C.CLR_MUTED)
            self._entry.bind("<FocusIn>",  self._clear_ph)
            self._entry.bind("<FocusOut>", self._restore_ph)
        else:
            self._ph = ""

    def _clear_ph(self, _):
        if self._entry.get() == self._ph:
            self._entry.delete(0, "end")
            self._entry.config(fg=C.CLR_TEXT)

    def _restore_ph(self, _):
        if not self._entry.get():
            self._entry.insert(0, self._ph)
            self._entry.config(fg=C.CLR_MUTED)

    def get(self):
        v = self._var.get()
        return "" if v == self._ph else v

    def set(self, v):
        self._var.set(v)
