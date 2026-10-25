"""Configuration for the remote desktop application."""

# --- Network (direct/LAN mode) ---
DEFAULT_HOST_PORT = 5051
CONNECT_TIMEOUT = 10          # seconds, when viewer connects

# --- Relay server ---
# Change RELAY_HOST to your deployed relay server IP/domain.
# Leave as "127.0.0.1" for local testing (run relay_server.py on same machine).
RELAY_HOST = "remote-desktop-app-n0fn.onrender.com"
RELAY_PORT = 5052
RELAY_HEARTBEAT_INTERVAL = 20  # seconds between heartbeat pings

# --- Authentication ---
# If None, a random 6-digit password is generated on host start.
AUTH_PASSWORD = None

# --- Screen capture / streaming ---
FPS = 18                      # frames sent per second by the host
JPEG_QUALITY = 55             # 1-95, lower = smaller/faster
SCALE = 1.0                   # downscale factor for transmission (1.0 = native)

# --- Branding ---
APP_NAME    = "SwiftDesk"
APP_VERSION = "1.0"
VIEWER_TITLE = "SwiftDesk — Remote Viewer"
HOST_TITLE   = "SwiftDesk — Host"

# --- Viewer display ---
VIEWER_BG = "#0f1117"

# --- Theme colours ---
CLR_BG        = "#0f1117"   # deepest background
CLR_SURFACE   = "#1a1d27"   # cards / panels
CLR_BORDER    = "#2a2d3e"   # subtle borders
CLR_ACCENT    = "#4f8ef7"   # primary blue (buttons, ID)
CLR_ACCENT2   = "#7c6af7"   # purple accent
CLR_GREEN     = "#3ddc84"   # connected / online
CLR_RED       = "#f7604f"   # error / stop
CLR_ORANGE    = "#f7a94f"   # warning / waiting
CLR_TEXT      = "#e8eaf0"   # primary text
CLR_MUTED     = "#6b7280"   # secondary / muted text
CLR_WHITE     = "#ffffff"

# --- Throttling ---
MOVE_THROTTLE_MS = 10          # min ms between consecutive mouse-move sends
