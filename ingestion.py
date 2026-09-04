# ==============================================================================
# ingestion.py — FIRMS NRT ingestion + OSM Overpass industrial-polygon extraction
# ------------------------------------------------------------------------------
# Pipeline position (SRS §5, D2–D5):
#   1) Pull raw thermal-anomaly CSV feeds from NASA FIRMS (area API).
#   2) Normalize any source variant (VIIRS/MODIS) into one canonical frame.
#   3) Build an Overpass QL query scoped to a buffered AOI and extract OSM
#      industrial polygons (industrial=*, landuse=industrial, power=plant,
#      man_made=flare).
# All outputs are GeoDataFrames in EPSG:4326. No secrets live here — the
# configured credentials are imported from config_keys.py (NFR-SEC-01).
# ==============================================================================

from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from typing import Optional

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Polygon
from shapely.validation import make_valid

from config_keys import AppConfig, config

# Module-level constant: OSM API response format we request (SRS §4.2)
_OVERQUERY_TIMEOUT_S = 60


class IngestionError(RuntimeError):
    """Typed ingestion failure — converted to HTTP 502 by the service layer."""


# ==============================================================================
# Generic resilient HTTP helper (FR-ING-07)
# ==============================================================================
def _request_with_retry(url: str, *, method: str = "GET", cfg: AppConfig,
                        **kwargs) -> requests.Response:
    """Perform one HTTP request with timeout + exponential-backoff retries.

    Retries transient network errors and HTTP 429/5xx (honoring Retry-After).
    Raises IngestionError once retries are exhausted so callers can map to
    a clean 502 response instead of crashing the process.
    """
    last_error: Optional[Exception] = None
    for attempt in range(cfg.max_retries + 1):
        try:
            # Single request attempt; kwargs carry params/body as needed
            resp = requests.request(method, url, timeout=cfg.http_timeout_s, **kwargs)
            # Success codes (2xx) return immediately — no retry needed
            if resp.status_code < 300:
                return resp
            # Provider is rate-limiting us: honor Retry-After when supplied
            if resp.status_code == 429 and resp.headers.get("Retry-After"):
                wait = min(float(resp.headers["Retry-After"]), 60.0)
                time.sleep(wait)
                continue
            # Only retry transient server errors (5xx); 4xx are permanent faults
            if resp.status_code < 500:
                raise IngestionError(
                    f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
            last_error = IngestionError(
                f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
        except requests.RequestException as exc:  # connection/timeout errors
            last_error = IngestionError(f"Request to {url} failed: {exc}")
        # Exponential backoff: base * 2^attempt, capped at a polite maximum
        time.sleep(min(cfg.retry_backoff_base_s * (2 ** attempt), 15.0))
    raise IngestionError(f"Giving up after {cfg.max_retries + 1} attempts: {last_error}")


# ==============================================================================
# FIRMS NRT ingestion (FR-ING-01..03, FR-ING-08)
# ==============================================================================
def normalize_firms_frame(df: pd.DataFrame, source: Optional[str] = None) -> gpd.GeoDataFrame:
    """Normalize any FIRMS CSV variant (VIIRS or MODIS) into the canonical frame.

    - Coerces numerics (never trusts the CSV types).
    - Parses acq_date + acq_time (HHMM) into a UTC-aware datetime.
    - Maps nominal confidence (low|nominal|high) to percent 30/60/90.
    - Drops rows with unusable geometry/dates instead of raising.
    Returns a point GeoDataFrame in EPSG:4326.
    """
    required = {"latitude", "longitude", "acq_date"}
    missing = required - set(df.columns)
    if missing:
        raise IngestionError(f"FIRMS feed missing required columns: {sorted(missing)}")

    out = df.copy()
    # Numeric coercion of geographic coordinates; null geometry rows are unusable
    out["latitude"] = pd.to_numeric(out["latitude"], errors="coerce")
    out["longitude"] = pd.to_numeric(out["longitude"], errors="coerce")
    out = out.dropna(subset=["latitude", "longitude"])  # drop bad geometry rows

    # FRP (MW) and brightness temperature (K) may be absent on some sources
    out["frp_mw"] = pd.to_numeric(out.get("frp"), errors="coerce")
    # Prefer the I-4 / MODIS ch21 band, fall back to the secondary band
    bt = out.get("bright_ti4", pd.Series(index=out.index, dtype=float))
    if "bright_ti5" in out.columns:  # secondary band only used when I-4 is missing
        bt = bt.fillna(pd.to_numeric(out["bright_ti5"], errors="coerce"))
    out["brightness_temp_k"] = pd.to_numeric(bt, errors="coerce")

    # Confidence: VIIRS reports nominal strings, MODIS reports percent
    out["confidence_raw"] = out.get("confidence", "nominal").astype(str)
    conf_map = {"low": 30.0, "nominal": 60.0, "high": 90.0}
    out["confidence_pct"] = out["confidence_raw"].str.lower().map(conf_map)
    # Rows that were numeric percentages keep their numeric value
    num_conf = pd.to_numeric(out["confidence_raw"], errors="coerce")
    out["confidence_pct"] = out["confidence_pct"].fillna(num_conf).clip(0, 100)

    # Combine acq_date (YYYY-MM-DD) and acq_time (HHMM int) into UTC datetime
    time_str = pd.to_numeric(out.get("acq_time"), errors="coerce") \
        .fillna(0).astype(int).astype(str).str.zfill(4)
    dt_str = out["acq_date"].astype(str) + " " + time_str.str[:2] + ":" + time_str.str[2:]
    out["acq_date_utc"] = pd.to_datetime(dt_str, format="%Y-%m-%d %H:%M",
                                         errors="coerce", utc=True)
    out = out.dropna(subset=["acq_date_utc"])  # drop undated detections

    # Tag each row with provenance; frontend + analytics group by this column
    out["source"] = source if source else out.get("source", "unknown").astype(str)
    out["satellite"] = out.get("satellite", "unknown").astype(str)
    out["instrument"] = out.get("instrument", "unknown").astype(str)
    out["daynight"] = out.get("daynight", "D").astype(str).str.upper().str[0]

    # Whitelist canonical columns only — raw source duplicates (frp, bright_ti4,
    # confidence, acq_time, ...) are dropped so GeoJSON payloads stay lean.
    keep = ["latitude", "longitude", "acq_date_utc", "frp_mw",
            "brightness_temp_k", "confidence_pct", "daynight",
            "satellite", "instrument", "source"]
    out = out[[c for c in keep if c in out.columns]].reset_index(drop=True)

    # Build the EPSG:4326 point geometry (GeoJSON-compliant interchange CRS)
    return gpd.GeoDataFrame(
        out, geometry=gpd.points_from_xy(out["longitude"], out["latitude"]),
        crs="EPSG:4326")


def fetch_firms_nrt(cfg: AppConfig = config,
                    sources: Optional[tuple] = None,
                    aoi: Optional[str] = None,
                    window_days: Optional[int] = None) -> gpd.GeoDataFrame:
    """Fetch the last `window_days` of FIRMS NRT anomalies for every source.

    URL contract (verified against official docs, SRS §4.1):
      GET {base}/api/area/csv/{MAP_KEY}/{SOURCE}/{AREA}/{DAY_RANGE}
    where AREA = "west,south,east,north" (degrees) and DAY_RANGE <= 5.
    One transaction per satellite source; results are concatenated and
    normalized into a single canonical point frame.
    """
    if not cfg.map_key.strip():
        raise IngestionError("MAP_KEY is empty — set it in .env or run demo mode")
    window = max(1, min(int(window_days or cfg.persistence_window_days), 5))
    area = (aoi or cfg.aoi).strip()
    frames = []
    for src in (sources or cfg.firms_sources):  # loop over satellite sources
        url = (f"{cfg.firms_base_url}/api/area/csv/{cfg.map_key}/{src}/{area}/{window}")
        resp = _request_with_retry(url, cfg=cfg)  # one CSV per source
        frame = pd.read_csv(io.StringIO(resp.text), dtype=str)
        frames.append(normalize_firms_frame(frame, source=src))
        time.sleep(1.2)  # politeness gap between provider transactions
    if not frames:
        raise IngestionError("No FIRMS data was returned for any source")
    return pd.concat(frames, ignore_index=True)  # fused multi-sensor frame


# ==============================================================================
# OSM Overpass extraction (FR-ING-04..06)
# ==============================================================================
def radius_to_deg(radius_m: float, latitude: float) -> tuple:
    """Convert a metric search radius to degrees at a given latitude.

    Latitude offset is constant (1 deg ≈ 111_320 m); the longitude offset must
    be widened by 1/cos(lat) because meridians converge toward the poles.
    """
    lat_deg = radius_m / 111_320.0
    lon_deg = radius_m / (111_320.0 * max(0.2, abs(float(latitude))))
    return lat_deg, lon_deg


def buffered_bbox_deg(anomalies_gdf: gpd.GeoDataFrame,
                      radius_m: float) -> tuple:
    """Return (south, west, north, east) around all anomalies + radius padding.

    Computed from the frame's total bounds in EPSG:4326, expanding with the
    metric radius converted to degrees — used to scope the Overpass query.
    """
    minx, miny, maxx, maxy = anomalies_gdf.total_bounds
    lat_deg, lon_deg = radius_to_deg(radius_m, (miny + maxy) / 2.0)
    return (miny - lat_deg, minx - lon_deg, maxy + lat_deg, maxx + lon_deg)


def build_overpass_query(south: float, west: float,
                         north: float, east: float) -> str:
    """Assemble the Overpass QL query fetching industrial polygon footprints.

    Overpass bbox order is (south, west, north, east) — the opposite of the
    FIRMS area order (west, south, east, north); both are kept distinct on
    purpose (SRS §4.2). `out geom;` returns full per-element geometry.
    """
    bbox = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"
    # Target tag filters — one clause per industrial footprint taxonomy
    tags = ['"industrial"', '"landuse"="industrial"',
            '"power"="plant"', '"man_made"="flare"']
    clauses = []
    for tag in tags:  # ways AND relations can both carry area footprints
        clauses.append(f'way[{tag}]({bbox});')
        clauses.append(f'relation[{tag}]({bbox});')
    return ("[out:json][timeout:60];"
            "(" + "".join(clauses) + ");out geom;")


def overpass_elements_to_gdf(elements: list) -> gpd.GeoDataFrame:
    """Convert Overpass `out geom` JSON elements into an industrial polygon frame.

    Each element carries a `geometry` array of {lat, lon}; rings are closed,
    polygons made valid, and ways/relations deduplicated by OSM id. The OSM
    `name` tag (nullable) is retained for UI display (FR-ING-06).
    """
    polys, names, ids = [], [], []
    seen: set = set()  # dedupe set: an OSM id may appear once per element list
    for el in elements:
        osm_id = el.get("id")
        if osm_id in seen:
            continue  # skip duplicate ways/relations from the union query
        coords = [(g["lon"], g["lat"]) for g in el.get("geometry", [])
                  if "lon" in g and "lat" in g]
        if len(coords) < 3:  # degenerate footprints are not polygons
            continue
        if coords[0] != coords[-1]:  # Overpass omits the closing vertex
            coords = coords + [coords[0]]
        try:
            poly = Polygon(coords)
        except (ValueError, TypeError):
            continue
        # Repair self-intersections/overlaps from raw OSM geometry
        poly = make_valid(poly)
        if poly.is_empty:
            continue
        # Only area footprints are useful for point-in-polygon analysis
        valid_types = ("Polygon", "MultiPolygon")
        if poly.geom_type not in valid_types:
            continue
        polys.append(poly)
        names.append((el.get("tags") or {}).get("name"))
        ids.append(osm_id)
        seen.add(osm_id)
    return gpd.GeoDataFrame(
        {"industry_name": names, "osm_id": ids, "geometry": polys},
        crs="EPSG:4326")


def fetch_osm_industrial_polygons(anomalies_gdf: gpd.GeoDataFrame,
                                  radius_m: float,
                                  cfg: AppConfig = config) -> gpd.GeoDataFrame:
    """Query Overpass for industrial polygons near the given anomaly points.

    The query bbox is the anomaly envelope buffered by `radius_m` (FR-ING-05).
    Returns an empty GeoDataFrame when no anomaly exists or the provider
    returns nothing — an empty result is a legal pipeline state (FR-ING-08).
    """
    if anomalies_gdf.empty:
        return gpd.GeoDataFrame({"industry_name": [], "osm_id": [],
                                 "geometry": []}, crs="EPSG:4326")
    south, west, north, east = buffered_bbox_deg(anomalies_gdf, radius_m)
    # Full QL query text posted to the interpreter endpoint
    query = build_overpass_query(south, west, north, east)
    resp = _request_with_retry(cfg.overpass_api_url, cfg=cfg, method="POST",
                               data={"data": query})
    payload = resp.json()  # JSON output per `[out:json]`
    return overpass_elements_to_gdf(payload.get("elements", []))


# ==============================================================================
# Optional extension hook — reserved per SRS §4.4 (out of scope for v1.0)
# ==============================================================================
def fetch_sentinel_context(*args, **kwargs):
    """Reserved adapter for Copernicus Sentinel-2 context (SRS §4.4).

    Out of scope for v1.0; raising keeps the interface contract explicit so a
    future SWIR-based verification stage can plug in without pipeline changes.
    """
    raise NotImplementedError(
        "Sentinel-2 context fetching is reserved for a future release (SRS §4.4)")


def now_utc() -> str:
    """UTC timestamp helper used by the service layer for cache metadata."""
    return datetime.now(timezone.utc).isoformat()
