/* ImpulseCalc — client-side mean-line + API calls (Devenport-style forms)
 *
 * Pipeline: §1 mean-line → §2 blade metal → §3 case files → §4 CFD → §5–6 results.
 * Each section inherits from the previous; changing upstream marks downstream stale.
 *
 * IMPORTANT: open via the Flask server, not as a file:// page:
 *   python server.py  →  http://127.0.0.1:8765/calc.html
 *
 * Units: UI can display metric (SI) or imperial; API/backend always use SI.
 */

function fmt(x, dig) {
  if (x === null || x === undefined || isNaN(x)) return "";
  dig = dig || 6;
  var ax = Math.abs(x);
  if (ax >= 1e6 || (ax > 0 && ax < 1e-4)) return Number(x).toExponential(5);
  var s = String(Number(x.toPrecision(dig)));
  return s;
}

/** Active unit system for the page. */
window._unitSystem = "metric";

/**
 * CFD fidelity (top bar). Fast = current design-board defaults;
 * High accuracy = fine mesh, tight CFL, long endTime, unlimited solve budget.
 * Mirror of impulsecalc/fidelity.py presets (keep in sync for offline UI).
 */
window._fidelity = {
  mode: "fast",
  level: 0
};

var FIDELITY_PRESETS = {
  fast: {
    mode: "fast", level: 0, label: "Fast (design board)",
    nx: 80, ny: 40, max_co: 0.10,
    end_time_transit_mult: 16, end_time_floor_s: 6e-4, end_time_cap_s: 3e-3,
    mesh_timeout_s: 1200, solve_timeout_s: 5400,
    sample_n_points: 40, blade_n_points: 96,
    hint: "Quick mesh/solve for design iteration. Coarse grid. Solve budget ~90 min wall-clock."
  },
  balanced: {
    mode: "balanced", level: 50, label: "Balanced",
    nx: 160, ny: 90, max_co: 0.05,
    end_time_transit_mult: 32, end_time_floor_s: 1.2e-3, end_time_cap_s: 1e-2,
    mesh_timeout_s: 3600, solve_timeout_s: 14400,
    sample_n_points: 80, blade_n_points: 120,
    hint: "Body-fitted + SST. Solve budget ~4 h. Use for serious cascade checks."
  },
  accurate: {
    mode: "accurate", level: 100, label: "High accuracy (paper-closeness aim)",
    nx: 360, ny: 220, max_co: 0.02,
    end_time_transit_mult: 80, end_time_floor_s: 3e-3, end_time_cap_s: null,
    mesh_timeout_s: null, solve_timeout_s: null,
    sample_n_points: 160, blade_n_points: 180,
    hint: "Fine mesh + long endTime · no wall-clock limit (hours OK). Not full-stage 3D URANS."
  }
};

function _lerpNum(a, b, t) { return a + (b - a) * t; }
function _lerpInt(a, b, t) { return Math.round(a + (b - a) * t); }

/** Resolve slider 0–100 to settings (matches server resolve_fidelity). */
function resolveFidelityClient(mode, level) {
  var lv = Math.max(0, Math.min(100, parseInt(level, 10) || 0));
  var f = FIDELITY_PRESETS.fast;
  var b = FIDELITY_PRESETS.balanced;
  var a = FIDELITY_PRESETS.accurate;
  if (lv <= 0) return Object.assign({}, f);
  if (lv >= 100) return Object.assign({}, a);
  if (lv === 50) return Object.assign({}, b);
  var lo, hi, t, midMode;
  if (lv < 50) {
    lo = f; hi = b; t = lv / 50;
    midMode = lv >= 35 ? "balanced" : "fast";
  } else {
    lo = b; hi = a; t = (lv - 50) / 50;
    midMode = lv >= 75 ? "accurate" : "balanced";
  }
  var meshT, solveT;
  if (hi.mesh_timeout_s == null) {
    meshT = t >= 0.9 ? null : _lerpNum(lo.mesh_timeout_s || 600, 28800, t);
  } else {
    meshT = _lerpNum(lo.mesh_timeout_s, hi.mesh_timeout_s, t);
  }
  if (hi.solve_timeout_s == null) {
    solveT = t >= 0.9 ? null : _lerpNum(lo.solve_timeout_s || 1800, 28800, t);
  } else {
    solveT = _lerpNum(lo.solve_timeout_s, hi.solve_timeout_s, t);
  }
  var cap;
  if (hi.end_time_cap_s == null) {
    cap = t > 0.85 ? null : _lerpNum(lo.end_time_cap_s || 3e-3, 5e-2, t);
  } else {
    cap = _lerpNum(lo.end_time_cap_s || 3e-3, hi.end_time_cap_s, t);
  }
  return {
    mode: midMode,
    level: lv,
    label: (FIDELITY_PRESETS[midMode] || hi).label,
    nx: _lerpInt(lo.nx, hi.nx, t),
    ny: _lerpInt(lo.ny, hi.ny, t),
    max_co: _lerpNum(lo.max_co, hi.max_co, t),
    end_time_transit_mult: _lerpNum(lo.end_time_transit_mult, hi.end_time_transit_mult, t),
    end_time_floor_s: _lerpNum(lo.end_time_floor_s, hi.end_time_floor_s, t),
    end_time_cap_s: cap,
    mesh_timeout_s: meshT,
    solve_timeout_s: solveT,
    sample_n_points: _lerpInt(lo.sample_n_points, hi.sample_n_points, t),
    blade_n_points: _lerpInt(lo.blade_n_points, hi.blade_n_points, t),
    hint: t > 0.5 ? hi.hint : lo.hint
  };
}

function getFidelitySettings() {
  return resolveFidelityClient(window._fidelity.mode, window._fidelity.level);
}

function applyFidelityToCaseForm(settings, opts) {
  opts = opts || {};
  var form = document.forms.casegen;
  if (!form) return;
  // Always push mesh sizing from fidelity so accurate mode is not labels-only
  if (form.nx) form.nx.value = String(settings.nx);
  if (form.ny) form.ny.value = String(settings.ny);
  // end_time: keep auto so server uses fidelity-scaled transit budget
  if (form.end_time && (opts.forceEndAuto || form.end_time.value === "auto" || form.end_time.value === "")) {
    form.end_time.value = "auto";
  }
}

function updateFidelityReadout(settings) {
  settings = settings || getFidelitySettings();
  var ro = document.getElementById("fidelity_readout");
  var hint = document.getElementById("fidelity_hint");
  var bar = document.getElementById("fidelity_bar");
  var toSolve = settings.solve_timeout_s == null ? "∞" : (Math.round(settings.solve_timeout_s / 60) + " min");
  if (ro) {
    ro.textContent =
      "L" + settings.level + " · " + (settings.mode || "?") +
      " · nx=" + settings.nx + "×" + settings.ny +
      " · maxCo=" + Number(settings.max_co).toFixed(3) +
      " · solve budget " + toSolve;
  }
  if (hint) hint.textContent = settings.hint || "";
  if (bar) {
    if (settings.level >= 75) bar.classList.add("fid-accurate");
    else bar.classList.remove("fid-accurate");
  }
}

function onFidelityModeChange(mode) {
  mode = mode || "fast";
  var levelMap = { fast: 0, balanced: 50, accurate: 100 };
  var lv = levelMap[mode] != null ? levelMap[mode] : 0;
  window._fidelity.mode = mode;
  window._fidelity.level = lv;
  var slider = document.getElementById("fidelity_level");
  if (slider) slider.value = String(lv);
  var radios = document.getElementsByName("fidelity_mode");
  for (var i = 0; i < radios.length; i++) {
    radios[i].checked = radios[i].value === mode;
  }
  var s = getFidelitySettings();
  applyFidelityToCaseForm(s, { forceEndAuto: true });
  updateFidelityReadout(s);
  if (typeof onCaseSettingsChange === "function") onCaseSettingsChange();
}

function onFidelitySlider(val) {
  var lv = Math.max(0, Math.min(100, parseInt(val, 10) || 0));
  window._fidelity.level = lv;
  var mode = lv >= 75 ? "accurate" : (lv >= 35 ? "balanced" : "fast");
  window._fidelity.mode = mode;
  var radios = document.getElementsByName("fidelity_mode");
  for (var i = 0; i < radios.length; i++) {
    radios[i].checked = radios[i].value === mode;
  }
  var s = getFidelitySettings();
  applyFidelityToCaseForm(s, { forceEndAuto: true });
  updateFidelityReadout(s);
  if (typeof onCaseSettingsChange === "function") onCaseSettingsChange();
}

function fidelityPayload() {
  var s = getFidelitySettings();
  return {
    fidelity_mode: s.mode,
    fidelity_level: s.level,
    fidelity: { mode: s.mode, level: s.level }
  };
}

/** SI → imperial multipliers (display = SI * k). Temperature is absolute Rankine. */
var UNIT_K = {
  vel:  { k: 3.280839895,   si: "m/s",           imp: "ft/s" },
  press:{ k: 1 / 6894.757293, si: "Pa",          imp: "psi" },
  temp: { k: 1.8,           si: "K",             imp: "°R" },
  len:  { k: 3.280839895,   si: "m",             imp: "ft" },
  len_um:{ k: 1 / 25.4,     si: "μm",            imp: "mil" }, // μm → mil (0.001 in)
  dens: { k: 0.0624279606,  si: "kg/m³",         imp: "lbm/ft³" },
  visc: { k: 0.6719689751,  si: "Pa·s",          imp: "lbm/(ft·s)" },
  Rgas: { k: 0.185861,      si: "J/kg·K",        imp: "ft·lbf/(lbm·°R)" },
  espec:{ k: 0.0004299226,  si: "J/kg",          imp: "Btu/lbm" }, // energy per mass
  force:{ k: 0.224808943,   si: "N",             imp: "lbf" },
  mdot: { k: 2.204622622,   si: "kg/s",          imp: "lbm/s" },
  power:{ k: 0.001341022,   si: "W",             imp: "hp" },
  area: { k: 10.76391042,   si: "m²",            imp: "ft²" }
};

function isImperial() {
  return window._unitSystem === "imperial";
}

/** Convert SI value to current display units. */
function toDisplay(si, kind) {
  if (si === null || si === undefined || isNaN(si)) return si;
  if (!isImperial() || !UNIT_K[kind]) return si;
  return si * UNIT_K[kind].k;
}

/** Convert current display value to SI for API / math. */
function fromDisplay(val, kind) {
  if (val === null || val === undefined || isNaN(val)) return val;
  if (!isImperial() || !UNIT_K[kind]) return val;
  return val / UNIT_K[kind].k;
}

function unitLabel(kind) {
  var u = UNIT_K[kind];
  if (!u) return "";
  return isImperial() ? u.imp : u.si;
}

function refreshUnitLabels() {
  var labs = document.querySelectorAll(".u-lab[data-u]");
  for (var i = 0; i < labs.length; i++) {
    var kind = labs[i].getAttribute("data-u");
    labs[i].textContent = unitLabel(kind);
  }
  var hint = document.getElementById("unit_bar_hint");
  if (hint) {
    hint.textContent = isImperial()
      ? "ft/s · psi · °R · ft · ft·lbf/(lbm·°R)"
      : "m/s · Pa · K · m · J/kg·K";
  }
  var meta = document.getElementById("unit_meta_line");
  if (meta) {
    meta.textContent = isImperial()
      ? "Imperial display (ft/s, psi, °R); solver/API still SI."
      : "SI units (backend always SI; display converts).";
  }
  // metric cards that hardcode kJ/kg
  var eulerLab = document.querySelector("#m_euler");
  if (eulerLab && eulerLab.previousElementSibling) {
    /* keep structure; Euler card title updated below if present */
  }
}

/**
 * Switch metric/imperial. Converts all data-unit inputs between systems
 * and refreshes outputs from last meanline/report.
 */
function setUnitSystem(sys) {
  var next = (sys === "imperial") ? "imperial" : "metric";
  var prev = window._unitSystem || "metric";
  if (next === prev) {
    // sync radio + labels only
    window._unitSystem = next;
    refreshUnitLabels();
    return;
  }

  // Convert every input that carries data-unit, using previous system → SI → new system
  var wasImp = prev === "imperial";
  var inputs = document.querySelectorAll("input[data-unit]");
  for (var i = 0; i < inputs.length; i++) {
    var el = inputs[i];
    var kind = el.getAttribute("data-unit");
    var v = parseFloat(el.value);
    if (isNaN(v) || !UNIT_K[kind]) continue;
    // displayed value → SI
    var si = wasImp ? v / UNIT_K[kind].k : v;
    // SI → new display
    var disp = next === "imperial" ? si * UNIT_K[kind].k : si;
    if (kind === "visc" || Math.abs(disp) < 1e-3 || Math.abs(disp) >= 1e5) {
      el.value = Number(disp).toExponential(4);
    } else if (Math.abs(disp) >= 100) {
      el.value = disp.toFixed(2);
    } else if (Math.abs(disp) >= 1) {
      el.value = disp.toFixed(4);
    } else {
      el.value = disp.toPrecision(5);
    }
  }

  window._unitSystem = next;
  // sync radios (unit_bar is outside meanline form)
  var radios = document.querySelectorAll('input[name="unit_system"]');
  for (var r = 0; r < radios.length; r++) {
    radios[r].checked = radios[r].value === next;
  }
  refreshUnitLabels();

  // Refresh computed outputs in display units
  if (document.forms.meanline && window._lastMeanline) {
    fillMeanline(document.forms.meanline, window._lastMeanline);
  } else if (document.forms.meanline) {
    calcMeanline(document.forms.meanline);
  }
  if (window._lastMetrics) fillMetricsBoard(window._lastMetrics);
  if (window._lastMeanline) {
    // re-paint inherited strips
    if (typeof propagateFromSection1 === "function") propagateFromSection1();
  }
  try { localStorage.setItem("impulsecalc_units", next); } catch (e) { /* ignore */ }
}

function loadSavedUnitSystem() {
  try {
    var s = localStorage.getItem("impulsecalc_units");
    if (s === "imperial") {
      // Page defaults are metric; convert once into imperial display
      window._unitSystem = "metric";
      setUnitSystem("imperial");
      return;
    }
  } catch (e) { /* ignore */ }
  window._unitSystem = "metric";
  refreshUnitLabels();
}

/** API base: same origin as the page (frame is on :8765/calcbody.html). */
function apiUrl(path) {
  if (path.charAt(0) !== "/") path = "/" + path;
  // file:// pages cannot call the API — surface a clear error later
  if (typeof location !== "undefined" && location.protocol === "file:") {
    return path;
  }
  return path;
}

/**
 * fetch → JSON without throwing on HTML error pages.
 * (The classic "Unexpected token '<'" is HTML 404/index returned for /api/*.)
 */
function _isNetworkFetchError(e) {
  var m = String((e && e.message) || e || "");
  return (
    m === "Failed to fetch" ||
    m.indexOf("Failed to fetch") >= 0 ||
    m.indexOf("NetworkError") >= 0 ||
    m.indexOf("Load failed") >= 0 ||
    m.indexOf("Network request failed") >= 0 ||
    m.indexOf("server is down") >= 0
  );
}

function apiJson(path, options) {
  options = options || {};
  // Always force method for mutating/analysis calls when body is present
  if (options.body && !options.method) {
    options.method = "POST";
  }
  options.method = (options.method || "GET").toUpperCase();
  options.headers = options.headers || {};
  if (options.body && !options.headers["Content-Type"]) {
    options.headers["Content-Type"] = "application/json";
  }
  // Avoid caches turning POST analysis into a stale GET
  options.cache = "no-store";
  var maxAttempts = options._retries != null ? options._retries : 4;
  var attempt = options._attempt || 1;

  function once() {
    return fetch(apiUrl(path), options)
      .then(function (r) {
        return r.text().then(function (text) {
          var data = null;
          var trimmed = (text || "").replace(/^\uFEFF/, "").trim();
          if (trimmed.charAt(0) === "<") {
            var hint =
              "Server returned HTML instead of JSON for " + path +
              " (HTTP " + r.status + ", method " + options.method + "). ";
            if (r.status === 405) {
              hint +=
                "HTTP 405 = wrong method or old server process. " +
                "Stop every python server, then:  cd C:\\Users\\tyler\\ImpulseCalc ; python server.py  " +
                "and open http://127.0.0.1:8765/calc.html with Ctrl+F5.";
            } else {
              hint +=
                "Start ImpulseCalc with:  python server.py   then open  http://127.0.0.1:8765/calc.html  " +
                "(do not open the .html file directly).";
            }
            throw new Error(hint);
          }
          if (trimmed) {
            try {
              data = JSON.parse(trimmed);
            } catch (e) {
              throw new Error(
                "Invalid JSON from " + path + " (HTTP " + r.status + "): " +
                trimmed.slice(0, 120)
              );
            }
          } else {
            data = {};
          }
          if (!r.ok) {
            var msg = (data && (data.message || data.error)) || ("HTTP " + r.status);
            var err = new Error(msg);
            err.status = r.status;
            err.data = data;
            throw err;
          }
          return data;
        });
      });
  }

  return once().catch(function (e) {
    if (_isNetworkFetchError(e) && attempt < maxAttempts) {
      // Server may be restarting or briefly busy — retry with backoff
      var delay = 600 * attempt;
      return new Promise(function (resolve) {
        setTimeout(resolve, delay);
      }).then(function () {
        var next = Object.assign({}, options, {
          _attempt: attempt + 1,
          _retries: maxAttempts
        });
        return apiJson(path, next);
      });
    }
    if (_isNetworkFetchError(e)) {
      throw new Error(
        "Failed to fetch " + path + " — server is down or not reachable. " +
        "In PowerShell run:  cd C:\\Users\\tyler\\ImpulseCalc ; .\\start_server.ps1  " +
        "then open http://127.0.0.1:8765/calc.html (Ctrl+F5). " +
        "Leave the server window open while you use Auto-iterate."
      );
    }
    throw e;
  });
}

/** Format seconds as m:ss or h:mm:ss */
function formatDuration(sec) {
  if (sec == null || isNaN(sec) || sec < 0) return "—";
  sec = Math.round(sec);
  var h = Math.floor(sec / 3600);
  var m = Math.floor((sec % 3600) / 60);
  var s = sec % 60;
  function z(n) { return n < 10 ? "0" + n : String(n); }
  if (h > 0) return h + ":" + z(m) + ":" + z(s);
  return m + ":" + z(s);
}

/**
 * Wall-clock estimate (seconds) for mesh/solve from fidelity + kind.
 * Used for ETA until real CFD dumps refine the estimate.
 */
function estimateJobSeconds(kind) {
  var fid = (typeof getFidelitySettings === "function") ? getFidelitySettings() : { level: 0, nx: 80, ny: 40 };
  var cells = (fid.nx || 80) * (fid.ny || 40);
  var lv = fid.level || 0;
  if (kind === "mesh") {
    // ~blockMesh + topoSet/subset; scales with cells
    var meshBase = 25 + cells / 800;
    if (lv >= 75) meshBase *= 4;
    else if (lv >= 35) meshBase *= 2;
    return Math.max(30, Math.min(meshBase, 3600));
  }
  // solve: endTime / effective rate — fidelity multiplies hard
  var solveBase = 120 + cells / 120;
  if (lv >= 90) solveBase = 7200 + cells / 40;      // hours-class accurate
  else if (lv >= 75) solveBase = 2400 + cells / 60;
  else if (lv >= 35) solveBase = 480 + cells / 100;
  return Math.max(60, Math.min(solveBase, 86400));
}

function showLongJobPanel(kind, jobId) {
  var el = document.getElementById("long_job_panel");
  if (!el) return;
  el.className = "lj-active";
  var title = document.getElementById("long_job_title");
  if (title) {
    title.textContent =
      (kind === "mesh" ? "Meshing" : kind === "solve" ? "Flow solve (shockFluid)" : "Long job") +
      (jobId ? " · job " + jobId : "") +
      " — please wait";
  }
  var bar = document.getElementById("long_job_bar");
  if (bar) bar.style.width = "3%";
  var pct = document.getElementById("long_job_pct");
  if (pct) pct.textContent = "…";
  var hb = document.getElementById("long_job_heartbeat");
  if (hb) hb.textContent = "Starting… (elapsed will tick every few seconds — not stuck)";
  var det = document.getElementById("long_job_detail");
  if (det) det.textContent = "";
}

function updateLongJobPanel(j) {
  var el = document.getElementById("long_job_panel");
  if (!el) return;
  if (!el.classList.contains("lj-active") && j && j.status === "running") {
    el.className = "lj-active";
  }
  var elapsed = j.elapsed_s != null ? j.elapsed_s : 0;
  var eta = j.eta_s;
  var frac = j.progress_frac != null ? j.progress_frac : 0;
  var elEl = document.getElementById("long_job_elapsed");
  var etaEl = document.getElementById("long_job_eta");
  var pctEl = document.getElementById("long_job_pct");
  var bar = document.getElementById("long_job_bar");
  var hb = document.getElementById("long_job_heartbeat");
  var det = document.getElementById("long_job_detail");
  if (elEl) elEl.textContent = formatDuration(elapsed);
  if (etaEl) {
    if (j.status === "done") etaEl.textContent = "0:00";
    else if (eta == null || isNaN(eta)) etaEl.textContent = "estimating…";
    else if (eta > 7200) etaEl.textContent = formatDuration(eta) + " (long)";
    else etaEl.textContent = formatDuration(eta);
  }
  var pct = Math.min(99, Math.max(1, Math.round(100 * frac)));
  if (j.status === "done") pct = 100;
  if (pctEl) pctEl.textContent = pct + "%";
  if (bar) bar.style.width = pct + "%";
  if (hb) {
    hb.textContent = j.heartbeat || j.message ||
      ("STILL RUNNING · elapsed " + formatDuration(elapsed) + " · not stuck");
  }
  if (det) {
    var bits = [];
    if (j.kind) bits.push("step=" + j.kind);
    if (j.cfd && j.cfd.sim_time_s != null && j.cfd.end_time_s) {
      bits.push(
        "CFD time " + Number(j.cfd.sim_time_s).toPrecision(4) +
        " / " + Number(j.cfd.end_time_s).toPrecision(4) + " s"
      );
      bits.push((j.cfd.n_time_dirs || 0) + " field dumps written");
    }
    if (j.estimate_s) bits.push("budget ~" + formatDuration(j.estimate_s));
    det.textContent = bits.join(" · ");
  }
}

function finishLongJobPanel(ok, finalMsg) {
  var el = document.getElementById("long_job_panel");
  if (!el) return;
  el.className = "lj-active " + (ok ? "lj-done-ok" : "lj-done-fail");
  var title = document.getElementById("long_job_title");
  if (title) title.textContent = ok ? "Job finished successfully" : "Job finished with errors";
  var hb = document.getElementById("long_job_heartbeat");
  if (hb) hb.textContent = finalMsg || (ok ? "Done." : "Failed.");
  var bar = document.getElementById("long_job_bar");
  if (bar) bar.style.width = "100%";
  var pct = document.getElementById("long_job_pct");
  if (pct) pct.textContent = "100%";
  var etaEl = document.getElementById("long_job_eta");
  if (etaEl) etaEl.textContent = "0:00";
  // Auto-hide success after a short beat; keep failures visible longer
  setTimeout(function () {
    if (el.classList.contains("lj-done-ok")) {
      el.className = "";
    }
  }, ok ? 8000 : 45000);
}

function hideLongJobPanel() {
  var el = document.getElementById("long_job_panel");
  if (el) el.className = "";
}

/**
 * POST that may return {async, job_id} — poll /api/job until done.
 * Prevents browser "Failed to fetch" on multi-minute shockFluid runs.
 * opts.kind: "mesh" | "solve" for ETA estimates and panel title
 */
function apiJob(path, body, onProgress, opts) {
  opts = opts || {};
  var kind = opts.kind || (path.indexOf("mesh") >= 0 ? "mesh" : "solve");
  var estimate = opts.estimate_s != null ? opts.estimate_s : estimateJobSeconds(kind);
  var payload = Object.assign({ async: true, estimate_s: estimate }, body || {});
  return apiJson(path, {
    method: "POST",
    body: JSON.stringify(payload)
  }).then(function (data) {
    if (!data || !data.async || !data.job_id) {
      return data; // sync response
    }
    var jobId = data.job_id;
    var t0 = Date.now();
    showLongJobPanel(kind, jobId);
    data.elapsed_s = 0;
    data.estimate_s = estimate;
    data.eta_s = estimate;
    data.progress_frac = 0.02;
    data.kind = kind;
    data.heartbeat =
      "STILL RUNNING · just started · est. " + formatDuration(estimate) + " · not stuck";
    updateLongJobPanel(data);
    if (onProgress) onProgress(data);
    return new Promise(function (resolve, reject) {
      var tries = 0;
      var maxTries = 200000; // multi-hour accurate mode
      function tick() {
        tries += 1;
        apiJson("/api/job/" + encodeURIComponent(jobId), { method: "GET" })
          .then(function (j) {
            // Local elapsed fallback if server omits it
            if (j.elapsed_s == null) j.elapsed_s = (Date.now() - t0) / 1000;
            if (j.kind == null) j.kind = kind;
            updateLongJobPanel(j);
            if (onProgress) onProgress(j);
            if (j.status === "running" || j.status === "queued") {
              if (tries >= maxTries) {
                finishLongJobPanel(false, "Polling timed out — check server");
                reject(new Error("job timed out polling " + jobId));
                return;
              }
              setTimeout(tick, 2000);
              return;
            }
            if (j.status === "missing") {
              finishLongJobPanel(false, "Job lost on server");
              reject(new Error("job lost: " + jobId));
              return;
            }
            var result = j.result || j;
            var ok = !!result.success;
            finishLongJobPanel(
              ok,
              (ok ? "Done" : "Failed") +
                " after " + formatDuration(j.elapsed_s) +
                (result.message ? " — " + result.message : "")
            );
            resolve(result);
          })
          .catch(function (err) {
            // brief network blip while server busy — keep polling
            if (tries < 8) {
              var soft = {
                status: "running",
                kind: kind,
                elapsed_s: (Date.now() - t0) / 1000,
                estimate_s: estimate,
                progress_frac: Math.min(0.9, ((Date.now() - t0) / 1000) / estimate),
                heartbeat:
                  "STILL RUNNING · reconnecting to server… elapsed " +
                  formatDuration((Date.now() - t0) / 1000) +
                  " · not stuck"
              };
              updateLongJobPanel(soft);
              setTimeout(tick, 3000);
              return;
            }
            finishLongJobPanel(false, String(err.message || err));
            reject(err);
          });
      }
      setTimeout(tick, 1200);
    });
  });
}

function setServerBanner(ok, detail) {
  var el = document.getElementById("server_banner");
  if (!el) return;
  if (ok) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  el.style.display = "block";
  el.textContent = detail ||
    "Cannot reach ImpulseCalc API. Run: python server.py  →  http://127.0.0.1:8765/calc.html";
}

function checkServer() {
  if (typeof location !== "undefined" && location.protocol === "file:") {
    setServerBanner(
      false,
      "Page opened as a local file (file://). APIs will fail. " +
      "Run:  .\\start_server.ps1  then open  http://127.0.0.1:8765/calc.html"
    );
    return Promise.resolve(false);
  }
  return apiJson("/api/health")
    .then(function (data) {
      if (!data || !data.ok) {
        setServerBanner(false, "API health check failed.");
        return false;
      }
      // Stale process detection: loss endpoints added in 1.2
      if (data.version && data.has_design_report === false && data.has_analyze_loss === false) {
        setServerBanner(
          false,
          "Stale ImpulseCalc server (missing design_report routes). " +
          "Run start_server.ps1 from C:\\Users\\tyler\\ImpulseCalc, then Ctrl+F5."
        );
        return false;
      }
      setServerBanner(true);
      return true;
    })
    .catch(function (e) {
      setServerBanner(false, String(e.message || e));
      return false;
    });
}

/** Cross-section pipeline state */
window._pipeline = {
  meanlineOk: false,
  bladeOk: false,
  caseOk: false,
  caseStale: false,
  meshOk: false,
  solveOk: false,
  caseDir: ""
};

function setField(id, value) {
  var el = document.getElementById(id);
  if (!el) return;
  if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") el.value = value;
  else el.textContent = value;
}

function updatePipelineBanner() {
  var p = window._pipeline;
  var el = document.getElementById("pipeline_banner");
  if (!el) return;
  function step(ok, stale, label) {
    if (stale) return label + " ⚠ rebuild";
    if (ok) return label + " ✓";
    return label + " …";
  }
  el.textContent = [
    step(p.meanlineOk, false, "§1 triangles"),
    step(p.bladeOk, false, "§2 blade"),
    step(p.caseOk, p.caseStale, "§3 case"),
    step(p.meshOk && p.solveOk, p.caseStale, "§4 CFD"),
    step(!!p.surfaceOk, false, "§5 surface p"),
    step(!!p.lossOk, false, "§6 losses"),
    "§7 export"
  ].join("  →  ");
}

function markCaseStale(reason) {
  var p = window._pipeline;
  if (!p.caseOk && !p.caseDir) {
    setField("case_stale_msg", "");
    setField("mesh_stale_msg", "");
    updatePipelineBanner();
    return;
  }
  p.caseStale = true;
  p.meshOk = false;
  p.solveOk = false;
  var msg = reason || "Upstream design changed — rebuild case files in §3 before testing.";
  setField("case_stale_msg", msg);
  setField("mesh_stale_msg", "Case may be out of date with §1–2. Rebuild §3, then remesh.");
  var cg = document.forms.casegen;
  if (cg && cg.case_status_field) cg.case_status_field.value = "stale";
  updatePipelineBanner();
}

/** §1 → §2 / §3: publish mean-line outputs as inherited fields */
function propagateFromSection1() {
  var m = window._lastMeanline;
  if (!m) {
    window._pipeline.meanlineOk = false;
    updatePipelineBanner();
    return;
  }
  window._pipeline.meanlineOk = true;
  var pitch = m.pitch != null ? m.pitch : (m.chord / Math.max(m.solidity, 1e-9));
  syncPitchSolidityFields(document.forms.meanline, m.chord, pitch, m.solidity);

  setField("from1_beta1", fmt(m.beta1, 4) + "°");
  setField("from1_beta2", fmt(m.beta2, 4) + "°");
  setField("from1_metal_b1", fmt(m.metal_beta1 != null ? m.metal_beta1 : m.beta1, 4) + "°");
  setField("from1_metal_b2", fmt(m.metal_beta2 != null ? m.metal_beta2 : m.beta2, 4) + "°");
  setField("from1_chord", fmt(toDisplay(m.chord, "len"), 5) + " " + unitLabel("len"));
  setField("from1_solidity", fmt(m.solidity, 4));
  setField("from1_pitch", fmt(toDisplay(pitch, "len"), 5) + " " + unitLabel("len"));
  setField("from1_W1", fmt(toDisplay(m.W1, "vel")) + " " + unitLabel("vel"));
  setField("from1_Mw1", fmt(m.Mw1, 4));
  setField("from1_name", m.blade_name || "");

  setField("from12_name", m.blade_name || "");
  setField(
    "from12_beta",
    "flow β₁=" + fmt(m.beta1, 4) + "° β₂=" + fmt(m.beta2, 4) +
      "° · metal β₁*=" + fmt(m.metal_beta1 != null ? m.metal_beta1 : m.beta1, 4) +
      "° β₂*=" + fmt(m.metal_beta2 != null ? m.metal_beta2 : m.beta2, 4) +
      "° · α₁=" + fmt(m.alpha1, 4) + "° α₂=" + fmt(m.alpha2, 4) + "°"
  );
  setField(
    "from12_flow",
    "Inlet working fluid: |W₁|=" + fmt(toDisplay(m.W1, "vel")) + " " + unitLabel("vel") +
      " @ flow β₁=" + fmt(m.beta1, 2) + "°" +
      " · M_W1=" + fmt(m.Mw1, 4) +
      " · p₁=" + fmt(toDisplay(m.p1, "press")) + " " + unitLabel("press") +
      " · T₁=" + fmt(toDisplay(m.T1, "temp")) + " " + unitLabel("temp") +
      " · y⁺≈" + fmt(toDisplay(m.y * 1e6, "len_um"), 3) + " " + unitLabel("len_um") + " first layer"
  );
  setField(
    "from12_geom",
    "c=" + fmt(toDisplay(m.chord, "len"), 5) + " " + unitLabel("len") +
      " · spacing s=" + fmt(toDisplay(pitch, "len"), 5) + " " + unitLabel("len") +
      " · σ=c/s=" + fmt(m.solidity, 4) +
      " · r_m=" + fmt(toDisplay(m.r_m, "len"), 4) + " " + unitLabel("len") +
      " · span=" + fmt(toDisplay(m.span, "len"), 4) + " " + unitLabel("len")
  );
  updateDomainReadouts();

  updatePipelineBanner();
}

/** Keep pitch s, solidity σ, and s/c slider consistent (σ = c/s). */
function syncPitchSolidityFields(form, chord, pitch, solidity) {
  form = form || document.forms.meanline;
  if (!form) return;
  var c = chord != null ? chord : parseFloat(form.chord && form.chord.value);
  var s = pitch != null ? pitch : parseFloat(form.pitch && form.pitch.value);
  var sig = solidity != null ? solidity : parseFloat(form.solidity && form.solidity.value);
  if (!(c > 0)) c = 0.01;
  if (!(s > 0) && sig > 0) s = c / sig;
  if (!(sig > 0) && s > 0) sig = c / s;
  if (!(s > 0)) s = c / 1.13688;
  if (!(sig > 0)) sig = c / s;
  if (form.pitch && document.activeElement !== form.pitch) {
    form.pitch.value = String(Number(s.toPrecision(6)));
  }
  if (form.solidity && document.activeElement !== form.solidity) {
    form.solidity.value = String(Number(sig.toPrecision(6)));
  }
  var sc = s / c;
  if (form.pitch_sc) form.pitch_sc.value = String(Math.min(2, Math.max(0.4, sc)));
  if (form.pitch_sc_v) form.pitch_sc_v.value = fmt(sc, 3);
}

function onPitchChange(form) {
  form = form || document.forms.meanline;
  if (!form || !form.pitch || !form.chord || !form.solidity) return;
  var c = parseFloat(form.chord.value);
  var s = parseFloat(form.pitch.value);
  if (!(c > 0) || !(s > 0)) return;
  form.solidity.value = String(Number((c / s).toPrecision(6)));
  syncPitchSolidityFields(form, c, s, c / s);
  onMeanlineChange();
}

function onSolidityChange(form) {
  form = form || document.forms.meanline;
  if (!form || !form.pitch || !form.chord || !form.solidity) return;
  var c = parseFloat(form.chord.value);
  var sig = parseFloat(form.solidity.value);
  if (!(c > 0) || !(sig > 0)) return;
  var s = c / sig;
  form.pitch.value = String(Number(s.toPrecision(6)));
  syncPitchSolidityFields(form, c, s, sig);
  onMeanlineChange();
}

function onPitchScSlider(form) {
  form = form || document.forms.meanline;
  if (!form || !form.pitch_sc || !form.chord) return;
  var c = parseFloat(form.chord.value);
  var sc = parseFloat(form.pitch_sc.value);
  if (!(c > 0) || !(sc > 0)) return;
  var s = sc * c;
  form.pitch.value = String(Number(s.toPrecision(6)));
  form.solidity.value = String(Number((1 / sc).toPrecision(6)));
  if (form.pitch_sc_v) form.pitch_sc_v.value = fmt(sc, 3);
  onMeanlineChange();
}

/** §2 → §3: publish blade shape summary; sync cascade blade count when preview is multi-blade */
function propagateFromSection2() {
  var sh = bladeShapeFromForm();
  var m = window._lastMeanline;
  window._pipeline.bladeOk = !!(m && sh);

  setField(
    "from12_shape",
    "h_u/c=" + fmt(sh.upper_sagitta_c != null ? sh.upper_sagitta_c : sh.thickness_ratio, 3) +
      " · h_l/c=" + fmt(sh.lower_sagitta_c != null ? sh.lower_sagitta_c : 0.28, 3) +
      " · t_mid/c≈" + fmt(sh.thickness_ratio, 3) +
      " · LE/TE fillet=" + fmt(sh.le_fillet_r_c, 3) + "/" + fmt(sh.te_fillet_r_c, 3)
  );

  var bf = document.forms.bladeform;
  var cg = document.forms.casegen;
  if (bf && cg && bf.n_prev && cg.n_blades) {
    var np = parseInt(bf.n_prev.value, 10);
    if (np >= 3) {
      for (var i = 0; i < cg.n_blades.options.length; i++) {
        var opt = cg.n_blades.options[i];
        var v = parseInt(opt.value || opt.text, 10);
        if (v === np) {
          cg.n_blades.selectedIndex = i;
          break;
        }
      }
    }
  }
  updatePipelineBanner();
}

/** §3 → §4–6: push case directory downstream */
function propagateCaseDir(caseDir) {
  var dir = caseDir || "";
  window._pipeline.caseDir = dir;
  window._pipeline.caseOk = !!dir;
  if (dir) {
    window._pipeline.caseStale = false;
    setField("case_stale_msg", "");
    setField("mesh_stale_msg", "");
  }
  var forms = ["mesh", "cpform", "exportform", "casegen"];
  for (var i = 0; i < forms.length; i++) {
    var f = document.forms[forms[i]];
    if (f && f.case_dir) f.case_dir.value = dir;
  }
  setField("from3_case_mesh", dir);
  setField("from3_case_cp", dir);
  setField("from3_case_export", dir);
  updatePipelineBanner();
}

function onCaseSettingsChange() {
  updateDomainReadouts();
  // n_blades / domain knobs must re-render preview so purple box stays mesh-identical
  if (typeof previewBlade === "function") previewBlade();
  if (window._pipeline.caseOk) markCaseStale("§3 grid/run settings changed — rebuild case files.");
}

function meanlineFromForm(form) {
  var pure = form.pure.checked;
  var b1 = parseFloat(form.beta1.value);
  var b2 = pure ? (b1 >= 0 ? -Math.abs(b1) : Math.abs(b1)) : parseFloat(form.beta2.value);
  var incidence = form.incidence ? parseFloat(form.incidence.value) || 0 : 0;
  var deviation = form.deviation ? parseFloat(form.deviation.value) || 0 : 0;
  var metal_b1 = b1 - incidence;
  var metal_b2 = b2 + deviation;

  // Inputs may be imperial on screen — convert to SI for all math / API
  var r_m = form.r_m ? fromDisplay(parseFloat(form.r_m.value) || 0, "len") : 0.0375;
  var rpm = form.rpm ? parseFloat(form.rpm.value) || 0 : 0;
  var uFromRpm = !!(form.u_from_rpm && form.u_from_rpm.checked);
  var U = fromDisplay(parseFloat(form.U.value), "vel");
  var omega = 0;
  if (uFromRpm && r_m > 1e-9 && rpm > 1e-9) {
    omega = rpm * 2 * Math.PI / 60;
    U = omega * r_m;
    // keep form U in sync (display units)
    form.U.value = fmt(toDisplay(U, "vel"), 5);
  } else if (r_m > 1e-9 && Math.abs(U) > 1e-9) {
    omega = U / r_m;
    if (rpm <= 1e-9) rpm = omega * 60 / (2 * Math.PI);
  } else if (rpm > 1e-9) {
    omega = rpm * 2 * Math.PI / 60;
  }

  var W1 = Math.max(fromDisplay(parseFloat(form.w1.value), "vel"), 1);
  var p1 = Math.max(fromDisplay(parseFloat(form.p1.value), "press"), 1);
  var T1 = Math.max(fromDisplay(parseFloat(form.T1.value), "temp"), 1);
  var g = parseFloat(form.gamma.value);
  var R = fromDisplay(parseFloat(form.R.value), "Rgas");
  var mu = fromDisplay(parseFloat(form.mu.value), "visc");
  var chord = fromDisplay(parseFloat(form.chord.value), "len");
  var span = form.span ? fromDisplay(parseFloat(form.span.value) || 0, "len") : 0.012;
  var mdotIn = form.mdot ? fromDisplay(parseFloat(form.mdot.value) || 0, "mdot") : 0;
  var powerIn = form.power ? fromDisplay(parseFloat(form.power.value) || 0, "power") : 0;
  var yplus = parseFloat(form.yplus.value);

  if (g <= 1.0) {
    alert("gamma must be greater than 1");
    return null;
  }

  function rad(d) { return d * Math.PI / 180; }
  function deg(r) { return r * 180 / Math.PI; }

  var Wa1 = W1 * Math.cos(rad(b1));
  var Wt1 = W1 * Math.sin(rad(b1));
  var Ca1 = Wa1, Ct1 = Wt1 + U;
  var C1 = Math.hypot(Ca1, Ct1);
  var alpha1 = deg(Math.atan2(Ct1, Ca1));

  var W2 = W1;
  var Wa2 = W2 * Math.cos(rad(b2));
  var Wt2 = W2 * Math.sin(rad(b2));
  var Ca2 = Wa2, Ct2 = Wt2 + U;
  var C2 = Math.hypot(Ca2, Ct2);
  var alpha2 = deg(Math.atan2(Ct2, Ca2));

  var a = Math.sqrt(g * R * T1);
  var rho1 = p1 / (R * T1);
  var Mw1 = W1 / a, Mc1 = C1 / a, Mw2 = W2 / a;
  var euler = U * (Ct1 - Ct2);
  var r = pure ? 0.0 : ((Math.abs(euler) > 1e-6) ? (W2 * W2 - W1 * W1) / (2 * euler) : 0);
  var phi = Ca1 / Math.max(U, 1e-9);
  var psi = euler / Math.max(U * U, 1e-9);
  var eta = Math.max(0, Math.min(1, 1 - 0.35 * Math.pow(C2 / Math.max(C1, 1), 2)));

  var Re = rho1 * W1 * Math.max(chord, 1e-6) / Math.max(mu, 1e-12);
  var cf = 0.027 / Math.pow(Math.max(Re, 1), 0.1429);
  var tau = 0.5 * rho1 * W1 * W1 * cf;
  var ut = Math.sqrt(Math.max(tau / Math.max(rho1, 1e-12), 0));
  var y = yplus * mu / Math.max(rho1 * ut, 1e-12);

  var annulus = (r_m > 0 && span > 0) ? (2 * Math.PI * r_m * span) : 0;
  var mdotArea = annulus > 0 ? rho1 * Math.abs(Ca1) * annulus : 0;
  var mdot = mdotIn > 1e-12 ? mdotIn
    : (powerIn > 1e-12 && Math.abs(euler) > 1e-6 ? powerIn / Math.abs(euler) : mdotArea);
  var power = powerIn > 1e-12 ? powerIn : mdot * Math.abs(euler);
  var tip_r = r_m > 0 ? r_m + 0.5 * span : 0;
  var tip_u = (omega > 0 && tip_r > 0) ? omega * tip_r
    : (r_m > 1e-9 && tip_r > 0 ? U * tip_r / r_m : U);
  var tip_m = tip_u / Math.max(a, 1e-9);

  return {
    beta1: b1, beta2: b2, alpha1: alpha1, alpha2: alpha2,
    metal_beta1: metal_b1, metal_beta2: metal_b2,
    incidence: incidence, deviation: deviation,
    U: U, W1: W1, W2: W2, C1: C1, C2: C2,
    Ct1: Ct1, Ct2: Ct2, Ca1: Ca1, Ca2: Ca2,
    a: a, rho1: rho1, Mw1: Mw1, Mc1: Mc1, Mw2: Mw2,
    euler: euler, r: r, phi: phi, psi: psi, eta: eta, y: y,
    p1: p1, T1: T1, gamma: g, R: R, mu: mu, chord: chord,
    solidity: parseFloat(form.solidity.value),
    pitch: (function () {
      var c0 = parseFloat(form.chord.value);
      var sig0 = parseFloat(form.solidity.value);
      var p0 = form.pitch ? parseFloat(form.pitch.value) : NaN;
      if (p0 > 0) return p0;
      if (c0 > 0 && sig0 > 0) return c0 / sig0;
      return null;
    })(),
    blade_name: form.blade_name.value || "user_stage_r040",
    pure: pure,
    r_m: r_m, rpm: rpm, span: span, u_from_rpm: uFromRpm,
    mdot: mdot, power: power, annulus: annulus,
    tip_u: tip_u, tip_m: tip_m, omega: omega
  };
}

function fillMeanline(form, m) {
  if (!m) return;
  form.out_beta1.value = fmt(m.beta1, 5);
  form.out_beta2.value = fmt(m.beta2, 5);
  form.out_alpha1.value = fmt(m.alpha1, 5);
  form.out_alpha2.value = fmt(m.alpha2, 5);
  form.out_W1.value = fmt(toDisplay(m.W1, "vel"));
  form.out_W2.value = fmt(toDisplay(m.W2, "vel"));
  form.out_C1.value = fmt(toDisplay(m.C1, "vel"));
  form.out_C2.value = fmt(toDisplay(m.C2, "vel"));
  form.out_U.value = fmt(toDisplay(m.U, "vel"));
  form.out_Ct1.value = fmt(toDisplay(m.Ct1, "vel"));
  form.out_Ct2.value = fmt(toDisplay(m.Ct2, "vel"));
  form.out_Ca1.value = fmt(toDisplay(m.Ca1, "vel"));
  form.out_Mw1.value = fmt(m.Mw1, 5);
  form.out_Mc1.value = fmt(m.Mc1, 5);
  form.out_Mw2.value = fmt(m.Mw2, 5);
  form.out_a.value = fmt(toDisplay(m.a, "vel"));
  form.out_r.value = fmt(m.r, 5);
  form.out_euler.value = fmt(toDisplay(m.euler, "espec"));
  form.out_phi.value = fmt(m.phi, 5);
  form.out_psi.value = fmt(m.psi, 5);
  form.out_eta.value = fmt(m.eta, 4);
  form.out_rho1.value = fmt(toDisplay(m.rho1, "dens"), 5);
  form.out_y_m.value = fmt(toDisplay(m.y, "len"), 4);
  // y in μm (SI) or mil (imperial)
  form.out_y_um.value = fmt(toDisplay(m.y * 1e6, "len_um"), 4);
  if (form.out_metal_b1) form.out_metal_b1.value = fmt(m.metal_beta1, 5);
  if (form.out_metal_b2) form.out_metal_b2.value = fmt(m.metal_beta2, 5);
  if (form.out_mdot) form.out_mdot.value = fmt(toDisplay(m.mdot, "mdot"), 5);
  if (form.out_power) form.out_power.value = fmt(toDisplay(m.power, "power"), 5);
  if (form.out_tip_m) form.out_tip_m.value = fmt(m.tip_m, 4);
  if (form.out_tip_u) form.out_tip_u.value = fmt(toDisplay(m.tip_u, "vel"));
  if (form.out_annulus) form.out_annulus.value = fmt(toDisplay(m.annulus, "area"), 5);
  if (form.out_rpm) form.out_rpm.value = fmt(m.rpm, 1);
  drawTriangles(m);
  window._lastMeanline = m;
}

function calcMeanline(form) {
  var m = meanlineFromForm(form);
  fillMeanline(form, m);
  if (!m) {
    window._pipeline.meanlineOk = false;
    updatePipelineBanner();
    return null;
  }
  propagateFromSection1();
  return m;
}

function toggleBeta2(form) {
  form.beta2.disabled = form.pure.checked;
  if (form.pure.checked) {
    var b1 = parseFloat(form.beta1.value);
    form.beta2.value = (b1 >= 0 ? -Math.abs(b1) : Math.abs(b1));
  }
}

/* SVG velocity triangles (inlet + outlet) */
function drawTriangles(m) {
  var el = document.getElementById("tri_svg");
  if (!el || !m) return;

  var Wa1 = m.Ca1;
  var Wt1 = m.Ct1 - m.U;
  var Wa2 = m.Ca2;
  var Wt2 = m.Ct2 - m.U;

  // Fit both triangles into a shared scale (velocities can be ~1e3 m/s)
  var maxComp = 1;
  [m.Ca1, m.Ct1, Wa1, Wt1, m.Ca2, m.Ct2, Wa2, Wt2, m.U].forEach(function (v) {
    maxComp = Math.max(maxComp, Math.abs(v));
  });

  var panelW = 380;
  var panelH = 300;
  var pad = 48;
  var scale = Math.min((panelW - 2 * pad) / maxComp, (panelH - 2 * pad) / (2 * maxComp));
  scale = Math.max(scale, 0.04);

  function arrow(ox, oy, x0, y0, x1, y1, color, label) {
    // Plot: axial → +x screen, swirl θ → +y up
    var a0 = ox + x0 * scale;
    var a1 = oy - y0 * scale;
    var b0 = ox + x1 * scale;
    var b1 = oy - y1 * scale;
    var dx = b0 - a0;
    var dy = b1 - a1;
    var L = Math.hypot(dx, dy) || 1;
    var ux = dx / L;
    var uy = dy / L;
    var ah = 11;
    var hx = b0 - ah * ux;
    var hy = b1 - ah * uy;
    var px = -uy;
    var py = ux;
    var mx = (a0 + b0) / 2 + 8 * px;
    var my = (a1 + b1) / 2 + 8 * py;
    return (
      '<line x1="' + a0.toFixed(1) + '" y1="' + a1.toFixed(1) +
      '" x2="' + b0.toFixed(1) + '" y2="' + b1.toFixed(1) +
      '" stroke="' + color + '" stroke-width="2.2"/>' +
      '<polygon points="' +
      b0.toFixed(1) + "," + b1.toFixed(1) + " " +
      (hx + 5 * px).toFixed(1) + "," + (hy + 5 * py).toFixed(1) + " " +
      (hx - 5 * px).toFixed(1) + "," + (hy - 5 * py).toFixed(1) +
      '" fill="' + color + '"/>' +
      '<text x="' + mx.toFixed(1) + '" y="' + my.toFixed(1) +
      '" fill="' + color + '" font-size="12" font-family="Times New Roman, serif">' +
      label + "</text>"
    );
  }

  function panel(title, Ca, Ct, Wa, Wt, U, C, W, alpha, beta, ox) {
    var o = ox || 0;
    var cx = o + panelW / 2;
    var cy = 40 + (panelH - 20) / 2;
    var x0 = o + 18;
    var x1 = o + panelW - 18;
    var y0 = 40;
    var y1 = 40 + panelH - 24;
    return (
      '<rect x="' + o + '" y="0" width="' + panelW + '" height="' + (panelH + 40) +
      '" fill="#f4f0fa" stroke="#000" stroke-width="1"/>' +
      '<text x="' + cx + '" y="18" text-anchor="middle" font-size="15" font-weight="bold" ' +
      'font-family="Times New Roman, serif">' + title + "</text>" +
      '<text x="' + cx + '" y="34" text-anchor="middle" font-size="12" ' +
      'font-family="Times New Roman, serif">α=' + fmt(alpha, 4) + "°   β=" + fmt(beta, 4) + "°</text>" +
      // axes through origin of velocity space
      '<line x1="' + x0 + '" y1="' + cy + '" x2="' + x1 + '" y2="' + cy +
      '" stroke="#666" stroke-width="1"/>' +
      '<line x1="' + cx + '" y1="' + y0 + '" x2="' + cx + '" y2="' + y1 +
      '" stroke="#666" stroke-width="1"/>' +
      '<text x="' + (x1 - 4) + '" y="' + (cy + 14) +
      '" text-anchor="end" font-size="11" font-family="Times New Roman, serif">axial →</text>' +
      '<text x="' + (cx + 6) + '" y="' + (y0 + 12) +
      '" font-size="11" font-family="Times New Roman, serif">θ ↑</text>' +
      // C (absolute), W (relative), U (blade speed) — classic velocity triangle
      arrow(cx, cy, 0, 0, Ca, Ct, "#0000aa", "C=" + fmt(C, 4)) +
      arrow(cx, cy, 0, 0, Wa, Wt, "#aa0000", "W=" + fmt(W, 4)) +
      arrow(cx, cy, Wa, Wt, Ca, Ct, "#007700", "U=" + fmt(U, 4))
    );
  }

  var Wtot = panelW * 2 + 12;
  var Htot = panelH + 40;
  el.innerHTML =
    '<svg width="' + Wtot + '" height="' + Htot +
    '" viewBox="0 0 ' + Wtot + " " + Htot +
    '" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Velocity triangles">' +
    panel("Inlet triangle", m.Ca1, m.Ct1, Wa1, Wt1, m.U, m.C1, m.W1, m.alpha1, m.beta1, 0) +
    panel("Outlet triangle", m.Ca2, m.Ct2, Wa2, Wt2, m.U, m.C2, m.W2, m.alpha2, m.beta2, panelW + 12) +
    '<text x="8" y="' + (Htot - 6) +
    '" font-size="11" font-family="Times New Roman, serif" fill="#333">' +
    "blue C = absolute · red W = relative · green U = blade speed (C = W + U)</text>" +
    "</svg>";
}

function meanlinePayload(form) {
  try {
    var m = meanlineFromForm(form);
    if (!m) return null;
    return {
      beta1_deg: m.beta1,
      beta2_deg: m.beta2,
      blade_speed_u_m_s: m.U,
      w1_m_s: m.W1,
      p1_pa: m.p1,
      t1_k: m.T1,
      gamma: m.gamma,
      r_specific_j_kg_k: m.R,
      mu_pa_s: m.mu,
      chord_m: m.chord,
      solidity: m.solidity,
      blade_name: m.blade_name,
      pure_impulse_lock: m.pure,
      y_plus_target: form.yplus ? (parseFloat(form.yplus.value) || 1.0) : 1.0,
      mean_radius_m: m.r_m,
      rpm: m.rpm,
      u_from_rpm: !!m.u_from_rpm,
      span_m: m.span,
      mass_flow_kg_s: form.mdot ? fromDisplay(parseFloat(form.mdot.value) || 0, "mdot") : 0,
      power_target_w: form.power ? fromDisplay(parseFloat(form.power.value) || 0, "power") : 0,
      incidence_deg: m.incidence,
      deviation_deg: m.deviation
    };
  } catch (eMl) {
    console.warn("meanlinePayload", eMl);
    return null;
  }
}

/* ---- §2 LPRE impulse turbine design board ---- */
function _num(el, defv) {
  if (!el) return defv;
  var v = parseFloat(el.value);
  return isNaN(v) ? defv : v;
}

function bladeShapeFromForm(form) {
  form = form || document.forms.bladeform;
  if (!form) {
    return {
      profile_family: "impulse_bucket",
      upper_sagitta_c: 0.48,
      lower_sagitta_c: 0.28,
      thickness_ratio: 0.20,
      wall_thickness_c: 0.20,
      bucket_suction_cutback: 0.0,
      thickness_peak_x: 0.50,
      le_fillet_r_c: 0.0,
      te_fillet_r_c: 0.0,
      inlet_line_frac: 0.0,
      outlet_line_frac: 0.0,
      arc_bulge: 1.10,
      n_points: 160,
      stagger_deg: null,
      camber_dist: 0.5,
      le_shape: "circular",
      te_thickness_c: 0.0,
      te_wedge_deg: 10.0
    };
  }
  var hu = _num(form.upper_h_v || form.upper_h, 0.48);
  var hl = _num(form.lower_h_v || form.lower_h, 0.28);
  hu = Math.min(0.70, Math.max(0.12, hu));
  hl = Math.min(0.65, Math.max(0.02, hl));
  if (hl >= hu - 0.04) hl = Math.max(0.02, hu - 0.08);
  var thk = hu - hl;
  var le = _num(form.le_v || form.le, 0.0);
  le = Math.min(0.15, Math.max(0.0, le));
  // TE fillet: dedicated te_fillet field, fallback to legacy te / te_thk
  var teF = 0.0;
  if (form.te_fillet_v || form.te_fillet) {
    teF = _num(form.te_fillet_v || form.te_fillet, 0.0);
  } else if (form.te_v || form.te) {
    teF = _num(form.te_v || form.te, 0.0);
  }
  teF = Math.min(0.15, Math.max(0.0, teF));
  // Keep legacy arc_bulge roughly in sync (upper ~ semi-circle * scoop)
  var bulge = 1.0 + 2.0 * (hu - 0.5);
  bulge = Math.min(1.6, Math.max(0.7, bulge));
  var stRaw = form.stagger ? String(form.stagger.value).trim() : "";
  var stagger = (stRaw === "" || stRaw.toLowerCase() === "auto") ? null : parseFloat(stRaw);
  if (stagger != null && isNaN(stagger)) stagger = null;
  // Sync hidden legacy fields for any old readers
  if (form.thk) form.thk.value = String(thk.toFixed(4));
  if (form.thk_v) form.thk_v.value = String(thk.toFixed(4));
  if (form.wall_t) form.wall_t.value = String(thk.toFixed(4));
  if (form.bulge) form.bulge.value = String(bulge.toFixed(3));
  if (form.te) form.te.value = String(teF);
  if (form.te_v) form.te_v.value = String(teF);
  return {
    profile_family: "impulse_bucket",
    upper_sagitta_c: hu,
    lower_sagitta_c: hl,
    thickness_ratio: thk,
    wall_thickness_c: thk,
    bucket_suction_cutback: 0.0,
    thickness_peak_x: 0.50,
    le_fillet_r_c: le,
    te_fillet_r_c: teF,
    inlet_line_frac: 0.0,
    outlet_line_frac: 0.0,
    arc_bulge: bulge,
    n_points: 160,
    stagger_deg: stagger,
    camber_dist: 0.5,
    le_shape: "circular",
    te_thickness_c: (function () {
      var t = _num(form.te_thk_v || form.te_thk, 0.02);
      return Math.min(0.12, Math.max(0.0, t));
    })(),
    te_wedge_deg: _num(form.te_wedge_v || form.te_wedge, 12.0)
  };
}

function syncBladeSliders(form) { syncLpreSliders(form); syncArcSliders(form); }

var _DIM_SLIDER_KEYS = [
  "upper_h", "lower_h", "le", "te_fillet", "te_thk", "te_wedge", "t_min_c",
  "os_sl", "axial_gap_c", "hub_seal_pct", "root_fillet_c",
  "eta_ts", "u_over_c0", "work_split", "n_sec_sl"
];

function syncArcSliders(form) {
  if (!form) return;
  _DIM_SLIDER_KEYS.forEach(function (k) {
    if (form[k] && form[k + "_v"]) form[k + "_v"].value = form[k].value;
  });
  // os_target aliases os_sl
  if (form.os_sl && form.os_target) form.os_target.value = form.os_sl.value;
  if (form.n_sec_sl && form.n_sectors) form.n_sectors.value = form.n_sec_sl.value;
  var hu = _num(form.upper_h_v || form.upper_h, 0.48);
  var hl = _num(form.lower_h_v || form.lower_h, 0.28);
  if (hl >= hu - 0.04) {
    hl = Math.max(0.02, hu - 0.08);
    if (form.lower_h) form.lower_h.value = String(hl);
    if (form.lower_h_v) form.lower_h_v.value = String(hl.toFixed(3));
  }
  // axial gap absolute from /c
  var c = _num(form.chord_c, 0.04);
  var gxc = _num(form.axial_gap_c_v || form.axial_gap_c, 0.15);
  if (form.axial_gap && document.activeElement !== form.axial_gap) {
    form.axial_gap.value = String((gxc * c).toPrecision(4));
  }
  updateArcDerived(form);
}

function onArcText(key) { onDimText(key); }

function onDimSlider(el) {
  var form = document.forms.bladeform;
  if (!form || !el) return;
  if (el.name && form[el.name + "_v"]) form[el.name + "_v"].value = el.value;
  if (el.name === "os_sl" && form.os_target) form.os_target.value = el.value;
  if (el.name === "n_sec_sl" && form.n_sectors) form.n_sectors.value = el.value;
  syncArcSliders(form);
  lpreDerivedUpdate();
  onBladeChange();
}

function onDimText(key) {
  var form = document.forms.bladeform;
  if (!form) return;
  // text field may be key_v or alias
  var vEl = form[key + "_v"] || form[key];
  var sEl = form[key];
  if (key === "os_sl" && form.os_target) {
    if (form.os_sl) form.os_sl.value = form.os_target.value;
  } else if (key === "n_sec_sl" && form.n_sectors) {
    if (form.n_sec_sl) form.n_sec_sl.value = form.n_sectors.value;
  } else if (vEl && sEl && form[key + "_v"]) {
    sEl.value = form[key + "_v"].value;
  }
  syncArcSliders(form);
  lpreDerivedUpdate();
  onBladeChange();
}

function onChordSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  var mm = parseFloat(el.value);
  if (!(mm > 0)) return;
  form.chord_c.value = String(mm / 1000);
  onLpreChange();
  onBladeChange();
}
function onChordText() {
  var form = document.forms.bladeform;
  if (!form || !form.chord_c || !form.chord_mm) return;
  var m = parseFloat(form.chord_c.value);
  if (m > 0) form.chord_mm.value = String(m * 1000);
}

function onSoliditySlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.solidity_sig.value = el.value;
  onSolidityManual();
  onBladeChange();
}
function onZSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.Z.value = el.value;
  onLpreChange();
  onBladeChange();
}
function onZText() {
  var form = document.forms.bladeform;
  if (!form || !form.Z || !form.Z_sl) return;
  form.Z_sl.value = form.Z.value;
}
function onRmSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.rm.value = String(parseFloat(el.value) / 1000);
  // keep hub/tip about rm
  var ht = _num(form.hub_tip_sl || form.hub_tip, 0.889);
  var spanFrac = (1 - ht) / (1 + ht); // rough
  var rm = parseFloat(form.rm.value);
  var htR = _num(form.hub_tip, 0.889);
  // r_t - r_h = span; r_m = 0.5(rt+rh); rh/rt = ht
  // rt = 2 rm / (1+ht), rh = ht * rt
  var rt = 2 * rm / (1 + htR);
  var rh = htR * rt;
  if (form.rt) form.rt.value = String(rt.toPrecision(5));
  if (form.rh) form.rh.value = String(rh.toPrecision(5));
  onLpreChange();
  onBladeChange();
}
function onRmText() {
  var form = document.forms.bladeform;
  if (!form || !form.rm || !form.rm_mm) return;
  var m = parseFloat(form.rm.value);
  if (m > 0) form.rm_mm.value = String(m * 1000);
}
function onStaggerSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  var v = parseFloat(el.value);
  if (Math.abs(v) < 0.25) form.stagger.value = "";
  else form.stagger.value = String(v);
  onBladeChange();
}
function onHubTipSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  var ht = parseFloat(el.value);
  form.hub_tip.value = String(ht);
  var rm = _num(form.rm, 0.425);
  var rt = 2 * rm / (1 + ht);
  var rh = ht * rt;
  form.rt.value = String(rt.toPrecision(5));
  form.rh.value = String(rh.toPrecision(5));
  onLpreChange();
  onBladeChange();
}
function onAspectSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.aspect_tgt.value = el.value;
  var ar = parseFloat(el.value);
  var c = _num(form.chord_c, 0.04);
  var rm = _num(form.rm, 0.425);
  var span = ar * c;
  form.rt.value = String((rm + 0.5 * span).toPrecision(5));
  form.rh.value = String((rm - 0.5 * span).toPrecision(5));
  var ht = (rm - 0.5 * span) / Math.max(rm + 0.5 * span, 1e-9);
  if (form.hub_tip_sl) form.hub_tip_sl.value = String(Math.min(0.98, Math.max(0.7, ht)));
  if (form.hub_tip) form.hub_tip.value = String(ht.toFixed(3));
  onLpreChange();
  onBladeChange();
}
function onAspectText() {
  var form = document.forms.bladeform;
  if (!form || !form.aspect_tgt || !form.aspect_sl) return;
  form.aspect_sl.value = form.aspect_tgt.value;
  onAspectSlider(form.aspect_sl);
}
function onTipClrPctSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.tip_clr_pct.value = el.value;
  var pct = parseFloat(el.value);
  var span = _num(form.span_h, 0.05);
  form.tip_clr.value = String((pct / 100) * span);
  onLpreChange();
}
function onTipClrPctText() {
  var form = document.forms.bladeform;
  if (!form) return;
  if (form.tip_clr_pct_sl) form.tip_clr_pct_sl.value = form.tip_clr_pct.value;
  onTipClrPctSlider(form.tip_clr_pct_sl || { value: form.tip_clr_pct.value });
}
function onDomainSlider(which) {
  var form = document.forms.bladeform;
  if (!form) return;
  if (which === "up" && form.x_up_sl && form.x_up_c) {
    form.x_up_c.value = form.x_up_sl.value;
    syncDomainExtents(form.x_up_c);
  }
  if (which === "dn" && form.x_dn_sl && form.x_dn_c) {
    form.x_dn_c.value = form.x_dn_sl.value;
    syncDomainExtents(form.x_dn_c);
  }
  onBladeChange();
}
function onPRSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.lpre_PR.value = el.value;
  onLpreChange();
}
function onPhiSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.phi.value = el.value;
  onLpreChange();
}
function onZwSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.zweifel.value = el.value;
  onLpreChange();
}
function onBetaSlider(n) {
  var form = document.forms.bladeform;
  if (!form) return;
  if (n === 1 && form.beta1_sl) form.lpre_beta1.value = form.beta1_sl.value;
  if (n === 2 && form.beta2_sl) form.lpre_beta2.value = form.beta2_sl.value;
  onLpreChange();
}
function onIncDevSlider(which) {
  var form = document.forms.bladeform;
  if (!form) return;
  if (which === "inc" && form.inc_sl) form.lpre_inc.value = form.inc_sl.value;
  if (which === "dev" && form.dev_sl) form.lpre_dev.value = form.dev_sl.value;
  onLpreChange();
}
function onReactionSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.reaction.value = el.value;
  onLpreChange();
}
function onEpsSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.epsilon.value = el.value;
  onLpreChange();
}
function onMnSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.Mn_exit.value = el.value;
  onLpreChange();
}
function onARSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.nozzle_AR.value = el.value;
  onLpreChange();
}
function onHnSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.hn_over_hr.value = el.value;
  onLpreChange();
}
function onAlpha1Slider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.alpha1.value = el.value;
  onLpreChange();
}
function onRowsSlider(el) {
  var form = document.forms.bladeform;
  if (!form) return;
  form.n_rows.value = el.value;
  onLpreChange();
}

function updateArcDerived(form) {
  form = form || document.forms.bladeform;
  var box = document.getElementById("arc_derived");
  if (!form || !box) return;
  var hu = _num(form.upper_h_v || form.upper_h, 0.48);
  var hl = _num(form.lower_h_v || form.lower_h, 0.28);
  if (hl >= hu - 0.04) hl = Math.max(0.02, hu - 0.08);
  var thk = hu - hl;
  var c = _num(form.chord_c, 0.04);
  var s = _num(form.pitch_s, 0);
  if (!(s > 0)) {
    var rm = _num(form.rm, 0.425);
    var Z = Math.max(_num(form.Z, 60), 1);
    s = 2 * Math.PI * rm / Z;
  }
  var le = _num(form.le_v || form.le, 0);
  var teF = _num(form.te_fillet_v || form.te_fillet, 0);
  var teThk = _num(form.te_thk_v || form.te_thk, 0.02);
  var tMin = _num(form.t_min_c_v || form.t_min_c, 0.08);
  var rCap = s > 0 && c > 0 ? 0.5 * (s / c) : 0.1;
  rCap = Math.min(0.15, rCap);
  var leEff = Math.min(le, rCap);
  var teEff = Math.min(teF, rCap);
  var osT = _num(form.os_target || form.os_sl, 0.35);
  box.innerHTML =
    "h<sub>u</sub>/c=" + hu.toFixed(3) + " · h<sub>l</sub>/c=" + hl.toFixed(3) +
    " · mid t/c≈" + thk.toFixed(3) + " (" + (thk * c * 1000).toFixed(2) + " mm) · t<sub>TE</sub>/c=" + teThk.toFixed(3) +
    " · t<sub>min</sub>/c=" + tMin.toFixed(3) +
    "<br>LE " + (leEff <= 1e-6 ? "sharp" : ("r/c=" + leEff.toFixed(3))) +
    " · TE " + (teEff <= 1e-6 ? "sharp" : ("r/c=" + teEff.toFixed(3))) +
    " · fillet cap r/c≤" + rCap.toFixed(3) + " · s=" + s.toPrecision(4) + " m · σ=" +
    (c / Math.max(s, 1e-12)).toFixed(3) + " · o/s target=" + osT.toFixed(2) +
    "<br>Passage: upper of blade <i>i</i> ↔ lower of <i>i</i>+1 · g<sub>x</sub>/c=" +
    _num(form.axial_gap_c_v || form.axial_gap_c, 0.15).toFixed(2);
}

function syncLpreSliders(form) {
  if (!form) return;
  [
    "thk", "peak", "bulge", "le", "te_thk", "te_fillet", "te_wedge", "upper_h", "lower_h",
    "u_over_c0", "t_min_c", "eta_ts", "work_split", "axial_gap_c", "hub_seal_pct", "root_fillet_c"
  ].forEach(function (k) {
    if (form[k] && form[k + "_v"]) form[k + "_v"].value = form[k].value;
  });
  if (form.wall_t && form.thk) {
    form.wall_t.value = form.thk.value;
    if (form.wall_t_v) form.wall_t_v.value = form.thk.value;
  }
  // keep packing sliders aligned
  if (form.chord_c && form.chord_mm) {
    var cm = parseFloat(form.chord_c.value);
    if (cm > 0 && document.activeElement !== form.chord_mm) form.chord_mm.value = String(cm * 1000);
  }
  if (form.Z && form.Z_sl && document.activeElement !== form.Z_sl) form.Z_sl.value = form.Z.value;
  if (form.solidity_sig && form.solidity_sl && document.activeElement !== form.solidity_sl)
    form.solidity_sl.value = form.solidity_sig.value;
  if (form.rm && form.rm_mm) {
    var rm = parseFloat(form.rm.value);
    if (rm > 0 && document.activeElement !== form.rm_mm) form.rm_mm.value = String(rm * 1000);
  }
  if (form.lpre_PR && form.PR_sl && document.activeElement !== form.PR_sl)
    form.PR_sl.value = form.lpre_PR.value;
  if (form.phi && form.phi_sl && document.activeElement !== form.phi_sl) form.phi_sl.value = form.phi.value;
  if (form.zweifel && form.zw_sl && document.activeElement !== form.zw_sl)
    form.zw_sl.value = form.zweifel.value;
  if (form.lpre_beta1 && form.beta1_sl) form.beta1_sl.value = form.lpre_beta1.value;
  if (form.lpre_beta2 && form.beta2_sl) form.beta2_sl.value = form.lpre_beta2.value;
  if (form.lpre_inc && form.inc_sl) form.inc_sl.value = form.lpre_inc.value;
  if (form.lpre_dev && form.dev_sl) form.dev_sl.value = form.lpre_dev.value;
  if (form.epsilon && form.eps_sl) form.eps_sl.value = form.epsilon.value;
  if (form.Mn_exit && form.Mn_sl) form.Mn_sl.value = form.Mn_exit.value;
  if (form.nozzle_AR && form.AR_sl) form.AR_sl.value = form.nozzle_AR.value;
  if (form.hn_over_hr && form.hn_sl) form.hn_sl.value = form.hn_over_hr.value;
  if (form.alpha1 && form.alpha1_sl) form.alpha1_sl.value = form.alpha1.value;
  if (form.n_rows && form.n_rows_sl) form.n_rows_sl.value = form.n_rows.value;
  if (form.reaction && form.reaction_sl) form.reaction_sl.value = form.reaction.value;
  if (form.x_up_c && form.x_up_sl) form.x_up_sl.value = form.x_up_c.value;
  if (form.x_dn_c && form.x_dn_sl) form.x_dn_sl.value = form.x_dn_c.value;
  updateArcDerived(form);
}

function lpreDerivedUpdate() {
  var f = document.forms.bladeform;
  if (!f) return;
  var rt = _num(f.rt, 0.45), rh = _num(f.rh, 0.40), rm = _num(f.rm, 0.425);
  if (f.rt && f.rh && document.activeElement !== f.rm) {
    rm = 0.5 * (rt + rh);
    if (f.rm) f.rm.value = String(rm.toPrecision(5));
  }
  var span = Math.max(rt - rh, 1e-6);
  var Z = Math.max(_num(f.Z, 60), 1);
  var c = _num(f.chord_c, 0.04);
  var s = 2 * Math.PI * rm / Z;
  if (f.pitch_s && document.activeElement !== f.pitch_s) f.pitch_s.value = String(s.toPrecision(5));
  var sig = c / Math.max(s, 1e-12);
  if (f.solidity_sig && document.activeElement !== f.solidity_sig) f.solidity_sig.value = String(sig.toFixed(4));
  if (f.span_h) f.span_h.value = String(span.toPrecision(4));
  if (f.hub_tip) f.hub_tip.value = String((rh / Math.max(rt, 1e-9)).toFixed(3));
  if (f.tip_d) f.tip_d.value = String((2 * rt).toPrecision(4));
  if (f.ann_A) f.ann_A.value = String((2 * Math.PI * rm * span).toPrecision(4));
  if (f.aspect) f.aspect.value = String((span / Math.max(c, 1e-12)).toFixed(3));
  // Prefer tip clr % slider as source of truth when present
  var tipPct = _num(f.tip_clr_pct_sl || f.tip_clr_pct, NaN);
  var tipClr;
  if (!isNaN(tipPct) && tipPct >= 0) {
    tipClr = (tipPct / 100) * span;
    if (f.tip_clr && document.activeElement !== f.tip_clr) f.tip_clr.value = String(tipClr.toPrecision(4));
    if (f.tip_clr_pct) f.tip_clr_pct.value = String(tipPct.toFixed(2));
  } else {
    tipClr = _num(f.tip_clr, 0.001);
    if (f.tip_clr_pct) f.tip_clr_pct.value = String((100 * tipClr / span).toFixed(2));
  }
  // mid solid from dual-arc (or legacy thk)
  var hu = _num(f.upper_h_v || f.upper_h, NaN);
  var hl = _num(f.lower_h_v || f.lower_h, NaN);
  var thk;
  if (!isNaN(hu) && !isNaN(hl)) {
    if (hl >= hu - 0.04) hl = Math.max(0.02, hu - 0.08);
    thk = hu - hl;
  } else {
    thk = _num(f.thk_v || f.thk, 0.20);
  }
  if (f.t_max_m && document.activeElement !== f.t_max_m) {
    f.t_max_m.value = String((thk * c).toPrecision(4));
  }
  if (typeof updateArcDerived === "function") updateArcDerived(f);
  var rpm = _num(f.lpre_rpm, 5500);
  var U = (2 * Math.PI * rpm / 60) * rm;
  var eps = f.partial_admission && f.partial_admission.checked ? _num(f.epsilon, 0.35) : 1.0;
  var PR = _num(f.lpre_PR, 8);
  var g = _num(f.lpre_gamma, 1.3);
  var R = _num(f.lpre_R, 320);
  var T0 = _num(f.lpre_T0, 1100);
  // rough c0 from isentropic PR (perfect gas)
  var tau = Math.pow(Math.max(PR, 1.001), (g - 1) / g);
  var dhis = Math.max(0, g / (g - 1) * R * T0 * (1 - 1 / tau));
  var c0 = Math.sqrt(2 * dhis);
  var uoc = _num(f.u_over_c0_v || f.u_over_c0, 0.30);
  var U_from_ratio = uoc * c0;
  var p0 = _num(f.lpre_p0, 550000);
  var p_ex = p0 / Math.max(PR, 1.001);
  if (f.p_ex) f.p_ex.value = String(p_ex.toPrecision(5));
  if (f.c0_out) f.c0_out.value = String(c0.toFixed(1));
  if (f.U_uoc_out) f.U_uoc_out.value = String(U_from_ratio.toFixed(1));
  if (f.U_rm_out) f.U_rm_out.value = String(U.toFixed(1));
  var teThkC = _num(f.te_thk_v || f.te_thk, 0.02);
  var tMinC = _num(f.t_min_c_v || f.t_min_c, 0.08);
  if (f.t_min_m) f.t_min_m.value = String((tMinC * c).toPrecision(4));
  if (f.t_te_m) f.t_te_m.value = String((teThkC * c).toPrecision(4));
  var U_tip = (2 * Math.PI * rpm / 60) * rt;
  var a0 = Math.sqrt(Math.max(g * R * T0, 1));
  var M_tip = U_tip / a0;
  var AR = _num(f.nozzle_AR, 1.8);
  var hn_over = _num(f.hn_over_hr, 1.0);
  var h_n = hn_over * span;
  if (f.h_n) f.h_n.value = String(h_n.toPrecision(4));
  // rough isentropic choked A* from mdot if set
  var mdot = _num(f.lpre_mdot, 0);
  var A_star = 0;
  if (mdot > 0 && p0 > 0 && T0 > 0) {
    var Gamma = Math.sqrt(g) * Math.pow(2 / (g + 1), 0.5 * (g + 1) / (g - 1));
    A_star = mdot * Math.sqrt(R * T0) / (p0 * Gamma);
  }
  if (f.A_star && (String(f.A_star.value).trim() === "" || document.activeElement !== f.A_star)) {
    if (A_star > 0) f.A_star.value = String(A_star.toPrecision(4));
  }
  var A_star_use = _num(f.A_star, A_star);
  var A_e = A_star_use * AR;
  if (f.A_exit) f.A_exit.value = A_e > 0 ? String(A_e.toPrecision(4)) : "";
  var eta_ts = _num(f.eta_ts_v || f.eta_ts, 0.65);
  var n_sec = Math.max(1, Math.round(_num(f.n_sectors || f.n_sec_sl, 4)));
  var work_split = _num(f.work_split_v || f.work_split, 1.0);
  var hubSealPct = _num(f.hub_seal_pct_v || f.hub_seal_pct, 0.5);
  var rootFilC = _num(f.root_fillet_c_v || f.root_fillet_c, 0.04);
  var U_tip_lim = _num(f.U_tip_lim, 450);
  var M_tip_lim = _num(f.M_tip_lim, 1.2);
  var osT = _num(f.os_target || f.os_sl, 0.35);

  var box = document.getElementById("lpre_derived");
  if (box) {
    box.innerHTML =
      "r_m=" + rm.toPrecision(4) + " m · h=" + span.toPrecision(3) + " m · D_t=" + (2 * rt).toPrecision(3) +
      " m · Z=" + Z + " · s=" + s.toPrecision(4) + " m · σ=" + sig.toFixed(3) + " · h/c=" + (span / c).toFixed(2) +
      "<br>N=" + rpm.toFixed(0) + " rpm · U(Ω r_m)=" + U.toFixed(1) + " m/s · U_tip=" + U_tip.toFixed(1) +
      " · c₀≈" + c0.toFixed(0) + " m/s · U/c₀=" + uoc.toFixed(2) + " · U_from_ratio≈" + U_from_ratio.toFixed(1) +
      "<br>ε=" + eps.toFixed(2) + " · N_sec=" + n_sec + " · A_ann≈" + (2 * Math.PI * rm * span).toPrecision(3) +
      " m² · A_eff≈εA=" + (eps * 2 * Math.PI * rm * span).toPrecision(3) +
      " · A*≈" + (A_star_use > 0 ? A_star_use.toPrecision(3) : "—") +
      " · A_e≈" + (A_e > 0 ? A_e.toPrecision(3) : "—") +
      "<br>t_mid=" + (thk * c * 1000).toFixed(2) + " mm · t_TE=" + (teThkC * c * 1000).toFixed(2) +
      " mm · t_min=" + (tMinC * c * 1000).toFixed(2) + " mm · o/s_tgt=" + osT.toFixed(2) +
      " · tip clr/h=" + (100 * tipClr / span).toFixed(1) + "% · η_ts tgt=" + eta_ts.toFixed(2) +
      " · p_ex≈" + p_ex.toPrecision(4) + " Pa";
  }
  var lim = document.getElementById("design_limits");
  if (lim) {
    var flags = [];
    if (U_tip > U_tip_lim) flags.push("U_tip " + U_tip.toFixed(0) + " > limit " + U_tip_lim);
    else flags.push("U_tip OK (" + U_tip.toFixed(0) + " / " + U_tip_lim + " m/s)");
    if (M_tip > M_tip_lim) flags.push("M_tip " + M_tip.toFixed(2) + " > limit " + M_tip_lim);
    else flags.push("M_tip OK (" + M_tip.toFixed(2) + ")");
    if (thk < tMinC) flags.push("mid t/c " + thk.toFixed(3) + " < t_min/c " + tMinC.toFixed(3));
    if (Math.abs(U - U_from_ratio) / Math.max(U_from_ratio, 1) > 0.15)
      flags.push("U(Ωrm) vs U(U/c₀) mismatch " + U.toFixed(0) + " vs " + U_from_ratio.toFixed(0) + " m/s");
    lim.innerHTML = flags.join(" · ");
  }
  var ah = document.getElementById("admission_hint");
  if (ah) {
    ah.textContent = f.partial_admission && f.partial_admission.checked
      ? ("Partial admission ε=" + eps.toFixed(2) + " · " + n_sec + " sectors · A_eff for ṁ")
      : "Full admission (ε=1)";
  }
  window._lpreDesign = {
    architecture: {
      type: f.turb_type ? f.turb_type.value : "axial_impulse",
      n_stages: _num(f.n_stages, 1),
      n_rows: _num(f.n_rows, 1),
      reaction: _num(f.reaction, 0),
      partial_admission: !!(f.partial_admission && f.partial_admission.checked),
      epsilon: eps,
      n_sectors: n_sec,
      n_nozzles: _num(f.n_nozzles, 6),
      shrouded: !!(f.shrouded && f.shrouded.checked),
      work_split_row1: work_split
    },
    system: {
      power_w: _num(f.lpre_power_w, 0),
      rpm: rpm,
      mdot_kg_s: mdot,
      p0_pa: p0,
      p_ex_pa: p_ex,
      T0_k: T0,
      PR: PR,
      gamma: g,
      R_j_kg_k: R,
      mw: f.mw_gas && String(f.mw_gas.value).trim() !== "" ? _num(f.mw_gas, 0) : null,
      gas_note: f.gas_note ? f.gas_note.value : "",
      u_over_c0: uoc,
      phi: _num(f.phi, 0.35),
      psi: f.psi && String(f.psi.value).trim() !== "" ? _num(f.psi, 0) : null,
      zweifel: _num(f.zweifel, 0.9),
      eta_ts_target: eta_ts,
      beta1_deg: _num(f.lpre_beta1, 72),
      beta2_deg: _num(f.lpre_beta2, -72),
      incidence_deg: _num(f.lpre_inc, 0),
      deviation_deg: _num(f.lpre_dev, 0),
      w1_m_s: _num(f.lpre_w1, 950)
    },
    annulus: {
      tip_radius_m: rt, hub_radius_m: rh, mean_radius_m: rm,
      span_m: span, hub_tip: rh / Math.max(rt, 1e-9),
      tip_diameter_m: 2 * rt, annulus_area_m2: 2 * Math.PI * rm * span
    },
    nozzle: {
      Mn_exit: _num(f.Mn_exit, 1.8),
      area_ratio: AR,
      hn_over_hr: hn_over,
      alpha1_deg: _num(f.alpha1, 20),
      A_star_m2: A_star_use > 0 ? A_star_use : null,
      A_exit_m2: A_e > 0 ? A_e : null,
      h_n_m: h_n
    },
    packing: {
      Z: Z, chord_m: c, pitch_m: s, solidity: sig,
      aspect_ratio: span / Math.max(c, 1e-12),
      os_target: osT,
      stagger_deg: (function () {
        var st = f.stagger ? String(f.stagger.value).trim() : "";
        if (!st || st.toLowerCase() === "auto") return null;
        var v = parseFloat(st);
        return isNaN(v) ? null : v;
      })()
    },
    metal: {
      upper_sagitta_c: !isNaN(hu) ? hu : null,
      lower_sagitta_c: !isNaN(hl) ? hl : null,
      t_mid_c: thk,
      te_thickness_c: teThkC,
      t_min_c: tMinC,
      root_fillet_c: rootFilC
    },
    clearances: {
      tip_clearance_m: tipClr,
      tip_clearance_over_h: tipClr / span,
      axial_gap_m: _num(f.axial_gap, 0.003),
      hub_seal_over_h: hubSealPct / 100
    },
    limits: {
      U_tip_m_s: U_tip,
      U_tip_limit_m_s: U_tip_lim,
      M_tip: M_tip,
      M_tip_limit: M_tip_lim,
      rho_mat: _num(f.rho_mat, 7800),
      sigma_allow_pa: _num(f.sigma_allow, 400e6)
    },
    derived: { U_m_s: U, c0_m_s: c0, U_from_uoc_m_s: U_from_ratio, p_ex_pa: p_ex }
  };
}

function onLpreChange() {
  syncLpreSliders(document.forms.bladeform);
  syncArcSliders(document.forms.bladeform);
  lpreDerivedUpdate();
  // live outline while editing packing / LPRE
  if (typeof previewBlade === "function") {
    try { previewBlade(); } catch (e) { /* ignore while loading */ }
  }
}

function onTmaxAbs() {
  var f = document.forms.bladeform;
  if (!f) return;
  var c = _num(f.chord_c, 0.04);
  var tmax = _num(f.t_max_m, 0.008);
  if (c > 0) {
    var tc = Math.min(0.45, Math.max(0.08, tmax / c));
    if (f.thk) f.thk.value = String(tc);
    if (f.thk_v) f.thk_v.value = String(tc);
  }
  onLpreChange();
}

function onPitchManual() {
  var f = document.forms.bladeform;
  if (!f) return;
  var s = _num(f.pitch_s, 0.04);
  var rm = _num(f.rm, 0.425);
  if (s > 0 && rm > 0) {
    var Z = Math.max(3, Math.round(2 * Math.PI * rm / s));
    if (f.Z) f.Z.value = String(Z);
  }
  onLpreChange();
}

function onSolidityManual() {
  var f = document.forms.bladeform;
  if (!f) return;
  var sig = _num(f.solidity_sig, 1.2);
  var c = _num(f.chord_c, 0.04);
  if (sig > 0 && f.pitch_s) f.pitch_s.value = String((c / sig).toPrecision(5));
  onPitchManual();
}

function packFromZweifel() {
  // Approximate: Z_w ≈ 2 (s/c) cos²β2 (tanβ1 - tanβ2) / cosλ  — simplify for pure impulse
  var f = document.forms.bladeform;
  if (!f) return;
  var Zw = _num(f.zweifel, 0.9);
  var b1 = _num(f.lpre_beta1, 72) * Math.PI / 180;
  var b2 = _num(f.lpre_beta2, -72) * Math.PI / 180;
  var c = _num(f.chord_c, 0.04);
  var rm = _num(f.rm, 0.425);
  // Impulse packing rule-of-thumb: s/c ≈ Zw / (2 * cos(βm)^2 * turning_factor)
  var turn = Math.abs(Math.tan(b1) - Math.tan(b2));
  var sc = Zw / Math.max(0.5 * turn, 0.3);
  sc = Math.min(1.4, Math.max(0.45, sc));
  var s = sc * c;
  var Z = Math.max(8, Math.round(2 * Math.PI * rm / s));
  if (f.Z) f.Z.value = String(Z);
  var s2 = 2 * Math.PI * rm / Z;
  if (f.pitch_s) f.pitch_s.value = String(s2.toPrecision(5));
  if (f.solidity_sig) f.solidity_sig.value = String((c / s2).toFixed(3));
  onLpreChange();
  var msg = document.getElementById("lpre_apply_msg");
  if (msg) msg.textContent = "Zweifel pack → Z=" + Z + " σ≈" + (c / s2).toFixed(3);
}

function applyLpreDesign() {
  var f = document.forms.bladeform;
  var mf = document.forms.meanline;
  if (!f || !mf) return;
  lpreDerivedUpdate();
  var d = window._lpreDesign || {};
  var a = d.annulus || {}, p = d.packing || {}, s = d.system || {};
  // §1 meanline
  if (mf.chord) mf.chord.value = String(p.chord_m || _num(f.chord_c, 0.04));
  if (mf.pitch) mf.pitch.value = String(p.pitch_m || _num(f.pitch_s, 0.04));
  if (mf.solidity) mf.solidity.value = String((p.solidity || 1.2).toFixed(5));
  if (mf.r_m) mf.r_m.value = String(a.mean_radius_m || _num(f.rm, 0.425));
  if (mf.span) mf.span.value = String(a.span_m || _num(f.span_h, 0.05));
  if (mf.rpm) mf.rpm.value = String(s.rpm || _num(f.lpre_rpm, 5500));
  if (mf.u_from_rpm) mf.u_from_rpm.checked = true;
  if (mf.beta1) mf.beta1.value = String(s.beta1_deg != null ? s.beta1_deg : 72);
  if (mf.beta2) mf.beta2.value = String(s.beta2_deg != null ? s.beta2_deg : -72);
  if (mf.incidence) mf.incidence.value = String(s.incidence_deg || 0);
  if (mf.deviation) mf.deviation.value = String(s.deviation_deg || 0);
  if (mf.w1) mf.w1.value = String(s.w1_m_s || 950);
  if (mf.p1) mf.p1.value = String(s.p0_pa || 550000);
  if (mf.T1) mf.T1.value = String(s.T0_k || 1100);
  if (mf.gamma) mf.gamma.value = String(s.gamma || 1.3);
  if (mf.R) mf.R.value = String(s.R_j_kg_k || 320);
  if (mf.mdot) mf.mdot.value = String(s.mdot_kg_s || 0);
  if (mf.power) mf.power.value = String(s.power_w || 0);
  if (mf.pure) mf.pure.checked = Math.abs(_num(f.reaction, 0)) < 0.05;
  if (mf.blade_name) {
    var nm = "lpre_impulse";
    if (window._lprePresetName) nm = window._lprePresetName;
    mf.blade_name.value = nm;
  }
  if (mf.pitch_sc && p.chord_m > 0) {
    var sc = (p.pitch_m || 0.04) / p.chord_m;
    mf.pitch_sc.value = String(Math.min(2, Math.max(0.4, sc)));
    if (mf.pitch_sc_v) mf.pitch_sc_v.value = String(sc.toFixed(3));
  }
  window._stageTable = {
    tip_radius_m: a.tip_radius_m,
    hub_radius_m: a.hub_radius_m,
    mean_radius_m: a.mean_radius_m,
    n_blades: p.Z,
    chord_m: p.chord_m,
    blade_spacing_m: p.pitch_m,
    min_blade_thickness_m: _num(f.thk_v || f.thk, 0.2) * (p.chord_m || 0.04),
    span_m: a.span_m,
    solidity: p.solidity,
    lpre: d
  };
  var msg = document.getElementById("lpre_apply_msg");
  if (msg) {
    msg.textContent = "Applied " + (window._lprePresetName || "custom") +
      ": Z=" + p.Z + " c=" + (p.chord_m || 0).toPrecision(3) +
      " σ=" + (p.solidity || 0).toFixed(2) + " r_m=" + (a.mean_radius_m || 0).toPrecision(3);
  }
  onMeanlineChange();
}

function _setLpreFields(map) {
  var f = document.forms.bladeform;
  if (!f) return;
  Object.keys(map).forEach(function (k) {
    if (!f[k]) return;
    if (f[k].type === "checkbox") f[k].checked = !!map[k];
    else f[k].value = String(map[k]);
    if (f[k + "_v"]) f[k + "_v"].value = String(map[k]);
  });
}


function presetDualArcSharp() {
  window._lprePresetName = "dual_arc_sharp";
  _setLpreFields({
    upper_h: 0.48, lower_h: 0.28, le: 0, te_fillet: 0,
    chord_c: 0.04, Z: 60, rm: 0.425
  });
  onLpreChange();
  onBladeChange();
  var m = document.getElementById("preset_msg");
  if (m) m.textContent = "Sharp dual-arc C-bucket (pointed LE/TE).";
}

function presetDualArcFillet() {
  window._lprePresetName = "dual_arc_fillet";
  _setLpreFields({
    upper_h: 0.48, lower_h: 0.28, le: 0.025, te_fillet: 0.02,
    chord_c: 0.04, Z: 60, rm: 0.425
  });
  onLpreChange();
  onBladeChange();
  var m = document.getElementById("preset_msg");
  if (m) m.textContent = "Dual-arc with mild tip fillets (capped by pitch gap).";
}

function presetDualArcDeep() {
  window._lprePresetName = "dual_arc_deep";
  _setLpreFields({
    upper_h: 0.55, lower_h: 0.22, le: 0.01, te_fillet: 0.01,
    chord_c: 0.04, Z: 55, rm: 0.425
  });
  onLpreChange();
  onBladeChange();
  var m = document.getElementById("preset_msg");
  if (m) m.textContent = "Deep scoop · thicker mid solid.";
}

function presetLpreF1() {
  // F-1 class: large GG turbine, ~5500 rpm, ~3 ft class pitch diameter, multi-row style scale
  window._lprePresetName = "f1_class_gg";
  _setLpreFields({
    turb_type: "axial_impulse", n_stages: 1, n_rows: 2, reaction: 0,
    partial_admission: false, epsilon: 1.0, n_nozzles: 1, shrouded: true,
    lpre_power_w: 4.1e7, lpre_rpm: 5500, lpre_mdot: 75, lpre_p0: 6.5e6, lpre_T0: 1200,
    lpre_PR: 10, lpre_gamma: 1.28, lpre_R: 290, u_over_c0: 0.28, phi: 0.32, zweifel: 0.85,
    lpre_beta1: 65, lpre_beta2: -65, lpre_inc: 0, lpre_dev: 2, lpre_w1: 700,
    rt: 0.46, rh: 0.40, rm: 0.43, Z: 80, chord_c: 0.055,
    Mn_exit: 1.6, nozzle_AR: 1.6, hn_over_hr: 1.0, alpha1: 22,
    upper_h: 0.46, lower_h: 0.30, le: 0.015, te_fillet: 0.012, thk: 0.16, peak: 0.50, bulge: 1.10, te_thk: 0,
    tip_clr: 0.0015, axial_gap: 0.005
  });
  onLpreChange();
  packFromZweifel();
  applyLpreDesign();
  var m = document.getElementById("preset_msg");
  if (m) m.textContent = "F-1 class GG scale loaded (≈55k hp / 5500 rpm family).";
}

function presetLpreH1() {
  // H-1 / Mark-class: smaller kerosene GG turbopump than F-1
  window._lprePresetName = "h1_class_gg";
  _setLpreFields({
    turb_type: "axial_impulse", n_stages: 1, n_rows: 1, reaction: 0,
    partial_admission: true, epsilon: 0.45, n_nozzles: 8, shrouded: false,
    lpre_power_w: 1.5e6, lpre_rpm: 30000, lpre_mdot: 3.5, lpre_p0: 4e6, lpre_T0: 1050,
    lpre_PR: 12, lpre_gamma: 1.3, lpre_R: 300, u_over_c0: 0.32, phi: 0.35, zweifel: 0.9,
    lpre_beta1: 70, lpre_beta2: -70, lpre_inc: 0, lpre_dev: 1.5, lpre_w1: 850,
    rt: 0.12, rh: 0.10, rm: 0.11, Z: 55, chord_c: 0.018,
    Mn_exit: 1.9, nozzle_AR: 2.0, hn_over_hr: 1.0, alpha1: 18,
    upper_h: 0.48, lower_h: 0.30, le: 0.02, te_fillet: 0.015, thk: 0.18, peak: 0.50, bulge: 1.10, te_thk: 0,
    tip_clr: 0.0008, axial_gap: 0.002
  });
  onLpreChange();
  applyLpreDesign();
  var m = document.getElementById("preset_msg");
  if (m) m.textContent = "H-1 / Mark-class GG scale loaded.";
}

function presetLpreSmallGG() {
  window._lprePresetName = "small_gg_pa";
  _setLpreFields({
    turb_type: "axial_impulse", n_stages: 1, n_rows: 1, reaction: 0,
    partial_admission: true, epsilon: 0.30, n_nozzles: 4, shrouded: false,
    lpre_power_w: 2e5, lpre_rpm: 60000, lpre_mdot: 0.5, lpre_p0: 3e6, lpre_T0: 1000,
    lpre_PR: 15, lpre_gamma: 1.3, lpre_R: 320, u_over_c0: 0.27, phi: 0.30, zweifel: 0.95,
    lpre_beta1: 72, lpre_beta2: -72, lpre_inc: 0, lpre_dev: 1, lpre_w1: 950,
    rt: 0.05, rh: 0.042, rm: 0.046, Z: 48, chord_c: 0.01,
    Mn_exit: 2.0, nozzle_AR: 2.2, hn_over_hr: 1.0, alpha1: 15,
    upper_h: 0.48, lower_h: 0.30, le: 0.02, te_fillet: 0.015, thk: 0.18, peak: 0.50, bulge: 1.10, te_thk: 0,
    tip_clr: 0.0005, axial_gap: 0.0015
  });
  onLpreChange();
  applyLpreDesign();
  var m = document.getElementById("preset_msg");
  if (m) m.textContent = "Small modern partial-admission GG loaded.";
}

function presetLpreDefault() {
  window._lprePresetName = "lpre_default";
  presetLpreSmallGG();
}

function resetBladeShape(form) {
  presetLpreDefault();
}

function onMeanlineChange() {
  if (!document.forms.meanline) return;
  var prev = window._lastMeanline;
  calcMeanline(document.forms.meanline);
  // Any §1 edit invalidates a previously built case
  if (window._pipeline.caseOk) {
    markCaseStale("§1 velocity triangles changed — rebuild case files in §3.");
  }
  onBladeChange({ fromMeanline: true });
}

function onBladeChange(opts) {
  opts = opts || {};
  var bf = document.forms.bladeform;
  if (bf) {
    ["thk", "peak", "bulge", "le", "te_thk", "te_fillet", "te_wedge", "upper_h", "lower_h", "u_over_c0", "t_min_c", "eta_ts", "work_split", "axial_gap_c", "hub_seal_pct", "root_fillet_c"].forEach(function (k) {
      if (bf[k] && bf[k + "_v"] && document.activeElement === bf[k + "_v"]) {
        bf[k].value = bf[k + "_v"].value;
      }
    });
    try { syncArcSliders(bf); } catch (eArc) { /* optional */ }
    try {
      if (typeof lpreDerivedUpdate === "function") lpreDerivedUpdate();
    } catch (eLpre) {
      console.warn("lpreDerivedUpdate", eLpre);
    }
  }
  try {
    propagateFromSection2();
  } catch (eProp) {
    console.warn("propagateFromSection2", eProp);
  }
  if (!opts.fromMeanline && window._pipeline && window._pipeline.caseOk) {
    markCaseStale("§2 blade metal changed — rebuild case files in §3.");
  }
  previewBlade();
}

/** Shared cascade domain extents (chord fractions). Kept in sync §2 ↔ §3. */
function getDomainExtents() {
  var cg = document.forms.casegen;
  var bf = document.forms.bladeform;
  var up = 0.5, dn = 1.0;
  function read(el) {
    if (!el) return null;
    var v = parseFloat(el.value);
    return isNaN(v) ? null : v;
  }
  var a = read(cg && cg.x_up_c) != null ? read(cg.x_up_c)
    : (read(bf && bf.x_up_c) != null ? read(bf.x_up_c) : up);
  var b = read(cg && cg.x_dn_c) != null ? read(cg.x_dn_c)
    : (read(bf && bf.x_dn_c) != null ? read(bf.x_dn_c) : dn);
  // clamp to engineering range (matches backend)
  a = Math.min(5, Math.max(0.05, a));
  b = Math.min(8, Math.max(0.05, b));
  return { x_up_c: a, x_dn_c: b };
}

function syncDomainExtents(src) {
  var v = src ? parseFloat(src.value) : NaN;
  if (isNaN(v)) return;
  var name = src.name || src.id || "";
  var isUp = name.indexOf("up") >= 0 || name.indexOf("x_up") >= 0;
  var isDn = name.indexOf("dn") >= 0 || name.indexOf("x_dn") >= 0 || name.indexOf("out") >= 0;
  var ids = isUp
    ? ["x_up_c_blade", "x_up_c_case"]
    : (isDn ? ["x_dn_c_blade", "x_dn_c_case"] : []);
  // also match by form field name
  var forms = [document.forms.bladeform, document.forms.casegen];
  forms.forEach(function (f) {
    if (!f) return;
    if (isUp && f.x_up_c && f.x_up_c !== src) f.x_up_c.value = src.value;
    if (isDn && f.x_dn_c && f.x_dn_c !== src) f.x_dn_c.value = src.value;
  });
  ids.forEach(function (id) {
    var el = document.getElementById(id);
    if (el && el !== src) el.value = src.value;
  });
  updateDomainReadouts();
}

function updateDomainReadouts() {
  var ext = getDomainExtents();
  var m = window._lastMeanline;
  var c = m && m.chord ? m.chord : 0.01;
  var lin = ext.x_up_c * c;
  var lout = ext.x_dn_c * c;
  var abs =
    "L_in=" + fmt(toDisplay(lin, "len"), 4) + " " + unitLabel("len") +
    " · L_out=" + fmt(toDisplay(lout, "len"), 4) + " " + unitLabel("len") +
    " · axial=" + fmt(toDisplay(lin + c + lout, "len"), 4) + " " + unitLabel("len");
  setField("domain_len_hint", abs);
  setField("domain_abs_readout", abs);
  setField(
    "from12_domain",
    "Domain: inlet " + fmt(ext.x_up_c, 3) + "c · outlet " + fmt(ext.x_dn_c, 3) + "c · " + abs
  );
}

function previewBlade() {
  var mlForm = document.forms.meanline;
  var bf = document.forms.bladeform;
  if (!mlForm || !bf) return;
  var payload = meanlinePayload(mlForm);
  if (!payload) {
    var svgMiss = document.getElementById("blade_svg");
    if (svgMiss) {
      svgMiss.innerHTML =
        '<rect width="780" height="400" fill="#f4f0fa"/>' +
        '<text x="24" y="40" font-size="14" fill="#a00">Meanline payload missing — fix §1 numbers, then Apply design in §2</text>';
    }
    return;
  }
  var ext = getDomainExtents();
  var cg = document.forms.casegen;
  // Mesh parity: cascade preview uses §3 n_blades; single-profile only if user picks 1
  var nPrev = parseInt(bf.n_prev && bf.n_prev.value, 10) || 3;
  var nCase = cg && cg.n_blades ? (parseInt(cg.n_blades.value, 10) || 3) : 3;
  var nShow = nPrev <= 1 ? 1 : nCase;
  // Keep §2 selector aligned with §3 when showing cascade
  if (nPrev > 1 && bf.n_prev) {
    for (var i = 0; i < bf.n_prev.options.length; i++) {
      if (parseInt(bf.n_prev.options[i].value, 10) === nCase) {
        bf.n_prev.selectedIndex = i;
        break;
      }
    }
  }
  // Prefer live §2 packing (chord / pitch / solidity) so arc + cascade update without Apply
  if (bf.chord_c) {
    var cLive = parseFloat(bf.chord_c.value);
    if (cLive > 0) payload.chord_m = cLive;
  }
  if (bf.pitch_s) {
    var sLive = parseFloat(bf.pitch_s.value);
    if (!(sLive > 0) && bf.rm && bf.Z) {
      var rmL = parseFloat(bf.rm.value), ZL = parseFloat(bf.Z.value);
      if (rmL > 0 && ZL > 0) sLive = 2 * Math.PI * rmL / ZL;
    }
    if (sLive > 0 && payload.chord_m > 0) {
      payload.pitch_m = sLive;
      payload.solidity = payload.chord_m / sLive;
    }
  } else if (bf.solidity_sig) {
    var sigL = parseFloat(bf.solidity_sig.value);
    if (sigL > 0) payload.solidity = sigL;
  }
  var body = {
    meanline: payload,
    blade_shape: bladeShapeFromForm(bf),
    n_blades: nShow,
    x_up_c: ext.x_up_c,
    x_dn_c: ext.x_dn_c
  };
  updateDomainReadouts();
  apiJson("/api/blade_preview", {
    method: "POST",
    body: JSON.stringify(body)
  })
    .then(function (data) {
      if (!data || !data.success) {
        var svg0 = document.getElementById("blade_svg");
        if (svg0) {
          svg0.innerHTML =
            '<rect width="780" height="400" fill="#f4f0fa"/>' +
            '<text x="24" y="40" font-size="14" fill="#a00">Blade preview failed: ' +
            String((data && (data.error || data.message)) || "no success") +
            "</text>";
        }
        return;
      }
      drawBladeSvg(data);
      fillBladeEngReadout(data, payload);
      var th = data.throat || {};
      var os = th.opening_o_s;
      var om = th.throat_o_m;
      setField("blade_os_out", os == null ? "—" : fmt(os, 4));
      setField("from1_os", os == null ? "—" : fmt(os, 4));
      setField(
        "blade_o_out",
        om == null ? "—" : (fmt(toDisplay(om, "len"), 5) + " " + unitLabel("len"))
      );
      window._lastThroat = th;
      window._lastDomain = data.domain || null;
      window._lastBladePreview = data;
      if (data.domain) {
        setField(
          "domain_x_out",
          "MESH domain x=[" + fmt(data.domain.x_min, 5) + ", " + fmt(data.domain.x_max, 5) +
            "] m · y=[" + fmt(data.domain.y_min, 5) + ", " + fmt(data.domain.y_max, 5) +
            "] m · pitch s=" + fmt(data.pitch_m, 5) + " m · n=" +
            fmt(data.domain.n_blades, 0) + " · inlet " +
            fmt(data.domain.x_up_c, 3) + "c · outlet " + fmt(data.domain.x_dn_c, 3) + "c"
        );
      }
    })
    .catch(function (e) {
      setServerBanner(false, String(e.message || e));
    });
}

function fillBladeEngReadout(data, ml) {
  var el = document.getElementById("blade_eng_readout");
  if (!el) return;
  var dom = data.domain || {};
  var th = data.throat || {};
  var inn = data.inlet_flow || {};
  var st = data.stage || {};
  var mp = data.mesh_parity || {};
  var pitch = data.pitch_m != null ? data.pitch_m : (data.chord_m / Math.max(data.solidity || 1, 1e-9));
  var lines = [];
  lines.push(
    "<b>✓ Mesh parity:</b> " + (mp.same_as_blockMesh
      ? "this purple box is the same domain as §3 blockMeshDict / §4 mesh+solve."
      : "check domain knobs")
  );
  lines.push(
    "<b>Inlet working fluid (left patch):</b> |W₁|=" +
      (inn.w1_m_s != null ? fmt(toDisplay(inn.w1_m_s, "vel"), 3) + " " + unitLabel("vel") : "—") +
      " · flow β₁=" + fmt(inn.beta1_deg != null ? inn.beta1_deg : data.flow_beta1_deg, 2) + "°" +
      (inn.mach_w1 != null ? " · M<sub>W1</sub>=" + fmt(inn.mach_w1, 3) : "") +
      (inn.p1_pa != null ? " · p₁=" + fmt(toDisplay(inn.p1_pa, "press"), 3) + " " + unitLabel("press") : "") +
      (inn.t1_k != null ? " · T₁=" + fmt(toDisplay(inn.t1_k, "temp"), 0) + " " + unitLabel("temp") : "")
  );
  lines.push(
    "<b>Blade spacing:</b> s=" + fmt(toDisplay(pitch, "len"), 5) + " " + unitLabel("len") +
      " · σ=c/s=" + fmt(data.solidity != null ? data.solidity : (data.chord_m / pitch), 4) +
      " · c=" + fmt(toDisplay(data.chord_m, "len"), 4) + " " + unitLabel("len") +
      " · domain passages n=" + fmt(dom.n_blades || 0, 0) +
      (st.n_blades_machine ? " · full wheel Z=" + st.n_blades_machine : "")
  );
  lines.push(
    "<b>Metal / passage:</b> β₁*=" + fmt(data.beta1_deg, 2) + "° · β₂*=" + fmt(data.beta2_deg, 2) +
      "° · stagger=" + fmt(data.stagger_deg, 2) + "° · throat o=" +
      (th.throat_o_m != null ? fmt(toDisplay(th.throat_o_m, "len"), 5) + " " + unitLabel("len") : "—") +
      " · o/s=" + (th.opening_o_s != null ? fmt(th.opening_o_s, 4) : "—") +
      " · t/c=" + fmt((data.shape && data.shape.thickness_ratio) || 0, 3)
  );
  lines.push(
    "<b>Domain (meshed):</b> x∈[" + fmt(dom.x_min, 5) + ", " + fmt(dom.x_max, 5) + "] m" +
      " · y∈[" + fmt(dom.y_min, 5) + ", " + fmt(dom.y_max, 5) + "] m" +
      " · L_in=" + fmt(toDisplay(dom.inlet_length_m, "len"), 4) + " " + unitLabel("len") +
      " · L_out=" + fmt(toDisplay(dom.outlet_length_m, "len"), 4) + " " + unitLabel("len") +
      " · y-span=" + fmt(toDisplay(dom.y_span_m, "len"), 4) + " " + unitLabel("len")
  );
  if (st.mean_radius_m != null || (ml && ml.r_m)) {
    var rm = st.mean_radius_m != null ? st.mean_radius_m : ml.r_m;
    var sp = st.span_m != null ? st.span_m : (ml && ml.span);
    lines.push(
      "<b>Stage:</b> r_m=" + fmt(toDisplay(rm, "len"), 4) + " " + unitLabel("len") +
        (sp != null ? " · span h=" + fmt(toDisplay(sp, "len"), 4) + " " + unitLabel("len") : "") +
        (st.tip_radius_m != null ? " · tip=" + fmt(toDisplay(st.tip_radius_m, "len"), 4) : "") +
        (st.hub_radius_m != null ? " · hub=" + fmt(toDisplay(st.hub_radius_m, "len"), 4) : "") +
        (ml && ml.U != null ? " · U=" + fmt(toDisplay(ml.U, "vel"), 1) + " " + unitLabel("vel") : "")
    );
  }
  el.innerHTML = lines.join("<br>");
}

function drawBladeSvg(data) {
  var svg = document.getElementById("blade_svg");
  if (!svg) return;
  var W = 780, H = 400, pad = 48;
  var all = [];
  // Empty cascade [] is truthy in JS — must check length (was hiding all blades)
  var cascade = (data.cascade && data.cascade.length)
    ? data.cascade
    : (data.profile && data.profile.length ? [data.profile] : []);
  cascade.forEach(function (blade) {
    if (!blade || !blade.length) return;
    blade.forEach(function (p) {
      if (p && (p.x != null) && (p.y != null) && isFinite(p.x) && isFinite(p.y)) all.push(p);
    });
  });
  if (!all.length) {
    svg.innerHTML =
      '<rect width="780" height="400" fill="#f4f0fa"/>' +
      '<text x="24" y="40" font-size="14" fill="#a00">No blade points in preview — click Apply design or check server /api/blade_preview</text>';
    return;
  }
  var dom = data.domain || null;
  if (dom) {
    all.push({ x: dom.x_min, y: dom.y_min });
    all.push({ x: dom.x_max, y: dom.y_max });
    all.push({ x: dom.x_min, y: dom.y_max });
    all.push({ x: dom.x_max, y: dom.y_min });
  }
  var xs = all.map(function (p) { return p.x; });
  var ys = all.map(function (p) { return p.y; });
  var xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
  var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
  // pad view slightly so labels fit
  var mx = 0.04 * (xmax - xmin + 1e-9);
  var my = 0.06 * (ymax - ymin + 1e-9);
  xmin -= mx; xmax += mx; ymin -= my; ymax += my;
  var dx = Math.max(xmax - xmin, 1e-9), dy = Math.max(ymax - ymin, 1e-9);
  var s = Math.min((W - 2 * pad) / dx, (H - 2 * pad) / dy);
  function X(x) { return pad + (x - xmin) * s; }
  function Y(y) { return H - pad - (y - ymin) * s; }
  function polyPath(pts) {
    if (!pts || !pts.length) return "";
    var d = "M " + X(pts[0].x) + " " + Y(pts[0].y);
    for (var i = 1; i < pts.length; i++) d += " L " + X(pts[i].x) + " " + Y(pts[i].y);
    return d;
  }
  function arrow(x0, y0, x1, y1, color, sw) {
    var ang = Math.atan2(Y(y1) - Y(y0), X(x1) - X(x0));
    var ah = 8;
    var xh = X(x1), yh = Y(y1);
    return (
      '<line x1="' + X(x0).toFixed(1) + '" y1="' + Y(y0).toFixed(1) +
      '" x2="' + xh.toFixed(1) + '" y2="' + yh.toFixed(1) +
      '" stroke="' + color + '" stroke-width="' + (sw || 1.6) + '" marker-end="url(#arr)"/>' +
      // simple chevron head without defs dependency
      '<path d="M ' + xh.toFixed(1) + " " + yh.toFixed(1) +
      " L " + (xh - ah * Math.cos(ang - 0.4)).toFixed(1) + " " + (yh - ah * Math.sin(ang - 0.4)).toFixed(1) +
      " L " + (xh - ah * Math.cos(ang + 0.4)).toFixed(1) + " " + (yh - ah * Math.sin(ang + 0.4)).toFixed(1) +
      ' Z" fill="' + color + '"/>'
    );
  }
  var parts = [];
  parts.push('<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#f4f0fa"/>');
  var title =
    "Cascade preview ≡ mesh · c=" + fmt(data.chord_m, 4) + " m · s=" +
    fmt(data.pitch_m, 5) + " m · σ=" + fmt(data.solidity, 3) +
    " · metal β₁*/β₂*=" + fmt(data.beta1_deg, 2) + "°/" + fmt(data.beta2_deg, 2) + "°";
  parts.push('<text x="12" y="16" font-size="13" font-family="Times New Roman, serif" font-weight="bold">' +
    title + "</text>");

  // Flow domain rectangle (behind blades)
  if (dom) {
    var rx = X(dom.x_min), ry = Y(dom.y_max);
    var rw = X(dom.x_max) - X(dom.x_min);
    var rh = Y(dom.y_min) - Y(dom.y_max);
    // light fill for interior (gas path)
    parts.push(
      '<rect x="' + rx.toFixed(1) + '" y="' + ry.toFixed(1) +
      '" width="' + rw.toFixed(1) + '" height="' + rh.toFixed(1) +
      '" fill="rgba(100,160,220,0.08)" stroke="#6a3a9a" stroke-width="2" stroke-dasharray="7 4"/>'
    );
    // Solid inlet plane (working-fluid face)
    parts.push(
      '<line x1="' + rx.toFixed(1) + '" y1="' + ry.toFixed(1) +
      '" x2="' + rx.toFixed(1) + '" y2="' + (ry + rh).toFixed(1) +
      '" stroke="#1a7a3a" stroke-width="3.5"/>'
    );
    // Outlet plane
    parts.push(
      '<line x1="' + (rx + rw).toFixed(1) + '" y1="' + ry.toFixed(1) +
      '" x2="' + (rx + rw).toFixed(1) + '" y2="' + (ry + rh).toFixed(1) +
      '" stroke="#8a3a20" stroke-width="2.5"/>'
    );
    parts.push(
      '<text x="' + (rx + 10).toFixed(1) + '" y="' + (ry + 14).toFixed(1) +
      '" font-size="11" fill="#1a7a3a" font-family="Times New Roman, serif" font-weight="bold">' +
      "INLET · working fluid enters here</text>"
    );
    parts.push(
      '<text x="' + (rx + rw - 8).toFixed(1) + '" y="' + (ry + 14).toFixed(1) +
      '" font-size="11" fill="#8a3a20" font-family="Times New Roman, serif" text-anchor="end" font-weight="bold">' +
      "OUTLET</text>"
    );
    parts.push(
      '<text x="' + (rx + rw / 2).toFixed(1) + '" y="' + (ry + 12).toFixed(1) +
      '" font-size="10" fill="#5a2a8a" font-family="Times New Roman, serif" text-anchor="middle">cyclic · pitch period</text>'
    );
    parts.push(
      '<text x="' + (rx + rw / 2).toFixed(1) + '" y="' + (ry + rh - 6).toFixed(1) +
      '" font-size="10" fill="#5a2a8a" font-family="Times New Roman, serif" text-anchor="middle">cyclic · pitch period</text>'
    );
  }

  // cascade blades (use same cascade list as bounds — never skip when profile-only)
  var nB = cascade.length;
  cascade.forEach(function (blade, bi) {
    if (!blade || !blade.length) return;
    var mid = Math.floor((nB - 1) / 2);
    var d = polyPath(blade);
    if (!d) return;
    parts.push('<path d="' + d + ' Z" fill="' + (bi === mid ? "#c8c2b0" : "#e8e4d8") +
      '" stroke="#000" stroke-width="1.8"/>');
  });
  // meanline on center blade frame
  if (data.meanline && data.meanline.length) {
    parts.push('<path d="' + polyPath(data.meanline) + '" fill="none" stroke="#0033aa" stroke-width="1.5" stroke-dasharray="5 3"/>');
  }
  // LE / TE markers
  if (data.profile && data.profile.length > 2 && data.shape && data.meanline && data.meanline.length) {
    var c = data.chord_m || 0.01;
    var le = (data.shape.le_fillet_r_c || 0.02) * c;
    var te = (data.shape.te_fillet_r_c || 0.01) * c;
    var mle = data.meanline[0], mte = data.meanline[data.meanline.length - 1];
    parts.push('<circle cx="' + X(mle.x) + '" cy="' + Y(mle.y) + '" r="' + Math.max(le * s, 3) +
      '" fill="none" stroke="#aa0000" stroke-width="1"/>');
    parts.push('<circle cx="' + X(mte.x) + '" cy="' + Y(mte.y) + '" r="' + Math.max(te * s, 2) +
      '" fill="none" stroke="#aa0000" stroke-width="1"/>');
    parts.push('<text x="' + (X(mle.x) - 4) + '" y="' + (Y(mle.y) - 8) +
      '" font-size="10" fill="#aa0000">LE</text>');
    parts.push('<text x="' + (X(mte.x) + 4) + '" y="' + (Y(mte.y) - 8) +
      '" font-size="10" fill="#aa0000">TE</text>');
  }

  // Blade spacing dimensions (orange)
  var pairs = data.spacing_pairs || [];
  pairs.forEach(function (sp, si) {
    var x = sp.x_m != null ? sp.x_m : 0.35 * (data.chord_m || 0.01);
    var y0 = sp.y0, y1 = sp.y1;
    parts.push(
      '<line x1="' + X(x).toFixed(1) + '" y1="' + Y(y0).toFixed(1) +
      '" x2="' + X(x).toFixed(1) + '" y2="' + Y(y1).toFixed(1) +
      '" stroke="#c06000" stroke-width="1.4"/>'
    );
    // end ticks
    parts.push(
      '<line x1="' + (X(x) - 5).toFixed(1) + '" y1="' + Y(y0).toFixed(1) +
      '" x2="' + (X(x) + 5).toFixed(1) + '" y2="' + Y(y0).toFixed(1) +
      '" stroke="#c06000" stroke-width="1.4"/>'
    );
    parts.push(
      '<line x1="' + (X(x) - 5).toFixed(1) + '" y1="' + Y(y1).toFixed(1) +
      '" x2="' + (X(x) + 5).toFixed(1) + '" y2="' + Y(y1).toFixed(1) +
      '" stroke="#c06000" stroke-width="1.4"/>'
    );
    var ym = 0.5 * (y0 + y1);
    parts.push(
      '<text x="' + (X(x) + 8).toFixed(1) + '" y="' + Y(ym).toFixed(1) +
      '" font-size="11" fill="#c06000" font-family="Courier New,monospace" font-weight="bold">' +
      "s=" + fmt(sp.pitch_m != null ? sp.pitch_m : data.pitch_m, 5) + " m</text>"
    );
  });

  // Inlet working-fluid arrows (green) along inlet plane
  var inn = data.inlet_flow || {};
  if (dom && inn.beta1_deg != null) {
    var b1 = (inn.beta1_deg * Math.PI) / 180;
    var arrLen = 0.22 * (data.chord_m || 0.01);
    var nArr = Math.max(3, Math.min(7, (data.cascade || []).length + 2));
    var yLo = dom.y_min + 0.08 * (dom.y_max - dom.y_min);
    var yHi = dom.y_max - 0.08 * (dom.y_max - dom.y_min);
    for (var ai = 0; ai < nArr; ai++) {
      var yy = yLo + (yHi - yLo) * (ai / Math.max(nArr - 1, 1));
      var x0 = dom.x_min + 0.02 * (data.chord_m || 0.01);
      var x1 = x0 + arrLen * Math.cos(b1);
      var y1 = yy + arrLen * Math.sin(b1);
      parts.push(arrow(x0, yy, x1, y1, "#1a7a3a", 1.8));
    }
    var wlab = inn.w1_m_s != null
      ? ("W₁=" + fmt(inn.w1_m_s, 0) + " m/s @ β₁=" + fmt(inn.beta1_deg, 1) + "°")
      : ("flow β₁=" + fmt(inn.beta1_deg, 1) + "°");
    parts.push(
      '<text x="' + (X(dom.x_min) + 18).toFixed(1) + '" y="' + (Y(0.5 * (dom.y_min + dom.y_max)) + 4).toFixed(1) +
      '" font-size="11" fill="#145a2c" font-family="Times New Roman, serif" font-weight="bold">' +
      wlab + "</text>"
    );
  }

  var foot =
    "green = inlet W₁ (working fluid from left) · orange = blade spacing s · purple = meshed domain · blue dashed = meanline";
  if (dom) {
    foot += " · domain " + fmt(dom.x_up_c, 2) + "c / " + fmt(dom.x_dn_c, 2) + "c";
  }
  parts.push('<text x="12" y="' + (H - 10) +
    '" font-size="11" font-family="Times New Roman, serif">' + foot + "</text>");
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.innerHTML = parts.join("");
}

function setStatus(id, text) {
  var el = document.getElementById(id);
  if (el) el.value = text;
}

function generateCase(form) {
  // Always recompute §1 so case matches the current form (pipeline §1→§3)
  form = form || document.forms.casegen;
  if (document.forms.meanline) calcMeanline(document.forms.meanline);
  propagateFromSection2();

  var payload = meanlinePayload(document.forms.meanline);
  if (!payload) {
    if (form) form.case_msg.value = "§1 mean-line invalid — fix gas/angles first.";
    setStatus("case_status", "blocked");
    return Promise.reject(new Error("§1 mean-line invalid"));
  }
  if (!document.forms.bladeform) {
    if (form) form.case_msg.value = "§2 blade form missing.";
    setStatus("case_status", "blocked");
    return Promise.reject(new Error("§2 blade form missing"));
  }
  var ext = getDomainExtents();
  var etRaw = (form.end_time && form.end_time.value) ? String(form.end_time.value).trim() : "";
  var etLower = etRaw.toLowerCase();
  // null (not the string "auto") so the server never float()'s a label
  var endTime =
    etRaw === "" || etLower === "auto" || etLower === "none" || etLower === "default"
      ? null
      : parseFloat(etRaw);
  if (endTime != null && isNaN(endTime)) endTime = null;
  var fid = getFidelitySettings();
  var body = {
    meanline: payload,
    blade_shape: bladeShapeFromForm(document.forms.bladeform),
    n_blades: parseInt(form.n_blades.value, 10),
    nx: parseInt(form.nx.value, 10) || fid.nx,
    ny: parseInt(form.ny.value, 10) || fid.ny,
    end_time: endTime, // null ⇒ server uses fidelity-scaled auto timing
    startup: form.startup_ics ? !!form.startup_ics.checked : true,
    output_dir: form.output_dir.value || "output",
    x_up_c: ext.x_up_c,
    x_dn_c: ext.x_dn_c,
    fidelity_mode: fid.mode,
    fidelity_level: fid.level,
    fidelity: { mode: fid.mode, level: fid.level }
  };
  setStatus("case_status", "working…");
  var fidNote = fid.level >= 75
    ? " [HIGH ACCURACY — fine mesh, long run, no short time limit]"
    : (fid.level >= 35 ? " [balanced fidelity]" : "");
  form.case_msg.value = (body.startup
    ? "Building startup case (quiescent → inlet W₁) from §1–2 + domain…"
    : "Building case from §1 flow + §2 metal + domain…") + fidNote;
  return apiJson("/api/generate_case", {
    method: "POST",
    body: JSON.stringify(body)
  })
    .then(function (data) {
      form.case_dir.value = data.case_dir || "";
      form.case_msg.value = data.message || data.error || "";
      setStatus("case_status", data.success ? "ok" : "failed");
      if (data.success) {
        window._pipeline.meshOk = false;
        window._pipeline.solveOk = false;
        window._pipeline.caseStale = false;
        propagateCaseDir(data.case_dir || "");
        form.case_msg.value = (data.message || "ok") + " — case ready for §4 mesh/solve.";
        if (data.domain) {
          setField(
            "domain_x_out",
            "x=[" + fmt(data.domain.x_min, 5) + ", " + fmt(data.domain.x_max, 5) + "] m · y=[" +
              fmt(data.domain.y_min, 5) + ", " + fmt(data.domain.y_max, 5) + "] m · inlet " +
              fmt(data.domain.x_up_c, 3) + "c · outlet " + fmt(data.domain.x_dn_c, 3) + "c"
          );
        }
      }
      refreshWorkflow(data.case_dir || "");
      return data;
    })
    .catch(function (e) {
      form.case_msg.value = String(e.message || e);
      setStatus("case_status", "error");
      setServerBanner(false, String(e.message || e));
      throw e;
    });
}

function requireCaseDir(form, actionLabel) {
  var dir = (form && form.case_dir && form.case_dir.value) || window._pipeline.caseDir || "";
  if (!dir) {
    alert((actionLabel || "This step") + " needs a case from §3. Build case files first.");
    return null;
  }
  if (window._pipeline.caseStale) {
    var go = confirm(
      "§1 or §2 changed after this case was built.\n\n" +
      "Continue with the old case, or cancel and rebuild §3?"
    );
    if (!go) return null;
  }
  if (form && form.case_dir) form.case_dir.value = dir;
  return dir;
}

/**
 * Mesh → solve → sample. Returns a Promise.
 * opts.skipDesignReport — don't auto-load §5 report
 * opts.stabilizeLayout — dim fix list in place during re-analyze (no page jump)
 * opts.use_openfoam_sample — prefer CFD sample for final design report (default true)
 */
function runFullTest(form, opts) {
  opts = opts || {};
  form = form || document.forms.mesh;
  var caseDir = requireCaseDir(form, "Full CFD test");
  if (!caseDir) return Promise.reject(new Error("No case dir for full CFD test"));

  // Mesh → solve → sample (async jobs + poll — no long-held HTTP for solve)
  form.wf_label.value = "Full test: meshing §3 case…";
  setStatus("mesh_status", "working…");
  setStatus("solve_status", "…");
  return apiJob(
    "/api/mesh",
    { case_dir: caseDir },
    function (j) {
      if (j && (j.status === "running" || j.heartbeat)) {
        form.wf_label.value =
          "Full test: MESH · " + (j.heartbeat || j.message || "running…");
        form.mesh_msg.value =
          (j.heartbeat || j.message || "meshing…") +
          " · elapsed " + formatDuration(j.elapsed_s) +
          " · ETA " + formatDuration(j.eta_s);
        setStatus("mesh_status", "working " + formatDuration(j.elapsed_s));
      }
    },
    { kind: "mesh" }
  )
    .then(function (data) {
      form.mesh_msg.value = data.message || "";
      form.mesh_ok.value = String(!!data.success);
      setStatus("mesh_status", data.success ? "ok" : "failed");
      window._pipeline.meshOk = !!data.success;
      if (!data.success) {
        form.wf_label.value = "Full test stopped: mesh failed — " + (data.message || "");
        updatePipelineBanner();
        throw new Error("mesh failed: " + (data.message || "unknown"));
      }
      form.wf_label.value = "Full test: solving flow (shockFluid)…";
      setStatus("solve_status", "working…");
      return apiJob(
        "/api/solve",
        { case_dir: caseDir },
        function (j) {
          if (j && (j.status === "running" || j.heartbeat)) {
            form.wf_label.value =
              "Full test: SOLVE · " + (j.heartbeat || j.message || "running…");
            form.solve_msg.value =
              (j.heartbeat || j.message || "solving…") +
              " · elapsed " + formatDuration(j.elapsed_s) +
              " · ETA " + formatDuration(j.eta_s);
            setStatus("solve_status", "working " + formatDuration(j.elapsed_s));
          }
        },
        { kind: "solve" }
      );
    })
    .then(function (data) {
      form.solve_msg.value = data.message || "";
      form.solve_ok.value = String(!!data.success);
      setStatus("solve_status", data.success ? "ok" : "failed");
      window._pipeline.solveOk = !!data.success;
      if (!data.success) {
        form.wf_label.value = "Full test stopped: solve failed — " + (data.message || "");
        updatePipelineBanner();
        throw new Error("solve failed: " + (data.message || "unknown"));
      }
      form.wf_label.value = "Full test: sampling surface pressure…";
      return apiJson("/api/sample", {
        method: "POST",
        body: JSON.stringify({ case_dir: caseDir })
      });
    })
    .then(function (data) {
      form.sample_msg.value = data.message || JSON.stringify(data);
      form.wf_label.value = data.success === false
        ? ("Full test: sample issue — " + (data.message || ""))
        : "Full test complete — open §5 surface pressure, then §6 shock/loss map";
      propagateCaseDir(caseDir);
      updatePipelineBanner();
      refreshWorkflow(caseDir);
      if (opts.skipDesignReport) return data;
      return loadDesignReport({
        use_openfoam_sample: opts.use_openfoam_sample !== false,
        stabilizeLayout: !!opts.stabilizeLayout
      }).then(function () { return data; });
    })
    .catch(function (e) {
      form.wf_label.value = "Full test error: " + String(e.message || e);
      setServerBanner(false, String(e.message || e));
      throw e;
    });
}

function runMesh(form) {
  var caseDir = requireCaseDir(form, "Mesh");
  if (!caseDir) return;
  setStatus("mesh_status", "working…");
  apiJob(
    "/api/mesh",
    { case_dir: caseDir },
    function (j) {
      if (j && (j.status === "running" || j.heartbeat)) {
        form.mesh_msg.value =
          (j.heartbeat || j.message || "meshing…") +
          " · elapsed " + formatDuration(j.elapsed_s) +
          " · ETA " + formatDuration(j.eta_s);
        setStatus("mesh_status", "working " + formatDuration(j.elapsed_s));
      }
    },
    { kind: "mesh" }
  )
    .then(function (data) {
      form.mesh_msg.value = data.message || JSON.stringify(data.notes || data);
      form.mesh_ok.value = String(!!data.success);
      setStatus("mesh_status", data.success ? "ok" : "failed");
      window._pipeline.meshOk = !!data.success;
      updatePipelineBanner();
      refreshWorkflow(caseDir);
    })
    .catch(function (e) {
      form.mesh_msg.value = String(e.message || e);
      setStatus("mesh_status", "error");
      setServerBanner(false, String(e.message || e));
    });
}

function runSolve(form) {
  var caseDir = requireCaseDir(form, "Flow solve");
  if (!caseDir) return;
  setStatus("solve_status", "working…");
  form.solve_msg.value = "Starting background solve (shockFluid)…";
  apiJob(
    "/api/solve",
    { case_dir: caseDir },
    function (j) {
      if (j && (j.status === "running" || j.heartbeat)) {
        form.solve_msg.value =
          (j.heartbeat || j.message || "solving…") +
          " · elapsed " + formatDuration(j.elapsed_s) +
          " · ETA " + formatDuration(j.eta_s);
        setStatus("solve_status", "working " + formatDuration(j.elapsed_s));
      }
    },
    { kind: "solve" }
  )
    .then(function (data) {
      form.solve_msg.value = data.message || "";
      form.solve_ok.value = String(!!data.success);
      setStatus("solve_status", data.success ? "ok" : "failed");
      window._pipeline.solveOk = !!data.success;
      updatePipelineBanner();
      refreshWorkflow(caseDir);
    })
    .catch(function (e) {
      form.solve_msg.value = String(e.message || e);
      setStatus("solve_status", "error");
      setServerBanner(false, String(e.message || e));
    });
}

function runSample(form) {
  var caseDir = requireCaseDir(form, "Sample");
  if (!caseDir) return;
  apiJson("/api/sample", {
    method: "POST",
    body: JSON.stringify({ case_dir: caseDir })
  })
    .then(function (data) {
      form.sample_msg.value = data.message || JSON.stringify(data);
      refreshWorkflow(caseDir);
    })
    .catch(function (e) {
      form.sample_msg.value = String(e.message || e);
      setServerBanner(false, String(e.message || e));
    });
}

function analysisPayload(caseDir, opts) {
  opts = opts || {};
  // Always recompute meanline from live form so re-analyze sees new knobs
  if (document.forms.meanline) calcMeanline(document.forms.meanline);
  var m = window._lastMeanline;
  return {
    case_dir: caseDir || "",
    meanline: meanlinePayload(document.forms.meanline),
    blade_shape: bladeShapeFromForm(document.forms.bladeform),
    p1_pa: m ? m.p1 : 5.5e5,
    rho1: m ? m.rho1 : 1.5,
    w1_m_s: m ? m.W1 : 950,
    chord_m: m ? m.chord : 0.01,
    beta1_deg: m ? m.beta1 : 72,
    beta2_deg: m ? m.beta2 : -72,
    solidity: m ? m.solidity : 1.4,
    mach_w1: m ? m.Mw1 : null,
    // Default true so design-board re-analyze updates with §1–2 even without OF
    force_synthetic: opts.force_synthetic !== false,
    include_plots: opts.include_plots !== false,
    _client_nonce: String(Date.now()) // defeat any intermediary caches
  };
}

function setMetric(id, value, cls) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  var card = el.parentElement;
  if (card && card.classList) {
    card.classList.remove("warn", "bad", "good");
    if (cls) card.classList.add(cls);
  }
}

function fillMetricsBoard(m) {
  if (!m) return;
  window._lastMetrics = m;
  setMetric("m_eta", fmt(m.eta_design_proxy, 4), m.eta_design_proxy >= 0.85 ? "good" : (m.eta_design_proxy < 0.7 ? "bad" : "warn"));
  setMetric("m_eta_ml", fmt(m.eta_meanline_proxy, 4));
  setMetric("m_penalty", fmt(m.surface_loss_penalty, 4), m.surface_loss_penalty > 0.15 ? "bad" : (m.surface_loss_penalty > 0.08 ? "warn" : "good"));
  setMetric("m_mw1", fmt(m.mach_w1, 3));
  // Euler: kJ/kg metric, Btu/lbm imperial
  var eJ = m.euler_work_j_kg || 0;
  setMetric(
    "m_euler",
    isImperial() ? (fmt(toDisplay(eJ, "espec"), 3) + " Btu/lbm") : (fmt(eJ / 1000, 3) + " kJ/kg")
  );
  setMetric("m_psi", fmt(m.stage_loading_psi, 3));
  setMetric("m_load", fmt(m.loading_int_dcp, 4));
  setMetric(
    "m_load_split",
    Math.round(100 * (m.loading_front_frac || 0)) + "/" +
      Math.round(100 * (m.loading_mid_frac || 0)) + "/" +
      Math.round(100 * (m.loading_aft_frac || 0)) + "%"
  );
  setMetric("m_ss_peak", fmt(m.peak_ss_cp, 3), m.peak_ss_cp < -0.8 ? "warn" : "");
  setMetric("m_ss_x", fmt(m.peak_ss_x_c, 3), m.peak_ss_x_c < 0.35 ? "warn" : "good");
  setMetric("m_ss_m", fmt(m.peak_ss_m_isen, 3));
  setMetric("m_df", fmt(m.lieblein_df_ss, 3), m.lieblein_df_ss > 0.6 ? "bad" : (m.lieblein_df_ss > 0.45 ? "warn" : "good"));
  setMetric("m_diff_ss", fmt(m.diffusion_ss, 3), m.diffusion_ss > 0.9 ? "bad" : "");
  setMetric("m_diff_ps", fmt(m.diffusion_ps, 3));
  setMetric("m_le", fmt(m.le_delta_cp, 3), Math.abs(m.le_delta_cp) > 0.45 ? "warn" : "");
  setMetric("m_te", fmt(m.te_delta_cp, 3), m.te_delta_cp > 0.25 ? "warn" : "");
  setMetric("m_nshock", String(m.n_shocks || 0), m.n_shocks > 0 ? "bad" : "good");
  setMetric("m_shock_x", m.strongest_shock_x_c == null ? "—" : fmt(m.strongest_shock_x_c, 3));
  setMetric("m_shock_d", fmt(m.max_shock_dcp, 2));
  setMetric("m_top", m.top_loss_id || "—");
  setMetric("m_tip_m", m.tip_mach_proxy == null ? "—" : fmt(m.tip_mach_proxy, 3),
    m.tip_mach_proxy > 1.2 ? "warn" : "");
  if (m.mass_flow_kg_s != null) {
    setMetric(
      "m_mdot",
      isImperial()
        ? (fmt(toDisplay(m.mass_flow_kg_s, "mdot"), 4) + " lbm/s")
        : (fmt(m.mass_flow_kg_s, 4) + " kg/s")
    );
  }
  if (m.power_w != null) {
    setMetric(
      "m_power",
      isImperial()
        ? (fmt(toDisplay(m.power_w, "power"), 3) + " hp")
        : (m.power_w >= 1000 ? (fmt(m.power_w / 1000, 3) + " kW") : (fmt(m.power_w, 2) + " W"))
    );
  }
  setMetric(
    "m_metal_b",
    fmt(m.metal_beta1_deg != null ? m.metal_beta1_deg : m.beta1_deg, 2) + " / " +
      fmt(m.metal_beta2_deg != null ? m.metal_beta2_deg : m.beta2_deg, 2)
  );
  setMetric("m_os", m.opening_o_s == null ? "—" : fmt(m.opening_o_s, 3),
    m.opening_o_s != null && m.opening_o_s < 0.15 ? "warn" : "");
  setMetric("m_stagger", m.stagger_deg == null ? "—" : fmt(m.stagger_deg, 2) + "°");
  setMetric(
    "m_i_d",
    fmt(m.incidence_deg || 0, 2) + " / " + fmt(m.deviation_deg || 0, 2) + "°"
  );
}

/**
 * Build a minimal advice object from metrics/loss when the server did not send
 * industry_advice (stale process). Enough for the UI to show "you ran a report".
 */
function adviceFallbackFromReport(data) {
  var m = data.metrics || {};
  var loss = data.loss_report || data.report || {};
  var items = [];
  function add(id, name, status, value, limit_text, suggestion, patches) {
    items.push({
      standard_id: id,
      name: name,
      status: status,
      metric: id,
      value: value,
      limit_text: limit_text,
      cite: "See §5a literature list / restart server for full industry_advice payload.",
      why: "",
      suggestion: suggestion,
      patches: patches || [],
      priority: status === "fail" ? 2 : 1
    });
  }
  if (m.lieblein_df_ss != null && m.lieblein_df_ss > 0.6) {
    add("lieblein_df", "Lieblein DF (SS)", "fail", m.lieblein_df_ss, "≤ 0.6",
      "DF high: reduce camber bulge, aft-load thickness peak, raise solidity.",
      [
        { section: "bladeform", field: "bulge", action: "set", value: 0.95, label: "reduce bulge" },
        { section: "bladeform", field: "peak", action: "set", value: 0.52, label: "peak aft" },
        { section: "meanline", field: "solidity", action: "set", value: 1.5, label: "raise σ" }
      ]);
  }
  if (m.n_shocks != null && m.n_shocks > 0) {
    add("n_shocks", "Detected shocks", "fail", m.n_shocks, "≤ 0",
      "Shocks present: lower |W₁|, soften camber, optional inlet line.",
      [
        { section: "meanline", field: "w1", action: "scale", value: 0.92, label: "drop W1 ~8%" },
        { section: "bladeform", field: "bulge", action: "set", value: 1.0, label: "flatten camber" },
        { section: "bladeform", field: "line_in", action: "set", value: 0.08, label: "inlet straight" }
      ]);
  }
  if (m.peak_ss_x_c != null && m.peak_ss_x_c < 0.35) {
    add("ss_peak_location", "SS peak location", "fail", m.peak_ss_x_c, "≥ 0.35",
      "Peak suction too far forward: aft-load section.",
      [
        { section: "bladeform", field: "line_in", action: "set", value: 0.1, label: "inlet line" },
        { section: "bladeform", field: "peak", action: "set", value: 0.5, label: "peak mid-aft" }
      ]);
  }
  if (m.diffusion_ss != null && m.diffusion_ss > 0.9) {
    add("ss_diffusion_cp", "SS diffusion ΔCp", "fail", m.diffusion_ss, "≤ 0.9",
      "Large SS recompression: add exit straight, ease camber.",
      [
        { section: "bladeform", field: "line_out", action: "set", value: 0.1, label: "exit line" },
        { section: "bladeform", field: "bulge", action: "set", value: 0.95, label: "reduce bulge" }
      ]);
  }
  // Always include loss ranked fixes as text-only suggestions if no patches above
  if (!items.length && (loss.ranked_fixes || []).length) {
    (loss.ranked_fixes || []).slice(0, 5).forEach(function (line, i) {
      add("loss_" + i, "Loss fix " + (i + 1), "warn", null, "—", line, []);
    });
  }
  var patches_merged = [];
  var seen = {};
  items.forEach(function (it) {
    (it.patches || []).forEach(function (p) {
      var k = p.section + "." + p.field;
      if (!seen[k]) { seen[k] = true; patches_merged.push(p); }
    });
  });
  return {
    items: items,
    patches_merged: patches_merged,
    summary: items.length
      ? ("Report loaded · " + items.length + " issue(s) from metrics (client fallback).")
      : "Report loaded · no hard gate failures from available metrics.",
    sources: [
      "Lieblein DF ≤ 0.6; de Haller ≳ 0.72; Hill & Peterson §3.7 shock p0 loss; Zweifel/Dixon solidity."
    ],
    auto_apply_safe: patches_merged.length > 0,
    _fallback: true
  };
}

function _escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _patchBits(patches) {
  return (patches || []).map(function (p) {
    return p.section + "." + p.field + " " + p.action + " " + p.value +
      (p.label ? " (" + p.label + ")" : "");
  }).join("; ");
}

function _renderOpenFixCard(it, idx) {
  var id = "fix_chk_" + (it.standard_id || idx);
  var st = (it.status || "").toUpperCase();
  var isWarn = st === "WARN";
  var badgeClass = isWarn ? "fix-badge-warn" : "fix-badge-fail";
  var sevClass = isWarn ? " sev-warn" : "";
  var patchBits = _patchBits(it.patches);
  var row = document.createElement("div");
  row.className = "fix-card fix-card-open" + sevClass;
  row.setAttribute("data-std-id", String(it.standard_id || ""));
  row.innerHTML =
    "<label class='fix-card-inner' for='" + id + "'>" +
    "<input type='checkbox' class='fix_suggestion_chk' data-std-id='" +
    _escapeHtml(String(it.standard_id || "")) + "' id='" + id + "' checked>" +
    "<span class='fix-card-body'>" +
    "<span class='fix-card-head'>" +
    "<span class='fix-badge " + badgeClass + "'>" + _escapeHtml(st || "OPEN") + "</span>" +
    "<span class='fix-card-name'>" + _escapeHtml(it.name || it.standard_id || "Fix") + "</span>" +
    "</span>" +
    "<div class='fix-card-metrics'>value " +
    (it.value == null ? "—" : fmt(it.value, 4)) +
    " · limit " + _escapeHtml(it.limit_text || "—") + "</div>" +
    "<p class='fix-card-suggestion'>" + _escapeHtml(it.suggestion || "") + "</p>" +
    (patchBits
      ? "<p class='fix-card-knobs'>Knobs · " + _escapeHtml(patchBits) + "</p>"
      : "") +
    "</span></label>";
  return row;
}

function _renderReadonlyFixCard(it) {
  var st = (it.status || "").toUpperCase();
  var row = document.createElement("div");
  row.className = "fix-card fix-card-readonly";
  row.innerHTML =
    "<div class='fix-card-inner' style='cursor:default;'>" +
    "<span class='fix-card-body'>" +
    "<span class='fix-card-head'>" +
    "<span class='fix-badge fix-badge-info'>" + _escapeHtml(st || "NOTE") + "</span>" +
    "<span class='fix-card-name'>" + _escapeHtml(it.name || "") + "</span>" +
    "</span>" +
    "<p class='fix-card-suggestion'>" + _escapeHtml(it.suggestion || "") + "</p>" +
    "<p class='fix-card-knobs'>Advisory only — no auto-knob patches for this gate.</p>" +
    "</span></div>";
  return row;
}

function _paintFixStatusBar(actionableN, passN, busyMsg, fallback) {
  var bar = document.getElementById("fix_status_bar");
  if (!bar) return;
  if (busyMsg) {
    bar.innerHTML =
      "<span class='fix-pill fix-pill-busy'>" + _escapeHtml(busyMsg) + "</span>";
    return;
  }
  var bits = [];
  if (actionableN > 0) {
    bits.push(
      "<span class='fix-pill fix-pill-open'>" + actionableN + " open</span>"
    );
  }
  if (passN > 0) {
    bits.push(
      "<span class='fix-pill fix-pill-pass'>" + passN + " passing</span>"
    );
  }
  if (!actionableN && passN >= 0) {
    bits.push("<span class='fix-pill fix-pill-pass'>gates clear</span>");
  }
  if (fallback) {
    bits.push(
      "<span class='fix-pill fix-pill-info'>client fallback · restart server for full standards</span>"
    );
  }
  bar.innerHTML = bits.join("");
}

/** Keep the fix panel under the viewport focus when its height shrinks (no empty padding). */
function _anchorFixBoxScroll() {
  var fixBox = document.getElementById("fix_box");
  if (!fixBox) return null;
  return fixBox.getBoundingClientRect().top;
}

function _restoreFixBoxScroll(prevTop) {
  if (prevTop == null) return;
  var fixBox = document.getElementById("fix_box");
  if (!fixBox) return;
  var newTop = fixBox.getBoundingClientRect().top;
  var dy = newTop - prevTop;
  if (Math.abs(dy) > 0.5 && typeof window.scrollBy === "function") {
    window.scrollBy(0, dy);
  }
}

/**
 * Paint suggestion list with only *current* open issues.
 * Applied fixes are removed (no ghost cards, no reserved dead space).
 * Scroll is re-anchored so the panel does not jump when the list shortens.
 */
function fillIndustryAdvice(advice, reportMeta) {
  reportMeta = reportMeta || {};
  var sum = document.getElementById("advice_summary");
  var list = document.getElementById("suggestion_list");
  var citeEl = document.getElementById("industry_citations");
  var btn = document.getElementById("btn_auto_redesign");
  var delta = document.getElementById("delta_box");
  var fixBox = document.getElementById("fix_box");
  var scrollAnchor = _anchorFixBoxScroll();

  // Never leave the box stuck on "build first" after a successful report
  if (!advice || !advice.items) {
    if (reportMeta.hasMetrics) {
      advice = adviceFallbackFromReport(reportMeta.data || {});
    } else {
      window._lastIndustryAdvice = null;
      if (sum) {
        sum.textContent = "build a design report first";
        sum.style.color = "";
        sum.style.fontWeight = "";
      }
      _paintFixStatusBar(0, 0, null, false);
      if (list) {
        list.classList.remove("fix-list-busy");
        list.style.minHeight = "";
        list.style.height = "";
        list.innerHTML =
          "<div class='fix-empty'>" +
          "<strong>No analysis yet</strong>" +
          "Run <b>Build full design report</b> to populate industry pass/fail cards." +
          "</div>";
      }
      if (btn) {
        btn.disabled = true;
        btn.value = "Apply selected & re-analyze";
      }
      _restoreFixBoxScroll(scrollAnchor);
      return;
    }
  }

  window._lastIndustryAdvice = advice;
  if (sum) {
    sum.textContent = advice.summary
      ? "report ready · " + advice.summary
      : "report ready · suggestions updated";
    sum.style.color = "#1a5030";
    sum.style.fontWeight = "600";
  }
  if (fixBox) fixBox.style.outline = "";
  if (delta && !delta.dataset.keep) delta.textContent = "";

  var actionable = (advice.items || []).filter(function (it) {
    return it.status !== "pass" && (it.patches || []).length > 0;
  });
  var readonlyIssues = (advice.items || []).filter(function (it) {
    return it.status !== "pass" && !(it.patches || []).length;
  });
  var passes = (advice.items || []).filter(function (it) {
    return it.status === "pass";
  });

  _paintFixStatusBar(
    actionable.length + readonlyIssues.length,
    passes.length,
    null,
    !!advice._fallback
  );

  if (list) {
    list.classList.remove("fix-list-busy");
    list.style.minHeight = "";
    list.style.height = "";
    list.innerHTML = "";

    if (!actionable.length && !readonlyIssues.length) {
      var allOk = document.createElement("div");
      allOk.className = "fix-empty";
      allOk.innerHTML =
        "<strong>All industry gates pass</strong>" +
        "Nothing required. Tweak §1–2 knobs anytime and re-analyze to re-check.";
      list.appendChild(allOk);
    }

    actionable.forEach(function (it, idx) {
      list.appendChild(_renderOpenFixCard(it, idx));
    });

    readonlyIssues.forEach(function (it) {
      list.appendChild(_renderReadonlyFixCard(it));
    });

    if (passes.length) {
      var ok = document.createElement("details");
      ok.className = "fix-passes";
      ok.open = passes.length <= 4;
      var sumEl = document.createElement("summary");
      sumEl.textContent = passes.length + " gate" + (passes.length === 1 ? "" : "s") + " passing";
      ok.appendChild(sumEl);
      var ul = document.createElement("ul");
      passes.forEach(function (p) {
        var li = document.createElement("li");
        li.textContent =
          (p.name || p.standard_id || "gate") +
          " · " +
          (p.value == null ? "—" : fmt(p.value, 3)) +
          (p.limit_text ? "  " + p.limit_text : "");
        ul.appendChild(li);
      });
      ok.appendChild(ul);
      list.appendChild(ok);
    }
  }

  if (citeEl) {
    citeEl.innerHTML = (advice.sources || []).map(function (s, i) {
      return (i + 1) + ". " + s;
    }).join("<br>") || "See design_advisor STANDARDS in the codebase.";
  }
  if (btn) {
    btn.disabled = actionable.length === 0;
    btn.value = actionable.length
      ? ("Apply selected & re-analyze (" + actionable.length + ")")
      : "Apply selected & re-analyze";
  }
  // Anchor after layout so shrinking the list does not shove the panel down
  requestAnimationFrame(function () {
    _restoreFixBoxScroll(scrollAnchor);
  });
}

/**
 * Soft busy state during re-analyze: dim list + status pill (no height lock / dead space).
 * fillIndustryAdvice replaces cards when done; applied items are simply gone.
 */
function showSuggestionListWorking(message) {
  var list = document.getElementById("suggestion_list");
  if (list) {
    list.classList.add("fix-list-busy");
    list.style.minHeight = "";
    list.style.height = "";
  }
  _paintFixStatusBar(0, 0, message || "Re-analyzing…", false);
  var sum = document.getElementById("advice_summary");
  if (sum) {
    sum.textContent = message || "Re-analyzing…";
    sum.style.color = "#3a2860";
  }
}

function setAllSuggestionChecks(on) {
  var boxes = document.querySelectorAll(".fix_suggestion_chk");
  for (var i = 0; i < boxes.length; i++) {
    if (!boxes[i].disabled) boxes[i].checked = !!on;
  }
}

function getSelectedSuggestionItems() {
  var advice = window._lastIndustryAdvice;
  if (!advice || !advice.items) return [];
  var selected = {};
  var boxes = document.querySelectorAll(".fix_suggestion_chk");
  for (var i = 0; i < boxes.length; i++) {
    if (boxes[i].checked && !boxes[i].disabled) {
      selected[boxes[i].getAttribute("data-std-id")] = true;
    }
  }
  return advice.items.filter(function (it) {
    return selected[it.standard_id] && it.status !== "pass" && (it.patches || []).length;
  });
}

/** Apply a DesignPatch to the live §1 / §2 forms (silent — no scroll). */
function applyOnePatch(p) {
  var form = p.section === "bladeform" ? document.forms.bladeform : document.forms.meanline;
  if (!form) return false;
  var fieldMap = {
    beta1: "beta1", beta2: "beta2", w1: "w1", U: "U", solidity: "solidity",
    thk: "thk", peak: "peak", bulge: "bulge",
    line_in: "line_in", line_out: "line_out", le: "le", te: "te",
    incidence: "incidence", deviation: "deviation", rpm: "rpm",
    r_m: "r_m", span: "span", mdot: "mdot", power: "power",
    camber_dist: "camber_dist", te_thk: "te_thk", te_wedge: "te_wedge",
    stagger: "stagger", le_shape: "le_shape"
  };
  var fname = fieldMap[p.field] || p.field;
  var ctrl = form[fname];
  var ctrlV = form[fname + "_v"];
  if (!ctrl && !ctrlV) return false;

  // Select / string fields (e.g. le_shape)
  if (ctrl && ctrl.tagName === "SELECT") {
    if (p.action === "set") ctrl.value = String(p.value);
    return true;
  }

  function readNum(el) { return parseFloat(el.value); }
  function writeNum(el, v) {
    if (!el) return;
    var s = (Math.abs(v) >= 10) ? v.toFixed(2) : (Math.abs(v) >= 1 ? v.toFixed(3) : v.toFixed(4));
    el.value = s;
  }

  var cur = readNum(ctrlV || ctrl);
  if (isNaN(cur)) cur = 0;
  var next = cur;
  if (p.action === "set") next = Number(p.value);
  else if (p.action === "delta") next = cur + Number(p.value);
  else if (p.action === "scale") next = cur * Number(p.value);

  if (fname === "bulge") next = Math.min(1.8, Math.max(0.3, next));
  if (fname === "thk") next = Math.min(0.55, Math.max(0.04, next));
  if (fname === "peak") next = Math.min(0.7, Math.max(0.2, next));
  if (fname === "line_in" || fname === "line_out") next = Math.min(0.4, Math.max(0, next));
  if (fname === "solidity") next = Math.min(2.5, Math.max(0.6, next));
  if (fname === "w1") next = Math.max(100, next);
  if (fname === "U") next = Math.max(50, next);
  if (fname === "beta1") next = Math.min(85, Math.max(20, next));
  if (fname === "camber_dist") next = Math.min(1, Math.max(0, next));
  if (fname === "te_thk") next = Math.min(0.04, Math.max(0.001, next));
  if (fname === "te_wedge") next = Math.min(30, Math.max(0, next));
  if (fname === "incidence" || fname === "deviation") next = Math.min(15, Math.max(-15, next));
  if (fname === "rpm") next = Math.max(0, next);

  writeNum(ctrl, next);
  writeNum(ctrlV, next);
  return true;
}

/** Live §1–2 knob fingerprint — used to detect when patches no longer move the design. */
function _knobSignature() {
  var parts = [];
  function read(form, keys) {
    if (!form) return;
    keys.forEach(function (k) {
      var el = form[k + "_v"] || form[k];
      if (el) parts.push(k + "=" + String(el.value));
    });
  }
  read(document.forms.meanline, [
    "beta1", "beta2", "w1", "U", "solidity", "incidence", "deviation",
    "r_m", "rpm", "span", "mdot", "power"
  ]);
  read(document.forms.bladeform, [
    "thk", "peak", "bulge", "line_in", "line_out", "le", "te",
    "camber_dist", "te_thk", "te_wedge", "stagger", "le_shape"
  ]);
  return parts.join("|");
}

function countOpenActionableFixes() {
  var advice = window._lastIndustryAdvice;
  if (!advice || !advice.items) return 0;
  var n = 0;
  (advice.items || []).forEach(function (it) {
    if (it.status !== "pass" && (it.patches || []).length > 0) n++;
  });
  return n;
}

function _setAutoUiRunning(running) {
  var btnApply = document.getElementById("btn_auto_redesign");
  var btnAuto = document.getElementById("btn_auto_converge");
  var btnStop = document.getElementById("btn_auto_converge_stop");
  if (btnApply) btnApply.disabled = !!running;
  if (btnAuto) btnAuto.disabled = !!running;
  if (btnStop) btnStop.disabled = !running;
}

function stopAutoConverge() {
  if (!window._autoConverge) window._autoConverge = {};
  window._autoConverge.cancel = true;
  var status = document.getElementById("auto_rerun_status");
  if (status) status.textContent = "Stop requested — finishing current step…";
}

/**
 * Apply only checked industry suggestions → update §1–2 knobs → re-analyze in place.
 * Returns a Promise resolving to { applied, items, data, skipped?, stuck? }.
 * opts.selectAll — check every open fix first (used by auto-iterate)
 * opts.fromAuto — quieter UI (no scroll), status prefix
 * opts.roundLabel — e.g. "3/8"
 */
function applySelectedFixesAndReanalyze(opts) {
  opts = opts || {};
  if (opts.selectAll) setAllSuggestionChecks(true);

  var items = getSelectedSuggestionItems();
  var status = document.getElementById("auto_rerun_status");
  var delta = document.getElementById("delta_box");
  var prefix = opts.roundLabel ? ("[" + opts.roundLabel + "] ") : "";
  function setSt(t) { if (status) status.textContent = prefix + t; }

  if (!items.length) {
    setSt("No fixes selected — tick the ones you want, or all gates already pass.");
    return Promise.resolve({ applied: [], items: [], data: null, skipped: true });
  }

  // Merge patches from selected items only (first wins per field)
  var merged = {};
  var labels = [];
  items.forEach(function (it) {
    labels.push(it.name || it.standard_id);
    (it.patches || []).forEach(function (p) {
      var key = p.section + "." + p.field;
      if (!merged[key]) merged[key] = p;
    });
  });

  var applied = [];
  Object.keys(merged).forEach(function (k) {
    var p = merged[k];
    if (applyOnePatch(p)) {
      applied.push(k + " " + p.action + "→" + p.value);
    }
  });

  if (document.forms.meanline) {
    toggleBeta2(document.forms.meanline);
    calcMeanline(document.forms.meanline);
  }
  if (document.forms.bladeform) {
    ["thk", "peak", "line_in", "line_out", "bulge", "le", "te", "camber_dist", "te_thk", "te_wedge"].forEach(function (k) {
      var f = document.forms.bladeform;
      if (f[k] && f[k + "_v"]) f[k].value = f[k + "_v"].value;
    });
    // silent blade refresh (preview only; do not force scroll)
    if (typeof previewBlade === "function") previewBlade();
  }

  if (!applied.length) {
    setSt("Could not write knobs — form fields missing or already at limits.");
    return Promise.resolve({ applied: [], items: items, data: null, stuck: true });
  }

  setSt("Applied " + applied.length + " knob change(s) for: " + labels.join("; ") + ". Re-analyzing…");
  if (delta && !opts.fromAuto) {
    delta.textContent = "Applying:\n  " + applied.join("\n  ");
    delta.dataset.keep = "1";
  }

  // Stay on §5 — re-run design report; stabilize layout so the page does not jump.
  // Applied fixes drop off when the new analysis paints (only remaining open issues).
  return loadDesignReport({ use_openfoam_sample: false, stabilizeLayout: true })
    .then(function (data) {
      var msg = document.getElementById("report_summary_line");
      var dline = (msg && msg.textContent) || "";
      setSt("Done. Metrics updated. " + (dline.indexOf("Δ") >= 0 ? dline.split("|").pop().trim() : ""));
      if (delta && !opts.fromAuto) {
        delta.textContent =
          "Applied knobs:\n  " + applied.join("\n  ") +
          "\n\nRe-analyzed — applied items are cleared; only remaining open fixes are listed.\n" +
          (dline || "Report refreshed.");
        delete delta.dataset.keep;
      }
      // Do not scrollIntoView — that was the annoying jump on multi re-analyze
      return { applied: applied, items: items, data: data, labels: labels };
    })
    .catch(function (e) {
      setSt("Re-analyze failed: " + String(e.message || e));
      throw e;
    });
}

// Back-compat name used by older markup
function applyIndustryPatchesAndRerun() {
  applySelectedFixesAndReanalyze();
}

/**
 * Auto-iterate: repeatedly apply all open industry fixes + synthetic re-analyze
 * until gates settle (or knobs stop moving / max rounds), then rebuild §3 case
 * and run full OpenFOAM mesh → solve → sample, refreshing the design board from CFD.
 */
function runAutoConvergeAndOpenFOAM() {
  if (window._autoConverge && window._autoConverge.running) {
    return Promise.resolve();
  }

  var status = document.getElementById("auto_rerun_status");
  var delta = document.getElementById("delta_box");
  var roundsEl = document.getElementById("auto_max_rounds");
  var maxRounds = roundsEl ? parseInt(roundsEl.value, 10) : 8;
  if (isNaN(maxRounds) || maxRounds < 1) maxRounds = 8;
  if (maxRounds > 20) maxRounds = 20;

  window._autoConverge = { running: true, cancel: false };
  _setAutoUiRunning(true);

  var logLines = [];
  function setSt(t) { if (status) status.textContent = t; }
  function log(line) {
    logLines.push(line);
    if (delta) {
      delta.textContent = logLines.join("\n");
      delta.dataset.keep = "1";
    }
  }
  function cancelled() {
    return !!(window._autoConverge && window._autoConverge.cancel);
  }
  function throwIfCancelled() {
    if (cancelled()) {
      var err = new Error("Auto-iterate stopped by user.");
      err.cancelled = true;
      throw err;
    }
  }

  log("Auto-iterate started (max " + maxRounds + " synthetic rounds → then OpenFOAM).");
  setSt("Auto-iterate: preparing…");

  // Ensure we have a baseline report with industry advice
  var start = Promise.resolve();
  if (!window._lastIndustryAdvice || !window._lastIndustryAdvice.items) {
    log("No report yet — building baseline design report…");
    start = loadDesignReport({
      use_openfoam_sample: false,
      stabilizeLayout: true
    });
  }

  function oneRound(round) {
    throwIfCancelled();
    var label = round + "/" + maxRounds;
    setSt("Auto round " + label + ": applying all open fixes…");
    log("--- Round " + label + " ---");

    var openBefore = countOpenActionableFixes();
    if (openBefore === 0) {
      log("No open fixes — synthetic gates settled.");
      return Promise.resolve({ reason: "converged", rounds: round - 1 });
    }

    var sigBefore = _knobSignature();
    return applySelectedFixesAndReanalyze({
      selectAll: true,
      fromAuto: true,
      roundLabel: "auto " + label
    }).then(function (res) {
      throwIfCancelled();
      if (res.skipped) {
        log("Nothing selected / nothing to apply — treating as converged.");
        return { reason: "converged", rounds: round - 1 };
      }
      if (res.stuck || !(res.applied && res.applied.length)) {
        log("Knobs at limits or write failed — stopping synthetic loop.");
        return { reason: "stuck", rounds: round };
      }
      log("Applied: " + res.applied.join("; "));
      var openAfter = countOpenActionableFixes();
      var sigAfter = _knobSignature();
      log("Open fixes now: " + openAfter + (openAfter === 0 ? " (all clear)" : ""));

      if (openAfter === 0) {
        return { reason: "converged", rounds: round };
      }
      if (sigBefore === sigAfter) {
        log("Knobs unchanged after apply — design at clamp / fixed-point. Stopping.");
        return { reason: "stuck", rounds: round };
      }
      if (round >= maxRounds) {
        log("Hit max rounds (" + maxRounds + ") with " + openAfter + " still open.");
        return { reason: "max_rounds", rounds: round, openLeft: openAfter };
      }
      return oneRound(round + 1);
    });
  }

  return start
    .then(function () {
      throwIfCancelled();
      return oneRound(1);
    })
    .then(function (result) {
      throwIfCancelled();
      result = result || { reason: "converged", rounds: 0 };
      log(
        "Synthetic phase done: " + result.reason +
        " after " + (result.rounds || 0) + " round(s)."
      );
      setSt("Converged (" + result.reason + "). Rebuilding §3 case for OpenFOAM…");
      log("Rebuilding OpenFOAM case from current §1–2…");
      if (!document.forms.casegen) {
        throw new Error("§3 case form missing — cannot rebuild for OpenFOAM.");
      }
      return generateCase(document.forms.casegen).then(function (caseData) {
        if (!caseData || !caseData.success) {
          throw new Error(
            "Case rebuild failed: " +
            ((caseData && (caseData.message || caseData.error)) || "unknown")
          );
        }
        log("Case ready: " + (caseData.case_dir || ""));
        return caseData;
      });
    })
    .then(function () {
      throwIfCancelled();
      setSt("Running full OpenFOAM test (mesh → solve → sample)…");
      log("Full CFD test: mesh → solve → sample (this can take a while)…");
      if (!document.forms.mesh) {
        throw new Error("§4 mesh form missing.");
      }
      // Skip auto design report inside runFullTest — load OF sample next
      return runFullTest(document.forms.mesh, {
        skipDesignReport: true,
        stabilizeLayout: true
      });
    })
    .then(function (ofData) {
      throwIfCancelled();
      setSt("OpenFOAM done — building design report from CFD sample…");
      log("Building final design report from OpenFOAM surface sample…");
      return loadDesignReport({
        use_openfoam_sample: true,
        stabilizeLayout: true
      }).then(function (report) {
        return { ofData: ofData, report: report };
      });
    })
    .then(function () {
      var openFinal = countOpenActionableFixes();
      log(
        "Done. Auto-iterate + OpenFOAM complete." +
        (openFinal ? (" " + openFinal + " gate(s) still open on CFD.") : " All open gates cleared on CFD.")
      );
      setSt(
        "Auto complete. CFD report loaded." +
        (openFinal ? (" " + openFinal + " issue(s) remain.") : " Gates look clear.")
      );
      if (delta) delete delta.dataset.keep;
      // No scrollIntoView — keep viewport stable across multi-round work
    })
    .catch(function (e) {
      if (e && e.cancelled) {
        log("Stopped by user before OpenFOAM (or mid-step).");
        setSt("Auto-iterate stopped.");
      } else {
        log("Auto-iterate error: " + String((e && e.message) || e));
        setSt("Auto-iterate failed: " + String((e && e.message) || e));
      }
      if (delta) delete delta.dataset.keep;
    })
    .then(function () {
      if (window._autoConverge) window._autoConverge.running = false;
      _setAutoUiRunning(false);
    });
}

function fillStationsTable(stations) {
  var body = document.getElementById("stations_body");
  if (!body) return;
  body.innerHTML = "";
  (stations || []).forEach(function (s) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + fmt(s.x_c, 3) + "</td>" +
      "<td>" + fmt(s.cp_ps, 4) + "</td>" +
      "<td>" + fmt(s.cp_ss, 4) + "</td>" +
      "<td>" + fmt(s.delta_cp, 4) + "</td>" +
      "<td>" + fmt(s.p_ps_pa, 5) + "</td>" +
      "<td>" + fmt(s.p_ss_pa, 5) + "</td>" +
      "<td>" + fmt(s.m_isen_ss, 3) + "</td>";
    body.appendChild(tr);
  });
}

function fillSurfaceTable(rows) {
  var body = document.getElementById("surface_body");
  if (!body) return;
  body.innerHTML = "";
  (rows || []).forEach(function (r) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + (r.side || "") + "</td>" +
      "<td>" + fmt(r.x_c, 4) + "</td>" +
      "<td>" + fmt(r.p_pa, 6) + "</td>" +
      "<td>" + fmt(r.Cp, 4) + "</td>";
    body.appendChild(tr);
  });
}

function fillShockRelationsTable(shocks, note) {
  var body = document.getElementById("shock_table_body");
  var noteEl = document.getElementById("gasdynamics_note");
  if (noteEl) noteEl.textContent = note || "";
  if (!body) return;
  body.innerHTML = "";
  if (!shocks || !shocks.length) {
    var tr0 = document.createElement("tr");
    tr0.innerHTML = "<td colspan='10' style='text-align:left'>No shocks detected — no jump table rows.</td>";
    body.appendChild(tr0);
    return;
  }
  shocks.forEach(function (s) {
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + (s.side || "") + "</td>" +
      "<td>" + fmt(s.x_c, 3) + "</td>" +
      "<td>" + (s.severity || "") + "</td>" +
      "<td>" + fmt(s.M1, 3) + "</td>" +
      "<td>" + fmt(s.M2, 3) + "</td>" +
      "<td>" + fmt(s.p2_p1, 4) + "</td>" +
      "<td>" + fmt(s.rho2_rho1, 4) + "</td>" +
      "<td>" + fmt(s.T2_T1, 4) + "</td>" +
      "<td>" + fmt(s.p02_p01, 5) + "</td>" +
      "<td>" + (s.kind || "normal") + "</td>";
    body.appendChild(tr);
  });
}

function showPlot(id, b64) {
  var img = document.getElementById(id);
  if (!img) return;
  if (b64) {
    img.src = "data:image/png;base64," + b64;
    img.style.display = "block";
  }
}

function fillLossReport(report, paths, extras) {
  if (!report) return;
  extras = extras || {};
  window._lastLossReport = report;
  window._pipeline.lossOk = true;
  setField("loss_summary", extras.summary || report.summary || "");
  if (paths && paths.loss_json) setField("loss_json_path", paths.loss_json);
  if (paths && paths.design_package_json) {
    setField("loss_json_path", paths.design_package_json);
  }

  var fixes = document.getElementById("loss_fixes");
  if (fixes) {
    fixes.innerHTML = "";
    var rf = extras.ranked_fixes || report.ranked_fixes || [];
    rf.forEach(function (line) {
      var li = document.createElement("li");
      li.textContent = line;
      fixes.appendChild(li);
    });
    if (!rf.length) fixes.innerHTML = "<li>No ranked fixes — see loss items.</li>";
  }

  var shocks = document.getElementById("shock_items");
  if (shocks) {
    var sc = report.shock_candidates || extras.shocks || [];
    if (!sc.length) shocks.textContent = "(no shock candidates detected)";
    else {
      shocks.textContent = sc.map(function (s, i) {
        return (i + 1) + ". " + s.side + " x/c≈" + fmt(s.x_c, 3) +
          "  severity=" + s.severity + "  dCp=" + fmt(s.delta_cp, 2) + "\n   " + (s.note || "");
      }).join("\n");
    }
  }

  var box = document.getElementById("loss_items");
  if (box) {
    var lines = (report.losses || []).map(function (L, i) {
      return (
        (i + 1) + ". [" + fmt(L.severity, 2) + "] " + L.location + "\n" +
        "   mechanism: " + L.mechanism + "\n" +
        "   evidence:  " + L.evidence + "\n" +
        "   fix:       " + L.fix + "\n" +
        "   knobs:     " + (L.design_knobs || []).join(", ")
      );
    });
    box.textContent = lines.join("\n") || "(no loss items)";
  }

  var cl = document.getElementById("iter_checklist");
  if (cl) {
    cl.innerHTML = "";
    (extras.iteration_checklist || []).forEach(function (line) {
      var li = document.createElement("li");
      li.textContent = line;
      cl.appendChild(li);
    });
  }
  updatePipelineBanner();
}

function applyDesignReport(data) {
  window._lastSurface = data;
  window._lastDesignReport = data.design_report || data;
  window._pipeline.surfaceOk = true;
  window._pipeline.lossOk = true;
  setServerBanner(true);

  var form = document.forms.cpform;
  if (form) {
    if (form.cp_source) form.cp_source.value = data.source || "";
    if (form.cp_pref) form.cp_pref.value = fmt(data.p_ref_pa);
    if (form.cp_qref) form.cp_qref.value = fmt(data.q_ref_pa);
    if (form.cp_csv) form.cp_csv.value = data.surface_csv || (data.exports && data.exports.surface_csv) || "";
  }

  var sum = data.summary || (data.design_report && data.design_report.summary) || "";
  var el = document.getElementById("report_summary_line");
  if (el) el.textContent = sum;

  if (data.metrics) fillMetricsBoard(data.metrics);
  // Resolve industry_advice from several response shapes; never leave the box blank after a report
  var advice =
    data.industry_advice ||
    (data.design_report && data.design_report.industry_advice) ||
    null;
  fillIndustryAdvice(advice, { hasMetrics: !!data.metrics, data: data });
  fillStationsTable(data.stations || []);
  fillSurfaceTable(data.surface_table || []);

  var plots = data.plots || {};
  showPlot("cp_img", data.plot_png_base64 || plots.cp);
  showPlot("plot_loading", plots.loading_mach);
  showPlot("plot_loss_bars", plots.loss_bars);
  showPlot("plot_hill_shock", data.plot_hill_shock_chart || plots.hill_shock_chart);

  fillShockRelationsTable(
    data.shock_relations_table || data.shocks || [],
    data.gasdynamics_note || ""
  );

  var loss = data.loss_report || data.report;
  if (loss) {
    fillLossReport(loss, {
      loss_json: data.loss_json || (data.exports && data.exports.loss_json),
      design_package_json: data.design_package_json || (data.exports && data.exports.design_package_json),
      surface_csv: data.surface_csv
    }, {
      summary: sum,
      ranked_fixes: data.ranked_fixes || loss.ranked_fixes,
      shocks: data.shocks,
      iteration_checklist: data.iteration_checklist
    });
  }

  var expList = document.getElementById("export_list");
  if (expList && data.exports) {
    expList.value = Object.keys(data.exports).map(function (k) {
      return k + "=" + data.exports[k];
    }).join(" | ");
  }
  if (data.design_package_json) setField("export_path", data.design_package_json);
  updatePipelineBanner();
}

function loadDesignReport(opts) {
  opts = opts || {};
  var form = document.forms.cpform;
  var caseDir =
    (form && form.case_dir && form.case_dir.value) ||
    window._pipeline.caseDir ||
    (document.forms.casegen && document.forms.casegen.case_dir.value) ||
    "";
  if (form && form.case_dir) form.case_dir.value = caseDir;

  // Snapshot previous metrics so we can show what changed
  var prev = window._lastMetrics ? JSON.parse(JSON.stringify(window._lastMetrics)) : null;

  var body = analysisPayload(caseDir, {
    force_synthetic: opts.use_openfoam_sample ? false : true,
    include_plots: true
  });
  setField("loss_summary", "Building design report…");
  var el = document.getElementById("report_summary_line");
  if (el) el.textContent = "Building dense CFD design report from current §1–2…";
  var st = document.getElementById("auto_rerun_status");
  var advSum = document.getElementById("advice_summary");
  if (advSum) {
    advSum.textContent = "building report…";
    advSum.style.color = "#3a2860";
    advSum.style.fontWeight = "normal";
  }
  // Dim / busy status — never reserve empty height (cards collapse cleanly when results land)
  if (opts.stabilizeLayout) {
    showSuggestionListWorking("Re-analyzing…");
  } else {
    showSuggestionListWorking("Building report…");
    var sug = document.getElementById("suggestion_list");
    if (sug) {
      sug.style.minHeight = "";
      sug.style.height = "";
      sug.innerHTML =
        "<div class='fix-empty'><strong>Working…</strong>Building industry gate analysis.</div>";
    }
  }

  return apiJson("/api/design_report", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify(body)
  })
    .then(function (data) {
      applyDesignReport(data);
      var deltaMsg = _metricsDeltaMessage(prev, data.metrics);
      if (el && deltaMsg) el.textContent = (data.summary || "") + "  |  " + deltaMsg;
      if (st && deltaMsg) st.textContent = deltaMsg;
      // Ensure fix box always updated even if applyDesignReport path missed advice
      if (!window._lastIndustryAdvice) {
        fillIndustryAdvice(
          data.industry_advice || (data.design_report && data.design_report.industry_advice),
          { hasMetrics: !!data.metrics, data: data }
        );
      }
      return data;
    })
    .catch(function (e1) {
      // Prefer surface_pressure fallback for analysis errors; network errors retry via apiJson
      var m1 = String(e1.message || e1);
      if (_isNetworkFetchError(e1)) {
        setField("loss_summary", m1);
        setServerBanner(false, m1);
        throw e1;
      }
      return apiJson("/api/surface_pressure", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(body)
      })
        .then(function (data) {
          applyDesignReport(data);
          var deltaMsg = _metricsDeltaMessage(prev, data.metrics);
          if (el && deltaMsg) el.textContent = (data.summary || "") + "  |  " + deltaMsg;
          return data;
        })
        .catch(function (e2) {
          var msg = String(e2.message || e2);
          setField("loss_summary", msg);
          setServerBanner(false, msg);
          throw e2;
        });
    });
}

function _metricsDeltaMessage(prev, next) {
  if (!prev || !next) return "Report loaded (baseline).";
  var keys = [
    ["eta_design_proxy", "η"],
    ["lieblein_df_ss", "DF"],
    ["peak_ss_cp", "Cp_ss"],
    ["peak_ss_x_c", "x_peak"],
    ["n_shocks", "shocks"],
    ["mach_w1", "Mw1"],
    ["surface_loss_penalty", "penalty"]
  ];
  var parts = [];
  keys.forEach(function (pair) {
    var k = pair[0], lab = pair[1];
    var a = prev[k], b = next[k];
    if (a == null || b == null) return;
    var d = b - a;
    if (Math.abs(d) < 1e-6 && k !== "n_shocks") return;
    if (k === "n_shocks" && d === 0) return;
    var sign = d > 0 ? "+" : "";
    parts.push(lab + " " + sign + (k === "n_shocks" ? String(d) : fmt(d, 3)));
  });
  if (!parts.length) {
    return "Re-analyzed — metrics unchanged (same knobs / same synthetic state).";
  }
  return "Δ since last report: " + parts.join(", ");
}

function loadSurfaceAnalysis(form) {
  return loadDesignReport();
}
function loadCp(form) { return loadDesignReport(); }
function runLossAnalysis() {
  // Explicit re-analyze: refresh from live forms, force shape-sensitive synthetic
  return loadDesignReport({ force_refresh: true });
}

function downloadSurfaceCsv() {
  var s = window._lastSurface;
  if (!s || (!s.x_c_ps && !s.x_c_ss && !(s.surface_table && s.surface_table.length))) {
    alert("Build design report in §5 first.");
    return;
  }
  var lines = ["side,x_c,p_pa,Cp"];
  if (s.surface_table && s.surface_table.length) {
    s.surface_table.forEach(function (r) {
      lines.push((r.side || "") + "," + r.x_c + "," + r.p_pa + "," + r.Cp);
    });
  } else {
    function add(side, xs, ps, cps) {
      for (var i = 0; i < (xs || []).length; i++) {
        lines.push(side + "," + xs[i] + "," + (ps[i] || "") + "," + (cps[i] || ""));
      }
    }
    add("PS", s.x_c_ps, s.p_ps, s.cp_ps);
    add("SS", s.x_c_ss, s.p_ss, s.cp_ss);
  }
  var blob = new Blob([lines.join("\n")], { type: "text/csv" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "surface_pressure_ps_ss.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function downloadStationsCsv() {
  var stations = (window._lastSurface && window._lastSurface.stations) ||
    (window._lastDesignReport && window._lastDesignReport.stations) || [];
  if (!stations.length) {
    alert("Build design report in §5 first.");
    return;
  }
  var lines = ["x_c,cp_ps,cp_ss,delta_cp,p_ps_pa,p_ss_pa,m_isen_ss"];
  stations.forEach(function (s) {
    lines.push([s.x_c, s.cp_ps, s.cp_ss, s.delta_cp, s.p_ps_pa, s.p_ss_pa, s.m_isen_ss].join(","));
  });
  var blob = new Blob([lines.join("\n")], { type: "text/csv" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "stations.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportDesignPackage() {
  if (document.forms.meanline) calcMeanline(document.forms.meanline);
  var m = window._lastMeanline;
  var caseDir = window._pipeline.caseDir ||
    (document.forms.casegen && document.forms.casegen.case_dir.value) || "";
  var dr = window._lastDesignReport || window._lastSurface || {};
  var ext = (typeof getDomainExtents === "function") ? getDomainExtents() : { x_up_c: 0.5, x_dn_c: 1.0 };
  var pkg = {
    format: "impulsecalc_design_package_v3",
    schema_version: 3,
    description: "Comparable CFD design package for turbine loss iteration (v3)",
    blade_name: m ? m.blade_name : "impulse",
    meanline: meanlinePayload(document.forms.meanline),
    meanline_inputs: meanlinePayload(document.forms.meanline),
    blade_shape: bladeShapeFromForm(document.forms.bladeform),
    domain: {
      x_up_c: ext.x_up_c,
      x_dn_c: ext.x_dn_c
    },
    operating: dr.operating || {
      beta1_deg: m ? m.beta1 : null,
      beta2_deg: m ? m.beta2 : null,
      mach_w1: m ? m.Mw1 : null,
      p1_pa: m ? m.p1 : null
    },
    metrics: dr.metrics || window._lastMetrics || null,
    stations: dr.stations || null,
    surface_table: dr.surface_table || null,
    shocks: dr.shocks || dr.shock_relations_table || null,
    shock_relations_table: dr.shock_relations_table || dr.shocks || null,
    loss_report: window._lastLossReport || dr.loss_report || null,
    industry_advice: dr.industry_advice || window._lastIndustryAdvice || null,
    ranked_fixes: dr.ranked_fixes || null,
    summary: dr.summary || null,
    case_dir: caseDir,
    n_blades: document.forms.casegen
      ? parseInt(document.forms.casegen.n_blades.value, 10)
      : 3,
    exports: dr.exports || {}
  };
  // Ask server to save under output/ (writes JSON + comparison_scalars.csv)
  apiJson("/api/save_design", {
    method: "POST",
    body: JSON.stringify({
      meanline: pkg.meanline_inputs,
      blade_shape: pkg.blade_shape,
      case_dir: caseDir,
      output_dir: (document.forms.casegen && document.forms.casegen.output_dir.value) || "output",
      package: pkg
    })
  })
    .then(function (data) {
      var path = data.path || "";
      var exp = data.exports || {};
      var list = Object.keys(exp).map(function (k) { return k + "=" + exp[k]; }).join(" | ");
      setField("export_path", path);
      setField("export_list", list || path);
      var blob = new Blob([JSON.stringify(pkg, null, 2)], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (m && m.blade_name ? m.blade_name : "impulse") + "_package_v3.json";
      a.click();
      URL.revokeObjectURL(a.href);
      if (!path) setField("export_path", a.download + " (browser download)");
    })
    .catch(function (e) {
      var blob = new Blob([JSON.stringify(pkg, null, 2)], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (m && m.blade_name ? m.blade_name : "impulse") + "_package_v3.json";
      a.click();
      URL.revokeObjectURL(a.href);
      setField("export_path", "downloaded locally; server save failed: " + String(e.message || e));
      setServerBanner(false, String(e.message || e));
    });
}

function genVideo(form) {
  form = form || document.forms.exportform;
  var caseDir = requireCaseDir(form, "Video");
  if (!caseDir) return;
  if (document.forms.meanline && !window._lastMeanline) calcMeanline(document.forms.meanline);
  var m = window._lastMeanline;
  var payload = {
    case_dir: caseDir,
    fields: [],
    resolution: (form.resolution && form.resolution.value) || "1080p",
    fps: parseInt((form.fps && form.fps.value) || "12", 10),
    steady_hold_s: parseFloat((form.steady_hold_s && form.steady_hold_s.value) || "1") || 1,
    duration_mode: "full",
    view_preset: (form.view && form.view.value) || "blade_passage_shocks",
    output_format: "mp4",
    run_pvbatch: form.run_pv ? form.run_pv.checked : true,
    show_blades: true,
    blade_name: m ? m.blade_name : "user_stage_r040",
    inlet_p1_pa: m ? m.p1 : null,
    inlet_t1_k: m ? m.T1 : null,
    beta1_deg: m ? m.beta1 : null,
    mach_w1: m ? m.Mw1 : null,
    gamma: m ? m.gamma : null,
    r_specific: m ? m.R : null
  };
  var boxes = form.querySelectorAll("input[name='vfield']:checked");
  for (var i = 0; i < boxes.length; i++) payload.fields.push(boxes[i].value);
  // Engineering minimums if user unchecked everything
  if (!payload.fields.length) {
    payload.fields = ["Mach", "streamlines", "U_vectors", "rho_gradient"];
  }

  setStatus("video_status", "working…");
  if (form.video_msg) form.video_msg.value = "Building engineering video (blades + Mach + flow paths)…";
  apiJson("/api/video", {
    method: "POST",
    body: JSON.stringify(payload)
  })
    .then(function (data) {
      if (form.video_status_out) form.video_status_out.value = data.status || "";
      if (form.video_msg) form.video_msg.value = data.message || "";
      if (form.video_out) {
        form.video_out.value = data.output_path || data.script_path || "";
      }
      setStatus("video_status", data.status || "done");
      refreshWorkflow(caseDir);
    })
    .catch(function (e) {
      if (form.video_msg) form.video_msg.value = String(e.message || e);
      setStatus("video_status", "error");
      setServerBanner(false, String(e.message || e));
    });
}

function refreshWorkflow(caseDir) {
  if (!caseDir) return;
  apiJson("/api/workflow?case_dir=" + encodeURIComponent(caseDir))
    .then(function (data) {
      var f = document.forms.mesh;
      if (f) {
        f.wf_mesh.value = data.mesh_ready ? "done" : "pending";
        f.wf_solver.value = data.solver_ready ? "done" : "pending";
        f.wf_video.value = data.video_ready ? "ready" : (data.script_present ? "script" : "pending");
        f.wf_label.value = data.label || "";
      }
      if (data.mesh_ready) window._pipeline.meshOk = true;
      if (data.solver_ready) window._pipeline.solveOk = true;
      updatePipelineBanner();
    })
    .catch(function () { /* non-fatal */ });
}

function saveDesign() {
  var payload = meanlinePayload(document.forms.meanline);
  if (!payload) return;
  apiJson("/api/save_design", {
    method: "POST",
    body: JSON.stringify({
      meanline: payload,
      blade_shape: bladeShapeFromForm(document.forms.bladeform),
      case_dir: (document.forms.casegen && document.forms.casegen.case_dir.value) || "",
      output_dir: (document.forms.casegen && document.forms.casegen.output_dir.value) || "output"
    })
  })
    .then(function (data) {
      alert(data.path ? ("Saved " + data.path) : (data.error || "save failed"));
    })
    .catch(function (e) {
      alert(String(e.message || e));
      setServerBanner(false, String(e.message || e));
    });
}

/**
 * Apply configs/default_design.json (user stage table) to §1–2 forms.
 * Called on page load so the board always starts from the user's turbine.
 */
function applyDefaultDesignPayload(d) {
  if (!d) return;
  var ml = d.meanline_inputs || {};
  var sh = d.blade_shape || {};
  var geo = d.geometry || {};
  var f = document.forms.meanline;
  if (f) {
    if (f.gamma && ml.gamma != null) f.gamma.value = String(ml.gamma);
    if (f.R && ml.r_specific_j_kg_k != null) f.R.value = String(ml.r_specific_j_kg_k);
    if (f.beta1 && ml.beta1_deg != null) f.beta1.value = String(ml.beta1_deg);
    if (f.beta2 && ml.beta2_deg != null) f.beta2.value = String(ml.beta2_deg);
    if (f.w1 && ml.w1_m_s != null) f.w1.value = String(ml.w1_m_s);
    if (f.p1 && ml.p1_pa != null) f.p1.value = String(ml.p1_pa);
    if (f.T1 && ml.t1_k != null) f.T1.value = String(ml.t1_k);
    if (f.mu && ml.mu_pa_s != null) f.mu.value = String(ml.mu_pa_s);
    if (f.U && ml.blade_speed_u_m_s != null) f.U.value = String(ml.blade_speed_u_m_s);
    if (f.r_m && ml.mean_radius_m != null) f.r_m.value = String(ml.mean_radius_m);
    if (f.span && ml.span_m != null) f.span.value = String(ml.span_m);
    if (f.chord && ml.chord_m != null) f.chord.value = String(ml.chord_m);
    if (f.solidity && ml.solidity != null) f.solidity.value = String(ml.solidity);
    var pitch = geo.blade_spacing_m;
    if (pitch == null && ml.chord_m && ml.solidity) pitch = ml.chord_m / ml.solidity;
    if (f.pitch && pitch != null) f.pitch.value = String(pitch);
    if (f.blade_name) f.blade_name.value = ml.blade_name || "user_stage_r040";
    if (f.pure_impulse || f.pure) {
      var pureBox = f.pure_impulse || f.pure;
      if (pureBox && pureBox.type === "checkbox") {
        pureBox.checked = ml.pure_impulse_lock !== false;
      }
    }
    // Keep packing slider consistent with s/c
    if (f.pitch_sc && pitch != null && ml.chord_m) {
      var sc = pitch / ml.chord_m;
      f.pitch_sc.value = String(sc);
      if (f.pitch_sc_v) f.pitch_sc_v.value = String(Number(sc).toFixed(2));
    }
  }
  var bf = document.forms.bladeform;
  if (bf && sh) {
    if (bf.profile_family && sh.profile_family) bf.profile_family.value = sh.profile_family;
    if (bf.upper_h && sh.upper_sagitta_c != null) {
      bf.upper_h.value = String(sh.upper_sagitta_c);
      if (bf.upper_h_v) bf.upper_h_v.value = String(sh.upper_sagitta_c);
    }
    if (bf.lower_h && sh.lower_sagitta_c != null) {
      bf.lower_h.value = String(sh.lower_sagitta_c);
      if (bf.lower_h_v) bf.lower_h_v.value = String(sh.lower_sagitta_c);
    }
    if (bf.thk && sh.thickness_ratio != null) {
      bf.thk.value = String(sh.thickness_ratio);
      if (bf.thk_v) bf.thk_v.value = String(sh.thickness_ratio);
    }
    if (bf.wall_t && sh.wall_thickness_c != null) {
      bf.wall_t.value = String(sh.wall_thickness_c);
      if (bf.wall_t_v) bf.wall_t_v.value = String(sh.wall_thickness_c);
    }
    if (bf.suction_cut && sh.bucket_suction_cutback != null) {
      bf.suction_cut.value = String(sh.bucket_suction_cutback);
      if (bf.suction_cut_v) bf.suction_cut_v.value = String(sh.bucket_suction_cutback);
    }
    if (bf.peak && sh.thickness_peak_x != null) {
      bf.peak.value = String(sh.thickness_peak_x);
      if (bf.peak_v) bf.peak_v.value = String(sh.thickness_peak_x);
    }
    if (bf.bulge && sh.arc_bulge != null) {
      bf.bulge.value = String(sh.arc_bulge);
      if (bf.bulge_v) bf.bulge_v.value = String(sh.arc_bulge);
    }
    if (bf.line_in && sh.inlet_line_frac != null) {
      bf.line_in.value = String(sh.inlet_line_frac);
      if (bf.line_in_v) bf.line_in_v.value = String(sh.inlet_line_frac);
    }
    if (bf.line_out && sh.outlet_line_frac != null) {
      bf.line_out.value = String(sh.outlet_line_frac);
      if (bf.line_out_v) bf.line_out_v.value = String(sh.outlet_line_frac);
    }
    if (bf.le && sh.le_fillet_r_c != null) {
      bf.le.value = String(sh.le_fillet_r_c);
      if (bf.le_v) bf.le_v.value = String(sh.le_fillet_r_c);
    }
    if (bf.te_fillet && sh.te_fillet_r_c != null) {
      bf.te_fillet.value = String(sh.te_fillet_r_c);
      if (bf.te_fillet_v) bf.te_fillet_v.value = String(sh.te_fillet_r_c);
    }
    if (bf.te && sh.te_fillet_r_c != null) {
      bf.te.value = String(sh.te_fillet_r_c);
      if (bf.te_v) bf.te_v.value = String(sh.te_fillet_r_c);
    }
    if (typeof syncArcSliders === "function") syncArcSliders(bf);
  }
  var cg = document.forms.casegen;
  if (cg && ml.blade_name) {
    // case name follows design name when rebuilding
  }
}

function loadAndApplyDefaultDesign() {
  return apiJson("/api/default_design")
    .then(function (d) {
      if (!d || d.ok === false) return d;
      applyDefaultDesignPayload(d);
      window._defaultDesign = d;
      return d;
    })
    .catch(function () {
      // Offline / old server: HTML form values already encode the user table
      return null;
    });
}

/** Snapshot for LPRE Library bookshelf handoff (parent iframe postMessage). */
function collectLibraryExport() {
  var mlForm = document.forms.meanline;
  var bf = document.forms.bladeform;
  var ml = null;
  try {
    ml = meanlinePayload(mlForm);
  } catch (e0) {
    ml = null;
  }
  var shape = null;
  try {
    shape = typeof bladeShapeFromForm === "function" ? bladeShapeFromForm(bf) : null;
  } catch (e1) {
    shape = null;
  }
  var last = window._lastMeanline || {};
  var metrics = window._lastMetrics || {};
  var loss = window._lastLossReport || {};
  var caseDir = "";
  try {
    if (document.forms.casegen && document.forms.casegen.case_dir) {
      caseDir = document.forms.casegen.case_dir.value || "";
    }
  } catch (e2) { /* ignore */ }
  var interfaces = {
    shaft_rpm: ml && ml.rpm != null ? ml.rpm : last.rpm,
    turbine_power_w: ml && ml.power_target_w != null ? ml.power_target_w : (last.power_w || metrics.power_w),
    chord_m: ml && ml.chord_m,
    solidity: ml && ml.solidity,
    pitch_m: ml && ml.chord_m && ml.solidity ? ml.chord_m / ml.solidity : null,
    mean_radius_m: ml && ml.mean_radius_m,
    span_m: ml && ml.span_m,
    beta1_deg: ml && ml.beta1_deg,
    beta2_deg: ml && ml.beta2_deg,
    w1_m_s: ml && ml.w1_m_s,
    euler_work_j_kg: last.euler_work || metrics.euler_work_j_kg || null,
    eta_design_proxy: metrics.eta_design_proxy || metrics.eta_meanline_proxy || null,
    mach_w1: last.mach_w1 || metrics.mach_w1 || null,
    tip_mach_proxy: metrics.tip_mach_proxy || null,
    opening_o_s: (window._lastThroat && window._lastThroat.opening_o_s) || metrics.opening_o_s || null
  };
  // Drop nulls
  Object.keys(interfaces).forEach(function (k) {
    if (interfaces[k] == null || interfaces[k] === "" || (typeof interfaces[k] === "number" && isNaN(interfaces[k]))) {
      delete interfaces[k];
    }
  });
  return {
    source: "impulsecalc",
    exported_at: new Date().toISOString(),
    case_dir: caseDir || null,
    meanline: ml,
    blade_shape: shape,
    meanline_result: last,
    metrics: metrics,
    loss_report: loss,
    interfaces: interfaces,
    checklist_updates: {
      turbine_blades: "done",
      turbine_rotor: "done",
      turbine_system: "done",
      turbine_stator: "in_progress"
    },
    notes: ["Exported to LPRE Library bookshelf"]
  };
}

function _libraryQuery() {
  var q = {};
  try {
    var sp = new URLSearchParams(window.location.search || "");
    q.model_id = sp.get("library_model_id") || sp.get("model_id") || "";
    q.book_id = sp.get("library_book_id") || sp.get("book_id") || "turbine.impulsecalc";
    q.library_origin = sp.get("library_origin") || "http://127.0.0.1:8770";
    q.from_library = sp.get("from_library") === "1" || sp.get("embed") === "library" || !!q.model_id;
  } catch (eQ) {
    q = { model_id: "", book_id: "turbine.impulsecalc", library_origin: "http://127.0.0.1:8770", from_library: false };
  }
  return q;
}

/** Save live snapshot for Library tab to import when user returns. */
function pushLibraryHandoff(doneCb) {
  var payload = collectLibraryExport();
  var q = _libraryQuery();
  payload.library_model_id = q.model_id || null;
  payload.library_book_id = q.book_id || "turbine.impulsecalc";
  // 1) Always write handoff file on ImpulseCalc server
  var p1 = apiJson("/api/library_handoff", {
    method: "POST",
    body: JSON.stringify(payload)
  }).catch(function (e) {
    console.warn("library_handoff", e);
    return { ok: false, error: String(e) };
  });
  // 2) If opened from Library with model id, POST directly into that model
  var p2 = Promise.resolve(null);
  if (q.model_id && q.library_origin) {
    p2 = fetch(q.library_origin.replace(/\/$/, "") + "/api/models/" + encodeURIComponent(q.model_id) + "/import_app", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book_id: q.book_id || "turbine.impulsecalc",
        mark_aliases: true,
        payload: payload
      })
    })
      .then(function (r) { return r.json(); })
      .catch(function (e) {
        console.warn("library import_app", e);
        return { ok: false, error: String(e) };
      });
  }
  return Promise.all([p1, p2]).then(function (pair) {
    var handoff = pair[0] || {};
    var imported = pair[1];
    if (typeof doneCb === "function") doneCb(handoff, imported);
    return { handoff: handoff, imported: imported, payload: payload };
  });
}

function initLibraryBridge() {
  var q = _libraryQuery();
  var inFrame = false;
  try {
    inFrame = window.self !== window.top;
  } catch (eF) {
    inFrame = true;
  }
  window.collectLibraryExport = collectLibraryExport;
  window.pushLibraryHandoff = pushLibraryHandoff;

  if (q.from_library || inFrame) {
    document.documentElement.classList.add("lpre-library-embed");
    try {
      var bar = document.createElement("div");
      bar.id = "lpre_library_embed_bar";
      bar.innerHTML =
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;width:100%;">' +
        '<span style="font-weight:600;letter-spacing:0.12em;text-transform:uppercase;font-size:11px;">ImpulseCalc · LPRE Library</span>' +
        '<span style="opacity:0.8;font-size:12px;flex:1;min-width:200px;">Finish design / CFD here, then send results back to the Library tab.</span>' +
        '<button type="button" id="lpre_send_btn" style="cursor:pointer;font-weight:600;font-size:12px;letter-spacing:0.06em;text-transform:uppercase;padding:8px 14px;border-radius:999px;border:0;background:#e8d5a3;color:#1a1410;">Done — send to Library</button>' +
        '</div>' +
        '<div id="lpre_send_msg" style="font-size:12px;margin-top:6px;opacity:0.85;"></div>';
      bar.style.cssText =
        "position:sticky;top:0;z-index:9999;background:#1a1410;color:#e8d5a3;padding:10px 14px;" +
        "border-bottom:1px solid rgba(201,162,39,0.35);font-family:system-ui,sans-serif;";
      if (document.body) document.body.insertBefore(bar, document.body.firstChild);
      var btn = document.getElementById("lpre_send_btn");
      var msg = document.getElementById("lpre_send_msg");
      if (btn) {
        btn.onclick = function () {
          btn.disabled = true;
          btn.textContent = "Sending…";
          if (msg) msg.textContent = "Packaging meanline, geometry, metrics…";
          pushLibraryHandoff(function (handoff, imported) {
            btn.disabled = false;
            btn.textContent = "Done — send to Library";
            var okH = handoff && handoff.ok !== false;
            var okI = imported && imported.ok;
            if (msg) {
              if (okI) {
                msg.textContent =
                  "✓ Sent to Library model" +
                  (q.model_id ? " “" + q.model_id + "”" : "") +
                  ". Switch back to the Library tab — the book is marked complete and numbers are on the pedestal.";
              } else if (okH) {
                msg.textContent =
                  "✓ Results saved for Library import. Return to the Library tab and click “Import results” (or just focus that tab).";
              } else {
                msg.textContent = "Send failed — is ImpulseCalc server running? " + (handoff && handoff.error ? handoff.error : "");
              }
            }
          });
        };
      }
    } catch (eBar) {
      console.warn(eBar);
    }
  }

  window.addEventListener("message", function (ev) {
    var data = ev.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "lpre_library_export_request" || data === "lpre_library_export_request") {
      var payload = null;
      try {
        payload = collectLibraryExport();
      } catch (eExp) {
        payload = { error: String(eExp), source: "impulsecalc" };
      }
      try {
        (ev.source || window.parent).postMessage(
          { type: "lpre_library_export", payload: payload },
          "*"
        );
      } catch (ePost) {
        console.warn("library export postMessage failed", ePost);
      }
    }
  });
}

window.onload = function () {
  loadSavedUnitSystem();
  // Top-bar CFD fidelity (default Fast = legacy design-board mesh sizes)
  try {
    updateFidelityReadout(getFidelitySettings());
  } catch (eFid) { /* optional if DOM partial */ }
  try {
    initLibraryBridge();
  } catch (eLib) {
    console.warn("library bridge", eLib);
  }
  checkServer().then(function () {
    return loadAndApplyDefaultDesign();
  }).then(function () {
    if (document.forms.meanline) {
      toggleBeta2(document.forms.meanline);
      calcMeanline(document.forms.meanline);
    }
    if (document.forms.bladeform) {
      if (typeof syncLpreSliders === "function") syncLpreSliders(document.forms.bladeform);
      else if (typeof syncBladeSliders === "function") syncBladeSliders(document.forms.bladeform);
      if (typeof lpreDerivedUpdate === "function") lpreDerivedUpdate();
      onBladeChange({ fromMeanline: true });
    }
    updatePipelineBanner();
  });
};
