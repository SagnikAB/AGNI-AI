# ==============================================================================
# demo.py — offline synthetic INDIA dataset (FR-ING-09, AGNI-AI)
# ------------------------------------------------------------------------------
# Dev/demo fixture ONLY: when no MAP_KEY is configured (or APP_DEMO_MODE=1)
# the service builds a realistic FIRMS-shaped raw DataFrame plus Overpass-
# shaped industrial elements across the INDIA subcontinent (SIH PS 26162),
# then runs them through the REAL ingestion normalizers so the whole pipeline
# — persistence, proximity, classification — is exercised without any API key
# or network access. Outputs are deterministic (fixed RNG seed).
# ==============================================================================

from __future__ import annotations

import numpy as np
import pandas as pd

import ingestion as ing  # reuse the real normalization code paths
from config_keys import AppConfig, config

# Fixed RNG seed → identical demo output on every run (deterministic tests)
_RNG = np.random.default_rng(42)

# Synthetic industrial footprints (WGS-84 degree boxes) at REAL Indian
# industrial belts (SIH region): Jamnagar, Dahej, Paradip, Visakhapatnam.
# Each entry: (minx, miny, maxx, maxy, display_name) — lon,lat,lon,lat.
_PLANTS = [
    (69.930, 22.290, 69.995, 22.350, "Jamnagar Petrochem Hub (demo)"),
    (72.700, 21.650, 72.830, 21.720, "Dahej PCPIR Zone (demo)"),
    (86.550, 20.180, 86.660, 20.300, "Paradip Refinery Hub (demo)"),
    (83.250, 17.660, 83.320, 17.720, "Visakhapatnam Industrial Belt (demo)"),
]

# Satellite → FIRMS NRT source mapping. Names match the real FIRMS CSV
# `satellite` column vocabulary: NPP / NOAA-20 / NOAA-21 / Aqua / Terra.
_SAT_TO_SOURCE = {
    "NPP": "VIIRS_SNPP_NRT", "NOAA-20": "VIIRS_NOAA20_NRT",
    "NOAA-21": "VIIRS_NOAA21_NRT", "Aqua": "MODIS_NRT", "Terra": "MODIS_NRT",
}


def _sat_sources(sat: str) -> tuple:
    """Return (instrument, source) for a satellite name, matching FIRMS CSV."""
    return ("VIIRS", _SAT_TO_SOURCE[sat]) if sat in ("NPP", "NOAA-20", "NOAA-21") \
        else ("MODIS", _SAT_TO_SOURCE[sat])


def _grid_latlon(lat: float, lon: float, step: float = 0.0034) -> tuple:
    """Quantize a coordinate to the VIIRS detection grid (as FIRMS would report)."""
    return (round(lat / step) * step, round(lon / step) * step)


def build_demo_raw(window_days: int) -> tuple:
    """Generate the synthetic FIRMS-shaped DataFrame + Overpass element list.

    Scene composition (each designed to exercise one classifier rule):
      1. Persistent flare pixels inside plants A/C on EVERY day → Class 1.
      2. One transient in-plant fire pixel on the final day → Class 1 (prox+heat).
      3. A wildfire front marching NE across days → Class 2 (moving, no persistence).
      4. Scattered weak single-day pixels far from plants → Class 3 noise.
    """
    # UTC dates for the last `window_days` days, newest last (NRT semantics)
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    dates = [today - pd.Timedelta(days=window_days - 1 - i) for i in range(window_days)]

    rows: list[dict] = []
    # --- (1) Persistent flare pixels inside plants A and C -------------------
    for plant in (_PLANTS[0], _PLANTS[2]):
        cx = (plant[0] + plant[2]) / 2.0   # plant center lon
        cy = (plant[1] + plant[3]) / 2.0   # plant center lat
        # Three grid pixels spread across the plant so snapping stays inside
        for lon_off in (-0.0034, 0.0, 0.0034):
            plat, plon = _grid_latlon(cy, cx + lon_off)  # returns (lat, lon)
            if not (plant[0] < plon < plant[2] and plant[1] < plat < plant[3]):
                continue  # pixel grid center must sit inside the footprint
            for date in dates:  # detected again EVERY day → persistence = 1.0
                sat = "NPP" if int(date.day) % 2 else "NOAA-20"
                inst, src = _sat_sources(sat)
                rows.append({
                    "latitude": plat + _RNG.uniform(-1e-4, 1e-4),   # sub-pixel jitter
                    "longitude": plon + _RNG.uniform(-1e-4, 1e-4),
                    "bright_ti4": round(_RNG.uniform(345.0, 360.0), 1),
                    "frp": round(_RNG.uniform(9.0, 26.0), 1),
                    "acq_date": date.strftime("%Y-%m-%d"),
                    "acq_time": int(_RNG.choice([213, 232, 305, 418, 445])),
                    "satellite": sat, "instrument": inst, "source": src,
                    "confidence": _RNG.choice(["high", "nominal"]),
                    "daynight": _RNG.choice(["N", "N", "N", "D"]),
                })
    # --- (2) Transient fire inside plant D on the last day only -------------
    p4 = _PLANTS[3]
    plat, plon = _grid_latlon((p4[1] + p4[3]) / 2.0, (p4[0] + p4[2]) / 2.0)
    for sat in ("NPP", "NOAA-20"):
        inst, src = _sat_sources(sat)
        rows.append({
            "latitude": plat, "longitude": plon, "bright_ti4": 370.0,
            "frp": 28.0, "acq_date": dates[-1].strftime("%Y-%m-%d"),
            "acq_time": 1855, "satellite": sat, "instrument": inst, "source": src,
            "confidence": "high", "daynight": "D",
        })
    # --- (3) Wildfire front marching north-east (central India dry forest) ----
    sats = ["NPP", "NOAA-20", "NOAA-21", "Aqua", "Terra"]
    for i, date in enumerate(dates):  # front origin advances ~10 km/day
        fx = 78.300 + i * 0.090        # front longitude anchor (deg, Madhya P.)
        fy = 20.550 + i * 0.040        # front latitude anchor (deg)
        for dx, dy in ((0.0, 0.0), (0.006, 0.0), (0.0, 0.004), (0.006, 0.004)):
            lat, lon = fy + dy + _RNG.uniform(-1e-3, 1e-3), fx + dx + _RNG.uniform(-1e-3, 1e-3)
            sat = sats[(i + int(dx * 1000)) % len(sats)]
            inst, src = _sat_sources(sat)
            rows.append({
                "latitude": lat, "longitude": lon,
                "bright_ti4": round(_RNG.uniform(348.0, 368.0), 1),
                "frp": round(_RNG.uniform(14.0, 55.0), 1),
                "acq_date": date.strftime("%Y-%m-%d"),
                "acq_time": int(_RNG.choice([215, 322, 420, 1600, 1715])),
                "satellite": sat, "instrument": inst, "source": src,
                "confidence": "high", "daynight": "D" if i > 2 else "N",
            })
    # --- (4) Agricultural-noise pixels (Punjab stubble-burn season, weak FRP) -
    for _ in range(12):
        # Sample Punjab/Haryana crop-residue belt — far from the plant sites
        lon = _RNG.uniform(74.90, 76.30)
        lat = _RNG.uniform(29.90, 31.20)
        date = dates[int(_RNG.integers(0, len(dates)))]
        sat = _RNG.choice(["NPP", "Terra", "Aqua"])
        inst, src = _sat_sources(sat)
        rows.append({
            "latitude": lat, "longitude": lon,
            "bright_ti4": round(_RNG.uniform(309.0, 322.0), 1),
            "frp": round(_RNG.uniform(0.8, 4.2), 2),
            "acq_date": date.strftime("%Y-%m-%d"),
            "acq_time": int(_RNG.choice([1510, 1620, 1730, 1905])),
            "satellite": sat, "instrument": inst, "source": src,
            "confidence": str(int(_RNG.integers(15, 45))),  # MODIS-style percent
            "daynight": "D",
        })
    raw = pd.DataFrame(rows)

    # --- Overpass-shaped industrial elements (ways with closed rings) --------
    elements = []
    for osm_id, (minx, miny, maxx, maxy, name) in enumerate(_PLANTS, start=1):
        ring = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
        elements.append({
            "type": "way", "id": osm_id, "tags": {"name": name, "landuse": "industrial"},
            "geometry": [{"lon": x, "lat": y} for x, y in ring],
        })
    return raw, elements


def load_demo_dataset(cfg: AppConfig = config) -> tuple:
    """Build anomalies + industrial frames THROUGH the real ingestion pipeline.

    Returns (anomalies_gdf, industrial_gdf) — identical in shape to what live
    FIRMS/Overpass fetches produce, so downstream code never branches on demo.
    """
    raw, elements = build_demo_raw(cfg.persistence_window_days)
    anomalies = ing.normalize_firms_frame(raw)          # real CSV normalizer
    industrial = ing.overpass_elements_to_gdf(elements)  # real Overpass parser
    return anomalies, industrial
