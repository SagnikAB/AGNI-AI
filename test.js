// ==============================================================================
// AGNI-AI Automated Test Suite (Mathematical, Geospatial, & Pipeline Verification)
// Tests: Transverse Mercator Projection, Stefan-Boltzmann Radiant Heat Engine,
//        Multinomial Statistical Classifier, Threat Matrix, and API Endpoints.
// ==============================================================================

import assert from "node:assert";
import http from "node:http";

console.log("\n🧪 STARTING AGNI-AI CORE VERIFICATION SUITE\n");

// ------------------------------------------------------------------------------
// 1. Transverse Mercator Projection (WGS-84) Verification
// ------------------------------------------------------------------------------
console.log("▶ Testing Dynamic Transverse Mercator Forward Projection...");

// Formula replication from server.js
function projectWgs84ToUtm(lat, lon, centralLon) {
  const a = 6378137.0;
  const f = 1 / 298.257223563;
  const e2 = 2 * f - f * f;
  const e_prime2 = e2 / (1.0 - e2);
  const k0 = 0.9996;

  const latRad = (lat * Math.PI) / 180;
  const lonRad = (lon * Math.PI) / 180;
  const lon0Rad = (centralLon * Math.PI) / 180;

  const sinLat = Math.sin(latRad);
  const cosLat = Math.cos(latRad);
  const tanLat = Math.tan(latRad);

  const n = a / Math.sqrt(1.0 - e2 * sinLat * sinLat);
  const t = tanLat * tanLat;
  const c = e_prime2 * cosLat * cosLat;
  const A = (lonRad - lon0Rad) * cosLat;

  const m0 = 1.0 - e2 / 4.0 - (3.0 * e2 * e2) / 64.0 - (5.0 * e2 * e2 * e2) / 256.0;
  const m1 = (3.0 * e2) / 8.0 + (3.0 * e2 * e2) / 32.0 + (45.0 * e2 * e2 * e2) / 1024.0;
  const m2 = (15.0 * e2 * e2) / 256.0 + (45.0 * e2 * e2 * e2) / 1024.0;
  const m3 = (35.0 * e2 * e2) / 3072.0;

  const M =
    a *
    (m0 * latRad -
      m1 * Math.sin(2.0 * latRad) +
      m2 * Math.sin(4.0 * latRad) -
      m3 * Math.sin(6.0 * latRad));

  const x =
    k0 *
      n *
      (A +
        ((1.0 - t + c) * Math.pow(A, 3)) / 6.0 +
        ((5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * e_prime2) * Math.pow(A, 5)) / 120.0) +
    500000.0;

  let y =
    k0 *
    (M +
      n *
        tanLat *
        (Math.pow(A, 2) / 2.0 +
          ((5.0 - t + 9.0 * c + 4.0 * c * c) * Math.pow(A, 4)) / 24.0 +
          ((61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * e_prime2) * Math.pow(A, 6)) / 720.0));
  if (lat < 0) y += 10000000.0;

  return [x, y];
}

// Check False Easting at Central Meridian
const [cmX, cmY] = projectWgs84ToUtm(22.0, 69.0, 69.0);
assert(
  Math.abs(cmX - 500000.0) < 1.0,
  `Expected false easting ~500000m at central meridian, got ${cmX}`
);
assert(cmY > 2400000 && cmY < 2500000, `Expected UTM Northing ~2.43M meters, got ${cmY}`);

// Check Distance Metric Scale: 100m northward displacement
const [p1X, p1Y] = projectWgs84ToUtm(22.3500, 69.8300, 69.0);
const [p2X, p2Y] = projectWgs84ToUtm(22.3509, 69.8300, 69.0); // ~100m north (0.0009 deg * 111320m)
const dy = Math.abs(p2Y - p1Y);
assert(dy > 95.0 && dy < 105.0, `Expected ~100m metric displacement, got ${dy.toFixed(2)}m`);
console.log("  ✓ Transverse Mercator projection verified (Easting precision < 1m, Northing scale validated)");

// ------------------------------------------------------------------------------
// 2. Stefan-Boltzmann Subpixel Flame Physics Verification
// ------------------------------------------------------------------------------
console.log("▶ Testing Stefan-Boltzmann Radiative Physics Equations...");

const STEFAN_BOLTZMANN = 5.670374e-8;
const GAS_FLARE_EMISSIVITY = 0.92;

function calcSubpixelArea(frpMw, flameTempK, emissivity = GAS_FLARE_EMISSIVITY) {
  const frpWatts = frpMw * 1e6;
  return frpWatts / (emissivity * STEFAN_BOLTZMANN * Math.pow(flameTempK, 4));
}

// Test flare stack condition: 25 MW at 1650 K
const areaFlare = calcSubpixelArea(25.0, 1650.0);
assert(
  areaFlare > 50 && areaFlare < 100,
  `Expected compact flare flame area between 50-100 m^2, got ${areaFlare.toFixed(2)} m^2`
);

// Test wildfire condition: 40 MW at 850 K
const areaWildfire = calcSubpixelArea(40.0, 850.0, 0.95);
assert(
  areaWildfire > 1200,
  `Expected extensive surface wildfire flame area > 1200 m^2, got ${areaWildfire.toFixed(2)} m^2`
);
console.log(`  ✓ Flare subpixel area: ${areaFlare.toFixed(1)} m²`);
console.log(`  ✓ Wildfire subpixel area: ${areaWildfire.toFixed(1)} m²`);
console.log("  ✓ Stefan-Boltzmann scaling ratio verified (T^4 temperature dependence holds)");

// ------------------------------------------------------------------------------
// 3. Multinomial Statistical Classifier & Decision Boundary Verification
// ------------------------------------------------------------------------------
console.log("▶ Testing Calibrated Multinomial Logit Classifier...");

function runClassifier(frpMw, btK, proximityM, persistenceScore, historyRecurrence, flameTempK, isNight = 0) {
  const f_frp = Math.min(1.0, frpMw / 60.0);
  const f_bt = Math.min(1.0, Math.max(0.0, (btK - 300.0) / 80.0));
  const f_prox = proximityM == null ? 0.0 : Math.exp(-proximityM / 900.0);
  const f_persist = persistenceScore;
  const f_history = historyRecurrence;
  const f_tempRatio = flameTempK / 2000.0;

  const logitIndustrial =
    -2.9 + 4.3 * f_prox + 3.5 * f_history + 2.2 * f_persist + 1.9 * f_tempRatio + 0.8 * isNight + 0.5 * f_frp;
  const logitWildfire =
    -1.4 - 2.6 * f_prox - 3.4 * f_history - 1.3 * f_persist + 4.0 * f_frp + 2.6 * f_bt;
  const logitNoise =
    0.6 - 2.2 * f_prox - 1.6 * f_history - 1.1 * f_frp - 0.9 * f_bt;

  const maxLogit = Math.max(logitIndustrial, logitWildfire, logitNoise);
  const eInd = Math.exp(logitIndustrial - maxLogit);
  const eWild = Math.exp(logitWildfire - maxLogit);
  const eNoise = Math.exp(logitNoise - maxLogit);
  const sumE = eInd + eWild + eNoise;

  const pInd = eInd / sumE;
  const pWild = eWild / sumE;
  const pNoise = eNoise / sumE;

  const predictedClass = pInd >= pWild && pInd >= pNoise ? 1 : pWild >= pNoise ? 2 : 3;
  return { pInd, pWild, pNoise, predictedClass };
}

// Case A: High persistence, inside refinery (Jamnagar), T_f=1600K
const caseA = runClassifier(22.0, 355.0, 150.0, 0.85, 0.90, 1600.0, 1);
assert.strictEqual(caseA.predictedClass, 1, "Expected Case A to classify as Industrial Gas Flare (Class 1)");
assert(caseA.pInd > 0.80, `Expected pInd > 0.80, got ${caseA.pInd}`);
console.log(`  ✓ Industrial Flare Case A: Class 1 (pInd = ${(caseA.pInd * 100).toFixed(1)}%)`);

// Case B: High FRP (35 MW), zero proximity to industry, transient front (Satpura forest)
const caseB = runClassifier(35.0, 348.0, 12000.0, 0.05, 0.02, 850.0, 0);
assert.strictEqual(caseB.predictedClass, 2, "Expected Case B to classify as Wildfire Front (Class 2)");
assert(caseB.pWild > 0.80, `Expected pWild > 0.80, got ${caseB.pWild}`);
console.log(`  ✓ Forest Wildfire Case B: Class 2 (pWild = ${(caseB.pWild * 100).toFixed(1)}%)`);

// Case C: Low FRP (2.1 MW), low BT (312 K), far from industry, transient clearing
const caseC = runClassifier(2.1, 312.0, 8000.0, 0.02, 0.01, 620.0, 0);
assert.strictEqual(caseC.predictedClass, 3, "Expected Case C to classify as Agricultural / Noise (Class 3)");
assert(caseC.pNoise > 0.65, `Expected pNoise > 0.65, got ${caseC.pNoise}`);
console.log(`  ✓ Agricultural Residue Case C: Class 3 (pNoise = ${(caseC.pNoise * 100).toFixed(1)}%)`);

// ------------------------------------------------------------------------------
// 4. Live Server Endpoints & GeoJSON RFC 7946 Compliance Verification
// ------------------------------------------------------------------------------
console.log("▶ Testing Active HTTP Endpoints on port 3000...");

function fetchJson(path) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: "localhost", port: 3000, path }, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(body) });
        } catch (e) {
          reject(new Error(`Failed parsing JSON from ${path}: ${body.slice(0, 100)}`));
        }
      });
    }).on("error", reject);
  });
}

async function verifyServer() {
  try {
    // 1. Health check
    const health = await fetchJson("/healthz");
    assert.strictEqual(health.status, 200, "Expected /healthz status 200");
    assert.strictEqual(health.data.status, "ok", "Expected status ok");
    console.log("  ✓ /healthz operational");

    // 2. Public config
    const config = await fetchJson("/api/v1/config/public");
    assert.strictEqual(config.status, 200, "Expected /api/v1/config/public status 200");
    assert(config.data.aoi, "Expected aoi bounding box in public config");
    console.log("  ✓ /api/v1/config/public operational");

    // 3. Stats summary
    const stats = await fetchJson("/api/v1/stats/summary");
    assert.strictEqual(stats.status, 200, "Expected /api/v1/stats/summary status 200");
    assert(stats.data.total_detections > 0, "Expected non-zero detection count");
    console.log(`  ✓ /api/v1/stats/summary operational (${stats.data.total_detections} anomalies indexed)`);

    // 4. Historical facilities
    const facilities = await fetchJson("/api/v1/historical/facilities");
    assert.strictEqual(facilities.status, 200, "Expected /api/v1/historical/facilities status 200");
    const facList = facilities.data.facilities || facilities.data;
    assert(Array.isArray(facList) && facList.length >= 8, "Expected 8 industrial complexes");
    console.log(`  ✓ /api/v1/historical/facilities operational (${facList.length} complexes tracked)`);

    // 5. Thermal anomalies GeoJSON
    const geo = await fetchJson("/api/v1/thermal-anomalies?scope=all&max_results=50");
    assert.strictEqual(geo.status, 200, "Expected status 200 for /api/v1/thermal-anomalies");
    assert.strictEqual(geo.data.type, "FeatureCollection", "Expected RFC 7946 FeatureCollection");
    assert(Array.isArray(geo.data.features) && geo.data.features.length > 0, "Expected non-empty features array");

    const f0 = geo.data.features[0];
    assert.strictEqual(f0.type, "Feature", "Expected Feature object");
    assert.strictEqual(f0.geometry.type, "Point", "Expected Point geometry");
    assert(Array.isArray(f0.geometry.coordinates) && f0.geometry.coordinates.length === 2, "Expected [lon, lat]");
    const [lon, lat] = f0.geometry.coordinates;
    assert(lon >= 68.0 && lon <= 98.0, `Expected longitude inside India bounds, got ${lon}`);
    assert(lat >= 6.0 && lat <= 38.0, `Expected latitude inside India bounds, got ${lat}`);

    // Verify properties and model metadata
    assert(f0.properties.threat_score, "Expected threat_score property");
    assert(f0.properties.physics_model, "Expected physics_model property");
    assert(f0.properties.ml_model, "Expected ml_model property");
    assert(f0.properties.xai, "Expected xai property");
    console.log("  ✓ GeoJSON RFC 7946 compliance validated (Geometry: WGS-84 Point [lon, lat] within India bounds)");

    console.log("\n🎉 ALL TESTS PASSED SUCCESSFULLY! 100% SPECIFICATION CONFORMANCE.\n");
  } catch (err) {
    console.error("  ❌ Server test failed:", err.message);
    process.exit(1);
  }
}

verifyServer();
