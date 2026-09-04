# ==============================================================================
# main.py — AGNI-AI FastAPI REST service (FR-API-01..08, NFR-SCL-*)
# ------------------------------------------------------------------------------
# Product: AGNI-AI — Automated Geospatial Network for Industrial Heat Detection
#          (SIH 2026 · PS 26162 · NTRO). India-subcontinent scope; all fetches
#          are bounded by the INDIA bboxes from config_keys.py.
# ------------------------------------------------------------------------------
# Responsibilities:
#   • Run the ingestion → classification pipeline and cache the result in
#     memory under a thread lock (never half-written reads).
#   • Serve the classified layer + analytics via a small REST API.
#   • Auto-refresh on a TTL, re-refresh on demand (POST /api/v1/refresh).
#   • Host the static map dashboard (index.html + app.js) at the root.
# No secret ever leaves config_keys.py; public endpoints are audited clean.
# ==============================================================================

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import classifier as clf
import demo           # offline synthetic dataset (FR-ING-09); imports ingestion
import ingestion as ing
from config_keys import config

# Structured single-line logging (NFR-SCL-04); never logs config or secrets
logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("firms-api")

_ROOT = Path(__file__).resolve().parent  # project root (this file lives there)

# ------------------------------------------------------------------------------
# In-memory application state (NFR-SCL-02/03): last-good dataset + metadata.
# All mutations happen under STATE_LOCK so readers never see partial frames.
# ------------------------------------------------------------------------------
STATE_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()   # serializes refreshes; non-blocking acquire
_LAST_REFRESH_ATTEMPT = 0.0        # epoch seconds of the last refresh attempt

# Shared state object mutated by the refresh pipeline and read by endpoints
state = {
    "gdf": None,                # classified GeoDataFrame (EPSG:4326) or None
    "industrial_count": 0,      # OSM industrial footprints found
    "updated_at_utc": None,     # ISO timestamp of the last successful refresh
    "window_days": config.persistence_window_days,
    "sources": [],              # FIRMS sources ingested
    "demo_mode": config.demo_mode,
    "status": "initializing",   # initializing | ready | stale
    "last_error": None,         # human-readable reason of the last failure
}


# ==============================================================================
# Pipeline execution
# ==============================================================================
def _run_pipeline() -> None:
    """Fetch + classify + swap the cache in one atomic operation.

    Live mode : FIRMS NRT (multi-source) → OSM Overpass industrial footprints.
    Demo mode : synthetic dataset through the SAME ingestion normalizers.
    Errors    : raise ing.IngestionError; callers keep the last-good dataset.
    """
    if config.uses_real_data:
        # D3: multi-sensor NRT anomalies for the observation window (FR-ING-01)
        anomalies = ing.fetch_firms_nrt(config,
                                        window_days=config.persistence_window_days)
        # D4–D5: industrial footprints around the detected pixels (FR-ING-04)
        industrial = ing.fetch_osm_industrial_polygons(anomalies,
                                                       config.osm_search_radius_m)
    else:
        # FR-ING-09: offline demo through the identical normalizer code paths
        anomalies, industrial = demo.load_demo_dataset()

    # D6–D8: persistence → proximity → class assignment (single entry point)
    classified = clf.classify_pipeline(anomalies, industrial,
                                       window_days=config.persistence_window_days,
                                       radius_m=config.osm_search_radius_m)

    # Atomic cache swap: readers are blocked only during the assignment below
    with STATE_LOCK:
        state["gdf"] = classified.reset_index(drop=True)
        state["industrial_count"] = int(len(industrial))
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        state["sources"] = sorted(classified["source"].unique().tolist()) \
            if not classified.empty else []
        state["window_days"] = config.persistence_window_days
        state["status"] = "ready"
        state["last_error"] = None
    log.info("refresh ok: %d anomalies, %d industrial sites (demo=%s)",
             len(classified), len(industrial), config.demo_mode)


def _refresh_once() -> tuple[bool, str]:
    """Run one refresh guarded by a non-blocking lock; returns (ok, message)."""
    global _LAST_REFRESH_ATTEMPT
    if not _REFRESH_LOCK.acquire(blocking=False):  # another refresh is running
        return False, "refresh already in progress"
    try:
        _LAST_REFRESH_ATTEMPT = time.time()
        _run_pipeline()
        return True, "refresh completed"
    except ing.IngestionError as exc:  # typed provider failure → stale, not dead
        _mark_failed(str(exc))
        return False, str(exc)
    except Exception as exc:  # unexpected bug must never kill the daemon thread
        log.exception("refresh crashed")
        _mark_failed(f"{type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        _REFRESH_LOCK.release()


def _mark_failed(reason: str) -> None:
    """Keep the last-good dataset but flag the state as stale (NFR-SCL-02)."""
    with STATE_LOCK:
        state["status"] = "stale" if state["gdf"] is not None else "initializing"
        state["last_error"] = reason


def _background_loop() -> None:
    """Startup worker: immediate first refresh, then TTL-driven auto-refresh."""
    _refresh_once()
    while True:  # daemon thread; only live mode needs periodic re-fetching
        time.sleep(config.refresh_ttl_minutes * 60)
        if config.uses_real_data:
            _refresh_once()


# FastAPI lifespan: spawn the background worker when the server starts
@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_background_loop, daemon=True).start()
    yield


app = FastAPI(title="AGNI-AI API — India Industrial Heat Detection", version="1.1.0",
              lifespan=lifespan,
              description="NASA FIRMS + OSM industrial-fire classification across India "
                          "(SIH 2026 PS 26162)")

# CORS: the dashboard may be served from any origin (e.g. a separate frontend
# host during SIH deployment); credentials are NOT allowed (no cookies used),
# so a permissive origin list is safe here.
app.add_middleware(CORSMiddleware,
                   allow_origins=["*"],
                   allow_credentials=False,
                   allow_methods=["*"],
                   allow_headers=["*"])


# ==============================================================================
# Serialization helpers
# ==============================================================================
def _clean(value) -> object:
    """Convert one cell into a JSON-safe value (NaN/NaT → None, dates → ISO)."""
    if value is None:
        return None
    try:  # NaN floats are invalid JSON — map to null
        if isinstance(value, float) and math.isnan(value):
            return None
    except TypeError:
        pass
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return value


# Property whitelist — guarantees no secret/internal column ever leaks, and
# keeps each GeoJSON feature lean (NFR-PERF-03/04)
_PROPS = ["class", "class_label", "confidence", "frp_mw", "brightness_temp_k",
          "acq_date_utc", "source", "satellite", "instrument", "daynight",
          "confidence_pct", "proximity_m", "persistence_days", "persistence_score",
          "industry_name", "latitude", "longitude"]


def _frame_to_geojson(gdf: gpd.GeoDataFrame) -> dict:
    """Serialize the classified frame to an RFC 7946 FeatureCollection.

    Column lists are materialized once (vectorized) instead of per-row Series
    access so 10k+ feature responses stay inside the latency budget.
    """
    cols = {p: list(gdf[p]) for p in _PROPS if p in gdf.columns}
    xs = gdf.geometry.x.values
    ys = gdf.geometry.y.values
    features = []
    for i in range(len(gdf)):  # one point feature per detection row
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(float(xs[i]), 6),
                                         round(float(ys[i]), 6)]},
            "properties": {p: _clean(cols[p][i]) for p in cols},
        })
    return {"type": "FeatureCollection", "features": features}


def _require_ready() -> gpd.GeoDataFrame:
    """Return the cached frame, or raise 503 while the layer is initializing."""
    with STATE_LOCK:
        if state["gdf"] is None:  # first refresh has not finished yet
            raise HTTPException(status_code=503, detail={
                "message": "Data layer not ready yet",
                "reason": state["last_error"] or "initial refresh in progress",
            })
        return state["gdf"]


# ==============================================================================
# REST endpoints (SRS §3.1.5)
# ==============================================================================
@app.get("/api/v1/thermal-anomalies")
def get_thermal_anomalies(
    date_from: date | None = Query(None,
                                   description="Detections on/after (YYYY-MM-DD UTC)"),
    date_to: date | None = Query(None,
                                 description="Detections on/before (YYYY-MM-DD UTC)"),
    classification: str | None = Query(None,
                                       description="Class filter: '1', '2', '3' or comma list"),
    min_frp: float | None = Query(None, ge=0.0,
                                  description="Minimum Fire Radiative Power (MW)"),
    max_results: int = Query(2000, ge=1, le=50000,
                             description="Cap on returned features"),
):
    """Return classified anomalies as GeoJSON (FR-API-01)."""
    df = _require_ready().copy()  # filter a copy; never mutate the cache
    if not df.empty:
        # Date-range filter on the UTC acquisition date (AND with other filters)
        if date_from is not None:
            df = df[df["acq_date_utc"].dt.date >= date_from]
        if date_to is not None:
            df = df[df["acq_date_utc"].dt.date <= date_to]
        # Class filter: parse '1' | '1,2,3' and reject anything outside {1,2,3}
        if classification and classification.strip():
            classes = {int(c) for c in classification.split(",") if c.strip()}
            invalid = classes - {1, 2, 3}
            if invalid:
                raise HTTPException(status_code=422,
                                    detail=f"Invalid classification(s): {sorted(invalid)}")
            df = df[df["class"].isin(classes)]
        # FRP floor filter (null FRP rows are excluded when a floor is set)
        if min_frp is not None:
            df = df[df["frp_mw"].notna() & (df["frp_mw"] >= min_frp)]
        # Most recent first, then cap payload size (NFR-PERF-04)
        df = df.sort_values("acq_date_utc", ascending=False).head(max_results)
    return JSONResponse(_frame_to_geojson(df))


@app.get("/api/v1/stats/summary")     # SIH PS 26162 KPI endpoint name
@app.get("/api/v1/analytics/summary")  # alias kept for SRS FR-API-02 contract
def get_analytics_summary():
    """Return cluster/class KPI aggregates over the FULL cached layer (FR-API-02)."""
    df = _require_ready()
    by_class: dict = {}
    for klass in (1, 2, 3):  # always report all three classes (zeros included)
        sub = df[df["class"] == klass]
        has_prox = not sub.empty and sub["proximity_m"].notna().any()
        by_class[str(klass)] = {
            "count": int(len(sub)),
            "mean_frp_mw": round(float(sub["frp_mw"].mean()), 2) if not sub.empty else 0.0,
            "mean_confidence": round(float(sub["confidence"].mean()), 3) if not sub.empty else 0.0,
            "mean_proximity_m": round(float(sub["proximity_m"].mean()), 1) if has_prox else None,
            "mean_persistence": round(float(sub["persistence_score"].mean()), 3) if not sub.empty else 0.0,
        }
    with STATE_LOCK:  # snapshot mutable metadata atomically with the frame
        meta = {k: state[k] for k in ("industrial_count", "window_days",
                                      "demo_mode", "status")}
    if df.empty:
        meta.update({"total_detections": 0, "unique_pixels": 0,
                     "date_min": None, "date_max": None})
    else:
        meta.update({
            "total_detections": int(len(df)),
            "unique_pixels": int(df.groupby(["snapped_lat", "snapped_lon"]).ngroups),
            "date_min": df["acq_date_utc"].min().strftime("%Y-%m-%d"),
            "date_max": df["acq_date_utc"].max().strftime("%Y-%m-%d"),
        })
    return {"by_class": by_class, "sources": list(state["sources"]),
            "updated_at_utc": state["updated_at_utc"], **meta}


@app.get("/api/v1/config/public")
def get_public_config():
    """Secret-free client config (FR-API-03): basemap style/tile URLs, India
    default camera [78.9629, 20.5937] @ zoom 5, demo flag, freshness."""
    with STATE_LOCK:
        return {**config.public_dict(),
                "data_updated_at_utc": state["updated_at_utc"],
                "status": state["status"]}


@app.post("/api/v1/refresh")
def refresh_data():
    """Force an immediate re-ingestion, rate-guarded (FR-API-04)."""
    elapsed = time.time() - _LAST_REFRESH_ATTEMPT
    if elapsed < config.refresh_min_interval_s:  # too soon → 429 with wait time
        raise HTTPException(status_code=429, detail={
            "message": "Refresh rate limit",
            "retry_after_s": round(config.refresh_min_interval_s - elapsed),
        })
    ok, message = _refresh_once()
    if not ok:
        raise HTTPException(status_code=502, detail={"message": message})
    with STATE_LOCK:  # read back totals for the confirmation payload
        total = int(len(state["gdf"] or []))
        refreshed = state["updated_at_utc"]
    return {"status": "ok", "refreshed_at_utc": refreshed,
            "total_detections": total}


@app.get("/healthz")
def healthz():
    """Liveness/probe endpoint (FR-API-05): 503 only when never initialized."""
    with STATE_LOCK:
        healthy = state["gdf"] is not None
        status = state["status"]
    if not healthy:
        raise HTTPException(status_code=503, detail={"status": "initializing"})
    return {"status": "ok", "state": status,
            "data_updated_at_utc": state["updated_at_utc"]}


# ------------------------------------------------------------------------------
# Static frontend hosting (FR-API-06)
# ------------------------------------------------------------------------------
@app.get("/")
def index_page():
    """Serve the map dashboard."""
    return FileResponse(_ROOT / "index.html", media_type="text/html")


@app.get("/app.js")
def app_js():
    """Serve the dashboard application script."""
    return FileResponse(_ROOT / "app.js", media_type="application/javascript")


if __name__ == "__main__":
    # Local dev entry point; binds per configured host/port (default 127.0.0.1)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
