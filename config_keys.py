# ==============================================================================
# config_keys.py — Centralized environment & credential configuration (AGNI-AI)
# ------------------------------------------------------------------------------
# Product  : AGNI-AI — Automated Geospatial Network for Industrial Heat
#            Detection (SIH 2026 · PS 26162 · NTRO). India-subcontinent scope.
# SECURITY RULE (NFR-SEC-01): this module is the ONLY place in the codebase
# that touches secrets (NASA FIRMS MAP_KEY, MapTiler token) and third-party
# endpoints. Business logic modules import `config` from here and never read
# os.environ directly, so credentials can never leak into app code.
#
# Every value can be overridden through real environment variables or a local
# `.env` file (copy .env.example → .env). A tiny native .env parser is used so
# no third-party config dependency (e.g. python-dotenv) is required.
# ==============================================================================

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- Path of the project root (this file lives at the root) -------------------
_ROOT = Path(__file__).resolve().parent

# ------------------------------------------------------------------------------
# Minimal .env loader: reads KEY=VALUE lines, strips inline `#` comments,
# removes surrounding quotes, and refuses to overwrite real environment
# variables (real env vars win). Loaded once at import time.
# ------------------------------------------------------------------------------
def _load_dotenv(path: Path) -> None:
    if not path.is_file():                      # .env is optional; env vars may suffice
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:   # skip blanks/comments
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in ("'", '"'):   # strip quotes if present
            value = value[1:-1]
        # Real environment variables take precedence over .env entries
        os.environ.setdefault(key, value)

_load_dotenv(_ROOT / ".env")


def _get_float(key: str, default: float) -> float:
    """Parse a float env var, falling back to `default` on missing/garbage input."""
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _get_int(key: str, default: int) -> int:
    """Parse an int env var, falling back to `default` on missing/garbage input."""
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


# ==============================================================================
# India geographic constraint (CRITICAL — SIH PS 26162)
# ------------------------------------------------------------------------------
# Both strings describe the SAME rectangle in WGS-84 degrees but in the ORDER
# each provider demands:
#   • FIRMS area API   → "WEST,SOUTH,EAST,NORTH" (68, 6, 97, 37)
#   • Overpass bbox    → (SOUTH, WEST, NORTH, EAST)
# All fetching is bounded to this region; never widen it in business logic.
# ==============================================================================
INDIA_FIRMS_BBOX = "68.0,6.0,97.0,37.0"     # FIRMS: west,south,east,north
INDIA_OSM_BBOX = (6.0, 68.0, 37.0, 97.0)     # Overpass QL: (south,west,north,east)

# Default dashboard camera for the India subcontinent (SIH PS 26162):
# [longitude, latitude] + zoom — served to the client via /config/public
DEFAULT_CENTER_LON = 78.9629
DEFAULT_CENTER_LAT = 20.5937
DEFAULT_ZOOM = 5

# Free, keyless dark basemap raster tiles: Esri World Dark Gray Canvas (no
# API key, no watermark). Raster tiles avoid the CORS issues of GL style-JSON
# fetches. (CartoDB dark_all was dropped because Carto now overlays an "API
# KEY REQUIRED" watermark on anonymous requests.)
# NOTE: literal host — {s}/{r} placeholders in the host portion can leak to
# DNS as net::ERR_NAME_NOT_RESOLVED tile errors. Esri scheme is z/y/x.
ESRI_DARK_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}")
# Combined attribution covers every basemap in the dashboard switcher
# (Esri dark/satellite raster + OSM street raster).
BASEMAP_ATTRIBUTION = ('Tiles &copy; Esri &mdash; Esri, Maxar, Earthstar '
                       'Geographics, USGS &middot; &copy; OpenStreetMap contributors')


# ==============================================================================
# Public configuration dataclass — imported by every other module.
# ==============================================================================
@dataclass(frozen=True)
class AppConfig:
    # --- Credentials (secrets — never log, never expose via API) ---------------
    # MAP_KEY: NASA FIRMS API key. Get one free at
    #   https://firms.modaps.eosdis.nasa.gov/api/map_key/  (register e-mail)
    map_key: str = os.environ.get("MAP_KEY", "")

    # MapTiler token (optional). Get one at https://cloud.maptiler.com
    maptiler_key: str = os.environ.get("MAPTILER_KEY", "")

    # --- Third-party endpoints ---------------------------------------------------
    # FIRMS NRT Area API base; the documented URL pattern is:
    #   {firms_base}/api/area/csv/{MAP_KEY}/{SOURCE}/{AREA}/{DAY_RANGE}
    firms_base_url: str = os.environ.get("FIRMS_BASE_URL",
                                         "https://firms.modaps.eosdis.nasa.gov")

    # Public Overpass instance. Mirrors (e.g. https://overpass.kumi.systems/api/)
    # can be substituted if the default is rate-limited.
    overpass_api_url: str = os.environ.get("OVERPASS_API_URL",
                                           "https://overpass-api.de/api/interpreter")

    # FIRMS NRT sources available per official docs (see SRS §4.1)
    firms_sources: tuple = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT",
                            "VIIRS_NOAA21_NRT", "MODIS_NRT")

    # --- Server ---------------------------------------------------------------
    host: str = os.environ.get("APP_HOST", "127.0.0.1")
    port: int = _get_int("APP_PORT", 8000)

    # --- Area of interest: west,south,east,north (WGS-84 deg, FIRMS order) ----
    # Default = the India subcontinent bbox (68,6,97,37) per SIH PS 26162.
    # Override only with a STRICTLY SMALLER sub-region of India, never larger.
    aoi: str = os.environ.get("AOI", INDIA_FIRMS_BBOX)

    # --- Ingestion / observation window -----------------------------------------
    # Days of thermal history fetched from FIRMS (provider max = 5 per call).
    persistence_window_days: int = _get_int("PERSISTENCE_WINDOW_DAYS", 5)

    # Radius (meters) around detected anomalies used to scope the OSM query and
    # to bound the "near industrial site" proximity signal.
    osm_search_radius_m: float = _get_float("OSM_SEARCH_RADIUS_M", 2000.0)

    # --- Classification tuning (SRS §3.1.4 — FR-CLS-06) -------------------------
    class1_evidence_min: float = _get_float("CLASS1_EVIDENCE_MIN", 0.55)
    wildfire_frp_min_mw: float = _get_float("WILDFIRE_FRP_MIN_MW", 6.0)
    wildfire_bt_min_k: float = _get_float("WILDFIRE_BT_MIN_K", 330.0)

    # --- HTTP resilience (FR-ING-07) --------------------------------------------
    http_timeout_s: float = _get_float("HTTP_TIMEOUT_S", 30.0)
    max_retries: int = _get_int("MAX_RETRIES", 3)
    retry_backoff_base_s: float = _get_float("RETRY_BACKOFF_BASE_S", 2.0)

    # --- Cache / refresh lifecycle (NFR-SCL-02/03) -------------------------------
    refresh_ttl_minutes: int = _get_int("REFRESH_TTL_MINUTES", 15)
    refresh_min_interval_s: int = _get_int("REFRESH_MIN_INTERVAL_S", 60)

    # --- Offline demo (FR-ING-09) -------------------------------------------------
    # APP_DEMO_MODE=1 (or no MAP_KEY set) → synthetic dataset so the full
    # pipeline and dashboard run without any API key or network access.
    demo_mode: bool = os.environ.get("APP_DEMO_MODE", "0").lower() in ("1", "true", "yes")

    # --------------------------------------------------------------------------
    # Derived helpers (computed lazily, not stored in the frozen dataclass).
    # --------------------------------------------------------------------------
    @property
    def uses_real_data(self) -> bool:
        """True when a real FIRMS key is present and demo mode is off."""
        return not self.demo_mode and bool(self.map_key.strip())

    @property
    def style_url(self) -> str | None:
        """Vector GL style URL — ONLY when a paid MapTiler key is configured;
        otherwise None so the frontend uses the inline Carto raster tiles.

        NOTE: the MapTiler key is embedded server-side into the URL delivered via
        GET /api/v1/config/public — never placed in client code by hand.
        """
        if self.maptiler_key.strip():
            return ("https://api.maptiler.com/maps/streets-v2/style.json"
                    f"?key={self.maptiler_key}")
        return None

    @property
    def tile_url(self) -> str:
        """Public raster tile URL: MapTiler raster when a key exists, otherwise
        free keyless CartoDB Dark tiles (dark_all — CORS-safe raster default)."""
        if self.maptiler_key.strip():
            return ("https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png"
                    f"?key={self.maptiler_key}")
        return ESRI_DARK_TILES

    @property
    def tile_attribution(self) -> str:
        """Attribution line required by tile providers (shown in the UI)."""
        if self.maptiler_key.strip():
            return '&copy; <a href="https://www.maptiler.com/">MapTiler</a> &copy; OpenStreetMap contributors'
        return BASEMAP_ATTRIBUTION

    def public_dict(self) -> dict:
        """Secret-free client configuration (FR-API-03). NEVER add map_key here."""
        return {
            "style_url": self.style_url,
            "tile_url": self.tile_url,
            "attribution": self.tile_attribution,
            "demo_mode": self.demo_mode,
            "aoi": self.aoi,
            "window_days": self.persistence_window_days,
            "default_center": [DEFAULT_CENTER_LON, DEFAULT_CENTER_LAT],
            "default_zoom": DEFAULT_ZOOM,
        }


# ==============================================================================
# Singleton instance — the only import the rest of the app needs:
#     from config_keys import config
# ==============================================================================
config = AppConfig()
