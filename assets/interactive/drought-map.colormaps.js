/* =============================================================================
 * drought-map.colormaps.js
 * -----------------------------------------------------------------------------
 * EXACT colour-maps transcribed from the old WCL production site
 * (github.com/wcl-iitgn/IDM), src/components/Legend*.jsx. Those maps were
 * pre-rendered raster images, so the Legend components ARE the authoritative
 * value -> colour specification. Each map below is a list of {upTo, color}
 * bands evaluated low-to-high: a value v gets the colour of the FIRST band whose
 * `upTo` threshold it is <= ; values above the last threshold get `aboveColor`.
 *
 * Sources:
 *   Legend.jsx  (CDI / drought)      6 colours, thresholds -3.0 -2.0 -1.6 -1.3 -0.8 -0.5
 *   Legend4.jsx (SPI / SRI / SSMI)  12 colours, thresholds -3.0 ... 3.0 (diverging)
 *   Legend3.jsx (streamflow %ile)   12 colours, percentile 0 2 5 10 20 30 70 80 90 95 98 100
 *   Legend2.jsx (drought persist)    2 colours (recover / persist)
 *
 * CSS named colours resolved per the W3C spec:
 *   brown=#A52A2A red=#FF0000 orange=#FFA500 yellow=#FFFF00 white=#FFFFFF black=#000000
 * and rgb(252,214,148)=#FCD394.
 * ========================================================================== */
(function () {
  "use strict";

  // ---- CDI / drought (Legend.jsx) -------------------------------------------
  // Legend lists, worst -> best: brown(-3) red(-2) orange(-1.6) rgb(252,214,148)(-1.3)
  // yellow(-0.8) white(-0.5). i.e. the number under each swatch is the UPPER edge of
  // that (dry) band. Anything wetter than -0.5 is "Normal" = white.
  var CDI = {
    label: "CDI / Drought",
    bands: [
      { upTo: -2.0, color: "#A52A2A", cls: "Exceptional (D4)" }, // <= -2.0 ... brown  (legend "-3.0" = darkest)
      { upTo: -1.6, color: "#FF0000", cls: "Extreme (D3)" },     // (-2.0, -1.6]  red
      { upTo: -1.3, color: "#FFA500", cls: "Severe (D2)" },      // (-1.6, -1.3]  orange
      { upTo: -0.8, color: "#FCD394", cls: "Moderate (D1)" },    // (-1.3, -0.8]  rgb(252,214,148)
      { upTo: -0.5, color: "#FFFF00", cls: "Abnormal (D0)" }     // (-0.8, -0.5]  yellow
    ],
    aboveColor: "#FFFFFF",      // > -0.5  Normal (white)
    aboveCls: "Normal",
    // the most-extreme bucket: <= -2.0 already brown above; legend's -3.0 row is the
    // same brown extended, so nothing extra needed.
    nodataColor: null
  };

  // ---- SPI / SRI / SSMI (Legend4.jsx) ---------------------------------------
  // 12 swatches, driest -> wettest, with the 12 numbers as the band UPPER edges:
  // black(-3) brown(-2) red(-1.6) orange(-1.3) yellow(-0.8) white(-0.5)
  // #B9F96E(0.5) #B3D16E(0.8) #3CBC3D(1.3) #009E1E(1.6) #6370F8(2.0) #6370F8(3.0)
  var DIVERGING = {
    label: "Standardised Index",
    bands: [
      { upTo: -2.0, color: "#000000", cls: "<= -2.0" },
      { upTo: -1.6, color: "#A52A2A", cls: "-2.0 to -1.6" },
      { upTo: -1.3, color: "#FF0000", cls: "-1.6 to -1.3" },
      { upTo: -0.8, color: "#FFA500", cls: "-1.3 to -0.8" },
      { upTo: -0.5, color: "#FFFF00", cls: "-0.8 to -0.5" },
      { upTo:  0.5, color: "#FFFFFF", cls: "-0.5 to 0.5 (near normal)" },
      { upTo:  0.8, color: "#B9F96E", cls: "0.5 to 0.8" },
      { upTo:  1.3, color: "#B3D16E", cls: "0.8 to 1.3" },
      { upTo:  1.6, color: "#3CBC3D", cls: "1.3 to 1.6" },
      { upTo:  2.0, color: "#009E1E", cls: "1.6 to 2.0" },
      { upTo:  3.0, color: "#6370F8", cls: "2.0 to 3.0" }
    ],
    aboveColor: "#6370F8",     // > 3.0
    aboveCls: "> 3.0 (very wet)",
    nodataColor: null
  };

  // ---- Streamflow percentile (Legend3.jsx) ----------------------------------
  // 12 swatches across percentile breakpoints 0 2 5 10 20 30 70 80 90 95 98 100
  var STREAMFLOW = {
    label: "Streamflow Percentile",
    bands: [
      { upTo: 2,   color: "#000000", cls: "0-2 (lowest)" },
      { upTo: 5,   color: "#A52A2A", cls: "2-5" },
      { upTo: 10,  color: "#FF0000", cls: "5-10" },
      { upTo: 20,  color: "#FFA500", cls: "10-20" },
      { upTo: 30,  color: "#FFFF00", cls: "20-30" },
      { upTo: 70,  color: "#FFFFFF", cls: "30-70 (normal)" },
      { upTo: 80,  color: "#B9F96E", cls: "70-80" },
      { upTo: 90,  color: "#B3D16E", cls: "80-90" },
      { upTo: 95,  color: "#3CBC3D", cls: "90-95" },
      { upTo: 98,  color: "#009E1E", cls: "95-98" },
      { upTo: 100, color: "#6370F8", cls: "98-100 (highest)" }
    ],
    aboveColor: "#6370F8",
    aboveCls: "100",
    nodataColor: null
  };

  // ---- Drought persistence (Legend2.jsx) ------------------------------------
  // Codes per Pranav's Notes: 0 no/no, 1 no->yes, 2 yes->no, 3 yes->yes.
  // Old legend has just two outcomes: Drought Recovers (#99CCFF), Drought Persists (#FF9999).
  // Map: recovers = code 2; persists = code 3; codes 0/1 (no current drought) -> white.
  var PERSIST = {
    label: "Drought Outlook",
    discrete: {
      0: { color: "#FFFFFF", cls: "No drought" },
      1: { color: "#FFFFFF", cls: "No current drought" },
      2: { color: "#99CCFF", cls: "Drought Recovers" },
      3: { color: "#FF9999", cls: "Drought Persists" }
    },
    nodataColor: null
  };

  // ---- Forecast PRECIPITATION magnitude (mm) --------------------------------
  // NOTE: WCL's legend set had no physical-magnitude colormap; this is a
  // conventional sequential "Blues" ramp with thresholds from the data range.
  // Swap thresholds/colours here if WCL provides an official precip colormap.
  var PRECIP = {
    label: "Precipitation (mm)",
    bands: [
      { upTo: 1,   color: "#FFFFFF", cls: "0–1 mm" },
      { upTo: 5,   color: "#DEEBF7", cls: "1–5" },
      { upTo: 10,  color: "#C6DBEF", cls: "5–10" },
      { upTo: 25,  color: "#9ECAE1", cls: "10–25" },
      { upTo: 50,  color: "#6BAED6", cls: "25–50" },
      { upTo: 75,  color: "#4292C6", cls: "50–75" },
      { upTo: 100, color: "#2171B5", cls: "75–100" },
      { upTo: 150, color: "#08519C", cls: "100–150" },
      { upTo: 200, color: "#08306B", cls: "150–200" }
    ],
    aboveColor: "#08306B", aboveCls: "> 200 mm", nodataColor: null
  };

  // ---- Forecast RUNOFF magnitude (mm) ---------------------------------------
  var RUNOFF = {
    label: "Runoff (mm)",
    bands: [
      { upTo: 0.5, color: "#FFFFFF", cls: "0–0.5 mm" },
      { upTo: 1,   color: "#E0F3DB", cls: "0.5–1" },
      { upTo: 2,   color: "#CCEBC5", cls: "1–2" },
      { upTo: 5,   color: "#A8DDB5", cls: "2–5" },
      { upTo: 10,  color: "#7BCCC4", cls: "5–10" },
      { upTo: 20,  color: "#4EB3D3", cls: "10–20" },
      { upTo: 40,  color: "#2B8CBE", cls: "20–40" },
      { upTo: 80,  color: "#0868AC", cls: "40–80" }
    ],
    aboveColor: "#084081", aboveCls: "> 80 mm", nodataColor: null
  };

  // ---- Forecast SOIL MOISTURE magnitude (v/v) -------------------------------
  // Dry (brown) -> wet (teal/green), BrBG-style.
  var SOILMOIST = {
    label: "Soil Moisture (v/v)",
    bands: [
      { upTo: 0.05, color: "#8C510A", cls: "0.00–0.05 (dry)" },
      { upTo: 0.10, color: "#BF812D", cls: "0.05–0.10" },
      { upTo: 0.15, color: "#DFC27D", cls: "0.10–0.15" },
      { upTo: 0.20, color: "#F6E8C3", cls: "0.15–0.20" },
      { upTo: 0.25, color: "#C7EAE5", cls: "0.20–0.25" },
      { upTo: 0.30, color: "#80CDC1", cls: "0.25–0.30" },
      { upTo: 0.40, color: "#35978F", cls: "0.30–0.40" },
      { upTo: 0.50, color: "#01665E", cls: "0.40–0.50" }
    ],
    aboveColor: "#003C30", aboveCls: "> 0.50 (wet)", nodataColor: null
  };

  // ---------------------------------------------------------------------------
  // For band maps: first band whose upTo >= v; else aboveColor.
  // For discrete maps: exact integer lookup.
  // ---------------------------------------------------------------------------
  function evaluate(cmap, v) {
    if (v == null || isNaN(v)) return { color: cmap.nodataColor, cls: "No data" };
    if (cmap.discrete) {
      var key = Math.round(v);
      return cmap.discrete[key] || { color: cmap.nodataColor, cls: "—" };
    }
    for (var i = 0; i < cmap.bands.length; i++) {
      if (v <= cmap.bands[i].upTo) return { color: cmap.bands[i].color, cls: cmap.bands[i].cls };
    }
    return { color: cmap.aboveColor, cls: cmap.aboveCls };
  }

  // Convert "#RRGGBB" to [r,g,b]
  function hexToRgb(hex) {
    if (!hex) return null;
    var h = hex.replace("#", "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  // Map a product key (from IDM.PRODUCTS) to the right colormap.
  function forProduct(key) {
    if (!key) return CDI;
    if (key.indexOf("pmag") === 0) return PRECIP;
    if (key.indexOf("rmag") === 0) return RUNOFF;
    if (key.indexOf("smmag") === 0) return SOILMOIST;
    if (key.indexOf("spi") === 0 || key.indexOf("sri") === 0 || key.indexOf("ssmi") === 0) return DIVERGING;
    if (key.indexOf("stream") === 0) return STREAMFLOW;
    if (key.indexOf("persist") === 0) return PERSIST;
    // cdi, fcdi7/15/30 (forecast CDI) all use the CDI drought scale
    return CDI;
  }

  window.IDM_COLORMAPS = {
    CDI: CDI,
    DIVERGING: DIVERGING,
    STREAMFLOW: STREAMFLOW,
    PERSIST: PERSIST,
    PRECIP: PRECIP,
    RUNOFF: RUNOFF,
    SOILMOIST: SOILMOIST,
    evaluate: evaluate,
    hexToRgb: hexToRgb,
    forProduct: forProduct
  };
})();
