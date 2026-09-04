# ==============================================================================
# classifier.py — spatiotemporal classification of thermal anomalies
# ------------------------------------------------------------------------------
# Pipeline position (SRS §5, D6–D8). Given the fused FIRMS point frame and the
# OSM industrial polygon frame (both EPSG:4326), this module:
#   D6 attach_persistence : grid-snapped, per-pixel day recurrence (FR-PER-*)
#   D7 attach_proximity   : ST_DWithin-equivalent distance to nearest OSM
#                           industrial boundary, computed in a dynamic UTM
#                           zone — meters are never computed in EPSG:4326
#   D8 assign_classes     : deterministic evidence rules (FR-CLS-01..07)
# Metrics are computed once per UNIQUE SNAPPED PIXEL and joined back onto
# every detection row so repeated overpasses share identical context (DRY).
# All thresholds are named constants, tunable via config (FR-CLS-06).
# ==============================================================================

from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

from config_keys import AppConfig, config

# --- Tunable rule constants (mirrored in .env.example, SRS §3.1.4) ------------
_SIGNAL_REACH_M = 2000.0      # proximity evidence reaches 0 at this distance (m)
_CLASS1_MAX_DIST_M = 1500.0   # Class 1 requires the pixel inside this radius (m)
_UTM_DISTANCE_MAX = 3_000.0   # cap for raw nearest-distance sanity (km guard)

# Class 1 labels (FR-CLS-05); shown verbatim in the UI sidebar
CLASS_LABELS = {
    1: "Gas Flare / Heavy Industrial Heat Source",
    2: "Wildfire / Vegetation Fire",
    3: "Thermal Anomaly / Agricultural Noise",
}

# Sensor native grid step (degrees) used for pixel snapping (SRS §1.3)
_SNAP_STEP_DEG = {"VIIRS": 0.0034, "MODIS": 0.0100}


def _snap_step_for(source: str) -> float:
    """Return the grid-snap step for a FIRMS source identifier."""
    return _SNAP_STEP_DEG.get("VIIRS" if source.upper().startswith("VIIRS") else "MODIS")


def _utm_epsg(lon: float, lat: float) -> int:
    """Pick the UTM zone EPSG code covering the given WGS-84 coordinate.

    Zones run 1–60 eastward from 180°W; northern/southern hemispheres use the
    326xx/327xx families respectively. Kept local to the data centroid so
    metric distances stay accurate (< ~1 m distortion across a zone).
    """
    zone = int((float(lon) + 180.0) // 6) + 1
    return (32600 + zone) if float(lat) >= 0 else (32700 + zone)


# ==============================================================================
# D6 — temporal persistence (FR-PER-01..05)
# ==============================================================================
def attach_persistence(gdf: gpd.GeoDataFrame,
                       window_days: int) -> gpd.GeoDataFrame:
    """Add persistence_score / persistence_days columns to the anomaly frame.

    Coordinates are snapped to the sensor grid so sub-pixel overpass jitter
    does not split one recurring source into many. Persistence = distinct
    observation days on that snapped pixel ÷ days-with-data in this window
    (lower bound: cloud/overpass gaps look like absence — SRS FR-PER-04).
    """
    if gdf.empty:
        return gdf.assign(snapped_lat=[], snapped_lon=[], persistence_days=[],
                          persistence_score=[])

    # Vectorized grid snap: per-source step chosen by sensor family
    steps = gdf["source"].map(_snap_step_for).fillna(0.0034).to_numpy()
    gdf = gdf.copy()
    gdf["snapped_lat"] = np.round(gdf["latitude"].to_numpy() / steps) * steps
    gdf["snapped_lon"] = np.round(gdf["longitude"].to_numpy() / steps) * steps

    # Denominator = distinct UTC days actually present in the fetched window
    days_in_data = gdf["acq_date_utc"].dt.normalize().nunique()
    window = max(1, min(int(window_days), int(days_in_data)))  # clamp defensively

    # Count distinct detection days per snapped pixel (the recurrence signal)
    pixel_stats = (gdf.groupby(["snapped_lat", "snapped_lon"])["acq_date_utc"]
                      .agg(lambda s: s.dt.normalize().nunique())
                      .rename("persistence_days").reset_index())
    pixel_stats["persistence_score"] = (pixel_stats["persistence_days"]
                                        / float(window)).clip(0.0, 1.0)
    # Join the per-pixel stats back onto every detection row (many-to-one)
    return gdf.merge(pixel_stats, on=["snapped_lat", "snapped_lon"],
                     how="left", validate="many_to_one")


# ==============================================================================
# D7 — spatial proximity / ST_DWithin (FR-PRX-01..03)
# ==============================================================================
def attach_proximity(gdf: gpd.GeoDataFrame,
                     industrial: gpd.GeoDataFrame,
                     radius_m: float) -> gpd.GeoDataFrame:
    """Add proximity_m (nearest industrial boundary, meters) + industry_name.

    Runs in the UTM zone of the pixel centroid: both frames are projected,
    then a nearest-neighbour spatial join (the ST_DWithin analog) computes
    distances in meters — 0 when the pixel lies inside a footprint. Distances
    beyond `radius_m` are reported as None ("far") per FR-PRX-02, and the
    matched OSM `name` is attached for UI display.
    """
    # No anomalies or no footprints → every row is "far" with no industry name
    if gdf.empty or industrial.empty:
        return gdf.assign(proximity_m=np.nan, industry_name=None)

    # Unique snapped pixels only; proximity is identical across overpass days
    pixels = (gdf[["snapped_lat", "snapped_lon"]]
              .drop_duplicates().reset_index(drop=True))
    pixels = gpd.GeoDataFrame(
        pixels, geometry=gpd.points_from_xy(pixels["snapped_lon"],
                                            pixels["snapped_lat"]),
        crs=gdf.crs)

    # Project to the UTM zone covering the pixel centroid (metric CRS)
    cy = float(pixels.geometry.y.mean())
    cx = float(pixels.geometry.x.mean())
    epsg = _utm_epsg(cx, cy)
    pixels_utm = pixels.to_crs(epsg=epsg)
    industrial_utm = industrial.to_crs(epsg=epsg).reset_index()

    # Nearest-polygon join with distance (meters, same-CRS requirement)
    joined = pixels_utm.sjoin_nearest(industrial_utm[["geometry", "industry_name",
                                                      "osm_id"]],
                                      how="left", distance_col="distance_m")
    joined = joined[["snapped_lat", "snapped_lon", "distance_m", "industry_name"]]

    # Bounded semantics (FR-PRX-02): beyond the search radius both proximity
    # AND the matched industry name report "far" — never distance w/o name.
    in_range = joined["distance_m"].notna() & (joined["distance_m"] <= radius_m)
    joined["proximity_m"] = joined["distance_m"].where(in_range)
    joined["industry_name"] = joined["industry_name"].where(in_range)
    joined["proximity_m"] = joined["proximity_m"].clip(upper=_UTM_DISTANCE_MAX)
    joined = joined.drop(columns=["distance_m"])
    # Merge the per-pixel proximity onto all detection rows (many-to-one)
    return gdf.merge(joined, on=["snapped_lat", "snapped_lon"],
                     how="left", validate="many_to_one")


# ==============================================================================
# D8 — classification rules (FR-CLS-01..07)
# ==============================================================================
def assign_classes(gdf: gpd.GeoDataFrame,
                   cfg: AppConfig = config) -> gpd.GeoDataFrame:
    """Label every row Class 1/2/3 with a [0,1] confidence score.

    Rule order matters (FR-CLS-04): industrial evidence (E1) is evaluated
    first so plant fires are not mislabeled as wildfire; then vegetation-fire
    heuristics; everything else falls into the low-confidence Class 3 noise
    bucket. Every input column is NaN-guarded (FR-CLS-07).
    """
    if gdf.empty:
        # `class` is a Python keyword — must be injected via ** unpacking
        return gdf.assign(**{"class": pd.Series(dtype="int"),
                             "class_label": pd.Series(dtype="str"),
                             "confidence": pd.Series(dtype="float")})

    # --- Normalized evidence signals, all NaN-safe (nulls contribute 0) ------
    # heat: FRP intensity, saturating at 50 MW
    heat = (pd.to_numeric(gdf["frp_mw"], errors="coerce") / 50.0).clip(0, 1).fillna(0)
    # persist: recurrence at the same snapped pixel across the window
    persist = pd.to_numeric(gdf["persistence_score"], errors="coerce").fillna(0)
    # prox: 1 when inside/near a footprint, decaying to 0 at SIGNAL_REACH_M;
    # None ("far") contributes 0 — it cannot drive Class 1 evidence
    d = pd.to_numeric(gdf["proximity_m"], errors="coerce")
    prox = ((1.0 - d / _SIGNAL_REACH_M).clip(lower=0.0)).fillna(0.0)

    # E1: weighted industrial signature — proximity dominates, recurrence and
    # heat corroborate (the persistent-flare and the plant-fire signatures)
    e1 = 0.50 * prox + 0.30 * persist + 0.20 * heat

    # --- Raw discriminators used by the second rule --------------------------
    frp = pd.to_numeric(gdf["frp_mw"], errors="coerce").fillna(0.0)
    bt = pd.to_numeric(gdf["brightness_temp_k"], errors="coerce").fillna(0.0)

    # --- Rule FR-CLS-01: Class 1 (industrial heat source / flare) ------------
    # Requires BOTH near-an-industrial-boundary (<=1500 m ⇔ prox >= 0.25)
    # AND enough weighted evidence from proximity + persistence + FRP.
    near_industry = d.notna() & (d <= _CLASS1_MAX_DIST_M)
    is_class1 = near_industry & (e1 >= cfg.class1_evidence_min)

    # --- Rule FR-CLS-02: Class 2 (wildfire / vegetation fire) ----------------
    # Strong FRP or very hot pixel away from (or missed by) the industrial
    # evidence rule — the classic moving fire signature. Elementwise `|` is
    # mandatory here: Python `or` would call bool() on a whole Series.
    is_class2 = (frp >= cfg.wildfire_frp_min_mw) | (bt >= cfg.wildfire_bt_min_k)

    # --- Rule FR-CLS-03: default Class 3 (thermal noise / agriculture) -------
    klass = np.where(is_class1, 1, np.where(is_class2, 2, 3))

    # --- Confidence per SRS §3.1.4 --------------------------------------------
    conf1 = e1
    conf2 = (0.45 + 0.35 * heat + 0.20 * (1.0 - persist)).clip(0.0, 0.95)
    conf3 = (0.15 + 0.30 * heat + 0.15 * persist).clip(0.0, 0.60)
    confidence = np.select([is_class1, is_class2], [conf1, conf2],
                           default=conf3)

    out = gdf.copy()
    out["class"] = klass
    out["class_label"] = out["class"].map(CLASS_LABELS)
    out["confidence"] = confidence.round(3)
    return out


# ==============================================================================
# Orchestration (D6 → D7 → D8) — single entry point for the pipeline
# ==============================================================================
def classify_pipeline(anomalies_gdf: gpd.GeoDataFrame,
                      industrial_gdf: gpd.GeoDataFrame,
                      window_days: int,
                      radius_m: float,
                      cfg: AppConfig = config) -> gpd.GeoDataFrame:
    """Run persistence → proximity → class assignment over an anomaly frame.

    Accepts the raw EPSG:4326 outputs of ingestion.py and returns the fully
    classified frame (still EPSG:4326) ready for GeoJSON serialization.
    Empty-in → empty-out with the correct schema (FR-CLS-07).
    """
    if anomalies_gdf.empty:
        return anomalies_gdf.copy()
    result = attach_persistence(anomalies_gdf, window_days)
    result = attach_proximity(result, industrial_gdf, radius_m)
    return assign_classes(result, cfg)
