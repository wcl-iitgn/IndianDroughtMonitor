/* =============================================================================
 * drought-map.app.js  —  site controller layer
 * -----------------------------------------------------------------------------
 * Thin orchestration on top of createDroughtMap() (drought-map.engine.js).
 * It does NOT touch Pranav's rendering logic; it only:
 *   - knows the catalogue of data products (CDI, SPI, SRI, SSMI, forecasts…)
 *   - knows the list of available weekly dates (for date pickers / animation)
 *   - wires page controls (product <select>, date <select>, state <select>,
 *     reset button, isolate toggle) to the engine's public API
 *   - mounts one or many maps on a page (Compare uses two)
 *   - mirrors the engine's on-canvas hover readout into an HTML panel
 *
 * Every page includes engine.js then app.js, and calls IDM.mount*(...) helpers.
 * ========================================================================== */
(function () {
  "use strict";

  // ---- shared data locations -------------------------------------------------
  var PATHS = {
    stateGrid: "./states_with_boundaries.csv",
    mainland: "./india_mainland_boundary.csv",
    stateVectors: "./state_vector_boundaries.json"
  };

  // ---- product catalogue -----------------------------------------------------
  // Each product is a single lat/lng/value grid the engine can render.
  // `file` is a fixed path; `frames:true` means it has a weekly time series
  // under data/Drough_TS/CDI_YYYYMMDD.txt (only the CDI does).
  var PRODUCTS = {
    cdi:        { label: "Combined Drought Index (CDI)", file: "./data/Current_CDI.txt", frames: true },
    spi1:       { label: "SPI — 1 month",  file: "./data/SPI_1month.txt" },
    spi3:       { label: "SPI — 3 month",  file: "./data/SPI_3month.txt" },
    spi6:       { label: "SPI — 6 month",  file: "./data/SPI_6month.txt" },
    spi12:      { label: "SPI — 12 month", file: "./data/SPI_12month.txt" },
    sri1:       { label: "SRI — 1 month",  file: "./data/SRI_1month.txt" },
    sri3:       { label: "SRI — 3 month",  file: "./data/SRI_3month.txt" },
    sri6:       { label: "SRI — 6 month",  file: "./data/SRI_6month.txt" },
    sri12:      { label: "SRI — 12 month", file: "./data/SRI_12month.txt" },
    ssmi1:      { label: "SSMI — 1 month", file: "./data/SSMI_1month.txt" },
    ssmi3:      { label: "SSMI — 3 month", file: "./data/SSMI_3month.txt" },
    ssmi6:      { label: "SSMI — 6 month", file: "./data/SSMI_6month.txt" },
    fcdi7:      { label: "Forecast CDI — 7 day",  file: "./data/Future_CDI_7day.txt" },
    fcdi15:     { label: "Forecast CDI — 15 day", file: "./data/Future_CDI_15day.txt" },
    fcdi30:     { label: "Forecast CDI — 30 day", file: "./data/Future_CDI_30day.txt" }
  };

  // ---- weekly dates (derived from the CDI time series filenames) -------------
  // Populated by buildDateList() on first use; format "YYYY-MM-DD" and "YYYYMMDD".
  var DATES = null;

  // The CDI series runs Tue 2021-07-14 weekly. We generate the list client-side
  // and the engine's loadCDIDataForDate() simply skips any missing file.
  function buildDateList() {
    if (DATES) return DATES;
    DATES = [];
    var d = new Date(2021, 6, 14);          // 2021-07-14 (month is 0-based)
    var end = new Date(2026, 4, 20);        // 2026-05-20
    while (d <= end) {
      var y = d.getFullYear();
      var m = String(d.getMonth() + 1).padStart(2, "0");
      var day = String(d.getDate()).padStart(2, "0");
      DATES.push({ iso: y + "-" + m + "-" + day, compact: "" + y + m + day });
      d.setDate(d.getDate() + 7);
    }
    return DATES;
  }

  // ---------------------------------------------------------------------------
  // CDI -> drought class (mirrors engine getOfficialCDIColor thresholds)
  // ---------------------------------------------------------------------------
  function classify(val, mn, mx) {
    if (val == null || isNaN(val)) return { label: "No data", bg: "transparent", fg: "inherit" };
    var n = (val - mn) / (mx - mn || 1);
    if (n < 0.12) return { label: "Exceptional (D4)", bg: "#730000", fg: "#fff" };
    if (n < 0.25) return { label: "Extreme (D3)",     bg: "#e60000", fg: "#fff" };
    if (n < 0.40) return { label: "Severe (D2)",      bg: "#ffaa00", fg: "#1f1d1c" };
    if (n < 0.55) return { label: "Moderate (D1)",    bg: "#ffff00", fg: "#1f1d1c" };
    if (n < 0.75) return { label: "Abnormally Dry (D0)", bg: "#aaffaa", fg: "#1f1d1c" };
    return { label: "No Drought", bg: "#38a800", fg: "#fff" };
  }

  // ---------------------------------------------------------------------------
  // Build a map on a given pair of canvases pointing at a product.
  // Returns the engine handle (with .init()).
  // ---------------------------------------------------------------------------
  function buildMap(rasterCanvas, vectorCanvas, productKey, opts) {
    opts = opts || {};
    var prod = PRODUCTS[productKey] || PRODUCTS.cdi;
    var cmap = (window.IDM_COLORMAPS ? window.IDM_COLORMAPS.forProduct(productKey) : null);
    return window.createDroughtMap({
      rasterCanvas: rasterCanvas,
      vectorCanvas: vectorCanvas,
      interactive: opts.interactive !== false,
      size: opts.size || 840,
      colormap: cmap,
      paths: {
        current: prod.file,
        stateGrid: PATHS.stateGrid,
        mainland: PATHS.mainland,
        stateVectors: PATHS.stateVectors
      },
      framePath: function (d) { return "./data/Drough_TS/CDI_" + d + ".txt"; },
      controls: opts.controls || {}
    });
  }

  // ---------------------------------------------------------------------------
  // Populate a <select> with the state list (after a map's data has loaded)
  // and wire it to zoomToStateBoundingBox. Union Territories are excluded from the
  // dropdown (states only).
  // ---------------------------------------------------------------------------
  // state_ids that are Union Territories (per state_vector_boundaries.json):
  // Andaman & Nicobar (2), Chandigarh (7), Dadara & Nagar Havelli (9),
  // Daman & Diu (10), Jammu & Kashmir (15), Lakshadweep (19), NCT of Delhi (26),
  // Puducherry (27).
  var UT_IDS = { 2: 1, 7: 1, 9: 1, 10: 1, 15: 1, 19: 1, 26: 1, 27: 1 };

  function wireStateSelect(selectEl, map) {
    if (!selectEl || !map) return;
    var byId = {};
    map.state.stateVectorBoundaries.forEach(function (p) {
      if (p && typeof p.state_id !== "undefined" && p.name) byId[p.state_id] = p.name;
    });
    Object.keys(byId)
      .map(function (id) { return { id: parseInt(id, 10), name: byId[id] }; })
      .filter(function (it) { return !UT_IDS[it.id]; })   // states only — drop UTs
      .sort(function (a, b) { return a.name.localeCompare(b.name); })
      .forEach(function (it) {
        var o = document.createElement("option");
        o.value = String(it.id); o.textContent = it.name;
        selectEl.appendChild(o);
      });
    selectEl.addEventListener("change", function () {
      var id = parseInt(selectEl.value, 10);
      if (id) map.zoomToStateBoundingBox(id);
    });
  }

  // ---------------------------------------------------------------------------
  // Mirror engine hover state into an HTML readout panel (read-only polling).
  // ---------------------------------------------------------------------------
  function wireReadout(map, els) {
    if (!els || !els.lat) return;
    function paint() {
      var hc = map.state.hoverCoords;
      if (hc) {
        els.lat.textContent = hc.lat != null ? hc.lat.toFixed(3) + "°N" : "—";
        els.lng.textContent = hc.lng != null ? hc.lng.toFixed(3) + "°E" : "—";
        els.val.textContent = hc.val != null ? hc.val.toFixed(3) : "No data";
        if (els.cls) {
          var cmap = map.state.colormap || (window.IDM_COLORMAPS && window.IDM_COLORMAPS.CDI);
          var res = (cmap && window.IDM_COLORMAPS) ? window.IDM_COLORMAPS.evaluate(cmap, hc.val) : { color: "#fff", cls: "—" };
          els.cls.textContent = res.cls || "—";
          els.cls.style.background = res.color || "transparent";
          // pick legible text colour
          var rgb = window.IDM_COLORMAPS ? window.IDM_COLORMAPS.hexToRgb(res.color) : null;
          var dark = rgb ? (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) < 140 : false;
          els.cls.style.color = dark ? "#fff" : "#1f1d1c";
        }
      } else {
        els.lat.textContent = els.lng.textContent = els.val.textContent = "—";
        if (els.cls) { els.cls.textContent = "—"; els.cls.style.background = "transparent"; els.cls.style.color = "inherit"; }
      }
      if (els.state) els.state.textContent = map.state.hoveredStateName || "—";
      if (els.stateBig) els.stateBig.textContent = map.state.hoveredStateName || "—";
    }
    window.addEventListener("mousemove", paint);
    map.canvases.vector.addEventListener("mouseleave", paint);
    setInterval(paint, 250);
  }

  // ---------------------------------------------------------------------------
  // Populate a product <select>.
  // ---------------------------------------------------------------------------
  function fillProductSelect(selectEl, selected) {
    if (!selectEl) return;
    Object.keys(PRODUCTS).forEach(function (key) {
      var o = document.createElement("option");
      o.value = key; o.textContent = PRODUCTS[key].label;
      if (key === selected) o.selected = true;
      selectEl.appendChild(o);
    });
  }

  // ---------------------------------------------------------------------------
  // Populate a date <select> with the weekly CDI dates (newest first).
  // ---------------------------------------------------------------------------
  function fillDateSelect(selectEl, selectedIso) {
    if (!selectEl) return;
    var list = buildDateList().slice().reverse();
    list.forEach(function (d, i) {
      var o = document.createElement("option");
      o.value = d.iso; o.textContent = d.iso;
      if (d.iso === selectedIso || (!selectedIso && i === 0)) o.selected = true;
      selectEl.appendChild(o);
    });
  }

  function hideLoader(panelSel) {
    var l = document.querySelector(panelSel || "#idm-map-loading");
    if (l) l.classList.add("idm-hidden");
  }

  // ---------------------------------------------------------------------------
  // PUBLIC: mount a single interactive map with optional controls.
  //   cfg = {
  //     raster, vector,            (canvas elements or ids)
  //     product: "cdi",
  //     stateSelect, productSelect, dateSelect, resetBtn, isolateChk,
  //     readout: {lat,lng,val,state,cls},
  //     animControls: {btnStart,btnStop,startDate,endDate,fps},
  //     loader: "#idm-map-loading"
  //   }
  // ---------------------------------------------------------------------------
  function el(x) { return typeof x === "string" ? document.getElementById(x) : x; }

  async function mountMap(cfg) {
    var raster = el(cfg.raster), vector = el(cfg.vector);
    var productKey = cfg.product || "cdi";

    var map = buildMap(raster, vector, productKey, {
      interactive: cfg.interactive !== false,
      controls: cfg.animControls || {}
    });
    await map.init();
    hideLoader(cfg.loader);

    wireStateSelect(el(cfg.stateSelect), map);
    wireReadout(map, cfg.readout && {
      lat: el(cfg.readout.lat), lng: el(cfg.readout.lng), val: el(cfg.readout.val),
      state: el(cfg.readout.state), cls: el(cfg.readout.cls), stateBig: el(cfg.readout.stateBig)
    });

    // reset button
    var resetBtn = el(cfg.resetBtn);
    if (resetBtn) resetBtn.addEventListener("click", function () {
      map.resetZoom();
      if (el(cfg.stateSelect)) el(cfg.stateSelect).value = "";
    });

    // isolate toggle
    var iso = el(cfg.isolateChk);
    if (iso) {
      iso.checked = !!map.state.isolateFocusedStateBoundaries;
      iso.addEventListener("change", function () {
        map.state.isolateFocusedStateBoundaries = iso.checked;
        map.renderStaticMap();
      });
    }

    // product switcher — reload a different grid into the SAME map.
    // We rebuild the CDI matrix in place by re-querying via a fresh engine load.
    var prodSel = el(cfg.productSelect);
    if (prodSel) {
      prodSel.addEventListener("change", function () {
        switchProduct(map, prodSel.value);
      });
    }

    // date switcher (CDI time series)
    var dateSel = el(cfg.dateSelect);
    if (dateSel) {
      dateSel.addEventListener("change", function () {
        var iso = dateSel.value;
        var compact = iso.replace(/-/g, "");
        map.loadCDIDataForDate(compact).then(function (ok) {
          if (ok) { map.renderStaticMap(); map.renderDynamicHUD(); }
        });
      });
    }

    // zoom-mode toggle button (rectangle-zoom <-> click-state-zoom)
    var zoomBtn = el(cfg.zoomModeBtn);
    if (zoomBtn) {
      function syncZoomBtn() {
        var mode = map.getZoomMode();
        if (mode === "rect") {
          zoomBtn.classList.add("is-rect");
          zoomBtn.innerHTML = "&#9783; Rectangle zoom: ON";
          zoomBtn.setAttribute("aria-pressed", "true");
          map.canvases.vector.style.cursor = "crosshair";
        } else {
          zoomBtn.classList.remove("is-rect");
          zoomBtn.innerHTML = "&#9783; Rectangle zoom: OFF";
          zoomBtn.setAttribute("aria-pressed", "false");
          map.canvases.vector.style.cursor = "pointer";
        }
      }
      zoomBtn.addEventListener("click", function () {
        map.setZoomMode(map.getZoomMode() === "rect" ? "state" : "rect");
        syncZoomBtn();
      });
      syncZoomBtn();
    }

    // interpolation-factor slider (engine state.INTERP)
    var interpSlider = el(cfg.interpSlider);
    if (interpSlider) {
      var interpVal = el(cfg.interpValue);
      interpSlider.value = String(map.getInterp());
      if (interpVal) interpVal.textContent = String(map.getInterp());
      var applyInterp = function () {
        if (interpVal) interpVal.textContent = interpSlider.value;
        map.setInterp(interpSlider.value);
      };
      interpSlider.addEventListener("input", function () { if (interpVal) interpVal.textContent = interpSlider.value; });
      interpSlider.addEventListener("change", applyInterp);
    }

    // greyscale toggle (checkbox)
    var grayChk = el(cfg.grayscaleChk);
    if (grayChk) {
      grayChk.checked = !!map.getGrayscale();
      grayChk.addEventListener("change", function () { map.setGrayscale(grayChk.checked); });
    }

    // download-PNG button
    var dlBtn = el(cfg.downloadBtn);
    if (dlBtn) {
      dlBtn.addEventListener("click", function () {
        var url = map.toPNGDataURL(false); // data layer only (no HUD overlay) for a clean map
        var a = document.createElement("a");
        a.href = url;
        a.download = (cfg.downloadName || "india-drought-map") + ".png";
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
      });
    }

    return map;
  }

  // Reload an arbitrary product grid into an existing map without rebuilding it.
  // Uses the engine's own loader path by temporarily pointing framePath logic at
  // the product file via loadCDIDataForDate-style query. Simplest: query here and
  // poke the matrix using the same math the engine uses.
  async function switchProduct(map, productKey) {
    var prod = PRODUCTS[productKey] || PRODUCTS.cdi;
    // switch to the matching exact WCL colour-map for this product
    if (window.IDM_COLORMAPS) map.state.colormap = window.IDM_COLORMAPS.forProduct(productKey);
    var q = "SELECT CAST([0] AS FLOAT) AS lat, CAST([1] AS FLOAT) AS lng, CAST([2] AS FLOAT) AS val " +
            "FROM csv('" + prod.file + "', {headers:false, separator:' '}) " +
            "WHERE [0] != 'NaN' AND [1] != 'NaN' AND [2] != 'NaN'";
    var rows;
    try { rows = await window.alasql.promise(q); } catch (e) { console.error(e); return; }
    if (!rows || !rows.length) return;
    var st = map.state;
    for (var r = 0; r < st.totalRows; r++) st.gridCDI[r].fill(null);
    rows.forEach(function (p) {
      var rr = Math.floor((st.base.long_N - p.lat) / st.dataStep);
      var cc = Math.floor((p.lng - st.base.lat_W) / st.dataStep);
      if (rr >= 0 && rr < st.totalRows && cc >= 0 && cc < st.totalCols) st.gridCDI[rr][cc] = p.val;
    });
    st.minVal = Math.min.apply(null, rows.map(function (d) { return d.val; }));
    st.maxVal = Math.max.apply(null, rows.map(function (d) { return d.val; }));
    map.renderStaticMap(); map.renderDynamicHUD();
  }

  // expose
  window.IDM = {
    PRODUCTS: PRODUCTS,
    buildDateList: buildDateList,
    classify: classify,
    buildMap: buildMap,
    mountMap: mountMap,
    switchProduct: switchProduct,
    wireStateSelect: wireStateSelect,
    wireReadout: wireReadout,
    fillProductSelect: fillProductSelect,
    fillDateSelect: fillDateSelect
  };
})();
