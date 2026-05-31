/* =============================================================================
 * idm-ai.js — shared AI layer for the India Drought Monitor
 * -----------------------------------------------------------------------------
 * Powers two features, both pure front-end:
 *   1) Weekly national SUMMARY generation (Summary page)
 *   2) A CHATBOT that answers questions about the drought + Hydrological Outlook
 *      data by writing AlaSQL queries against in-browser tables.
 *
 * LLM endpoint: an Ollama server on the LAN, using the /api/generate route.
 *   curl http://HOST:11434/api/generate -d '{"model":"qwen3.5:4b",
 *        "prompt":"...","stream":false,"think":false}'
 * We always use NON-THINKING mode ("think": false).
 *
 * The data the chatbot can query is loaded into AlaSQL as three tables; the LLM
 * is given the FULL SCHEMA (column names, types, meaning) in its system prompt —
 * never the data itself — and asked to emit a single read-only SELECT.
 * ========================================================================== */
(function () {
  "use strict";

  // ---- configuration ---------------------------------------------------------
  var CFG = {
    // The LAN Ollama endpoint. Override at runtime via IDM_AI.configure({...}).
    apiUrl: "http://10.0.60.193:11434/api/generate",
    model: "qwen3.5:4b",
    // data file locations (relative to the site root)
    paths: {
      timeseries: "data/India_Drought_Area_Timeseries.txt",
      stateGrid: "states_with_boundaries.csv",
      stateVectors: "state_vector_boundaries.json",
      currentCDI: "data/Current_CDI.txt",
      hydroStats: "assets/ai/hydro-stats.json",
      cachedSummary: "data/summary_latest.txt"
    }
  };

  function configure(opts) {
    if (!opts) return;
    if (opts.apiUrl) CFG.apiUrl = opts.apiUrl;
    if (opts.model) CFG.model = opts.model;
    if (opts.paths) Object.assign(CFG.paths, opts.paths);
  }

  // ---- low-level LLM call (Ollama /api/generate, non-thinking) ---------------
  // Returns the model's text. Throws on network/HTTP error.
  async function llm(prompt, opts) {
    opts = opts || {};
    var body = {
      model: CFG.model,
      prompt: prompt,
      stream: false,
      think: false,                 // NON-THINKING mode (as required)
      options: {
        temperature: opts.temperature != null ? opts.temperature : 0.7,
        top_p: 0.8,
        top_k: 20,
        num_predict: opts.maxTokens || 512
      }
    };
    if (opts.system) body.system = opts.system;
    var r = await fetch(CFG.apiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error("LLM HTTP " + r.status + ": " + (await r.text()).slice(0, 200));
    var j = await r.json();
    var txt = (j && j.response != null) ? String(j.response) : "";
    // Safety: if a stray think block slips through, keep only the part after it.
    if (txt.indexOf("</think>") !== -1) txt = txt.split("</think>").pop();
    return txt.trim();
  }

  // ===========================================================================
  // DATA LAYER — load everything into AlaSQL tables
  // ===========================================================================
  var _loaded = null;          // promise, so we only load once
  var META = { latestDate: null, latestCompact: null, hydroMonth: null };

  async function fetchText(p) {
    var r = await fetch(p);
    if (!r.ok) throw new Error("fetch " + p + " -> " + r.status);
    return await r.text();
  }

  // Classify a CDI value into a drought class using the SAME absolute thresholds as the
  // map engine and the WCL legend (NOT a per-week min/max normalisation). Values wetter
  // than -0.5 are Normal; drier values fall into D0..D4.
  function classify(v) {
    if (v > -0.5) return "None";
    if (v > -0.8) return "D0";
    if (v > -1.3) return "D1";
    if (v > -1.6) return "D2";
    if (v > -2.0) return "D3";
    return "D4";
  }

  // Build the per-state class table for the latest CDI week (same method the
  // data-tables page uses).
  async function buildStateLatest(ID2NAME) {
    var sgText = await fetchText(CFG.paths.stateGrid);
    var lines = sgText.trim().split("\n");
    var header = lines[0].split(",").map(function (s) { return s.trim(); });
    var li = header.indexOf("lat"), gi = header.indexOf("lng"), vi = header.indexOf("value");
    var cells = [];
    for (var i = 1; i < lines.length; i++) {
      var c = lines[i].split(",");
      var sid = parseInt(c[vi], 10);
      if (sid >= 2) cells.push({ lat: +c[li], lng: +c[gi], sid: sid });
    }
    var cdiText = await fetchText(CFG.paths.currentCDI);
    var cdi = cdiText.trim().split("\n").map(function (l) {
      var p = l.trim().split(/\s+/); return { lat: +p[0], lng: +p[1], val: parseFloat(p[2]) };
    }).filter(function (r) { return isFinite(r.val); });
    var idx = new Map();
    cdi.forEach(function (r) { idx.set(r.lat.toFixed(3) + "," + r.lng.toFixed(3), r.val); });
    var deltas = [0, 0.0625, -0.0625, 0.125, -0.125];
    function nearest(lat, lng) {
      for (var a = 0; a < deltas.length; a++) for (var b = 0; b < deltas.length; b++) {
        var v = idx.get((lat + deltas[a]).toFixed(3) + "," + (lng + deltas[b]).toFixed(3));
        if (v !== undefined) return v;
      }
      return null;
    }
    var tally = {};
    cells.forEach(function (c) {
      var v = nearest(c.lat, c.lng); if (v === null) return;
      var t = tally[c.sid] || (tally[c.sid] = { None: 0, D0: 0, D1: 0, D2: 0, D3: 0, D4: 0, tot: 0 });
      t[classify(v)]++; t.tot++;
    });
    var rows = [];
    Object.keys(tally).forEach(function (sid) {
      var t = tally[sid]; if (t.tot < 8) return;
      function pc(k) { return +(100 * t[k] / t.tot).toFixed(1); }
      var row = {
        state: ID2NAME[sid] || ("State " + sid),
        none_pct: pc("None"), d0_pct: pc("D0"), d1_pct: pc("D1"),
        d2_pct: pc("D2"), d3_pct: pc("D3"), d4_pct: pc("D4")
      };
      row.drought_pct = +(100 - row.none_pct).toFixed(1);
      rows.push(row);
    });
    return rows;
  }

  function loadData() {
    if (_loaded) return _loaded;
    _loaded = (async function () {
      if (typeof alasql === "undefined") throw new Error("AlaSQL not loaded");

      // 1) national drought-area time series
      var tsText = await fetchText(CFG.paths.timeseries);
      var tsRows = tsText.trim().split("\n").map(function (line) {
        var p = line.trim().split(/\s+/).map(Number);
        return {
          year: p[0], month: p[1], day: p[2],
          date: p[0] + "-" + String(p[1]).padStart(2, "0") + "-" + String(p[2]).padStart(2, "0"),
          normal_pct: p[3], d0_pct: p[4], d1_pct: p[5], d2_pct: p[6], d3_pct: p[7], d4_pct: p[8]
        };
      });
      META.latestDate = tsRows[tsRows.length - 1].date;
      META.latestCompact = META.latestDate.replace(/-/g, "");

      // 2) state names
      var names = JSON.parse(await fetchText(CFG.paths.stateVectors));
      var ID2NAME = {};
      names.forEach(function (p) { ID2NAME[Number(p.state_id)] = p.name; });

      // 3) per-state latest week
      var stateRows = await buildStateLatest(ID2NAME);

      // 4) hydrological outlook national means
      var hydro = JSON.parse(await fetchText(CFG.paths.hydroStats));
      META.hydroMonth = hydro.month_label;
      var hydroRows = hydro.national_means.map(function (r) {
        return {
          parameter: r.parameter, kind: r.kind,
          current_month: r.current_month, forecast_month: r.forecast_month,
          prev_1: r.prev_1, prev_2: r.prev_2, prev_3: r.prev_3, prev_4: r.prev_4,
          last_year_same_month: r.last_year_same_month, driest: r.driest, wettest: r.wettest
        };
      });

      // register tables
      ["drought_timeseries", "drought_state_latest", "hydro_outlook"].forEach(function (t) {
        try { alasql("DROP TABLE IF EXISTS " + t); } catch (e) {}
        alasql("CREATE TABLE " + t);
      });
      alasql.tables.drought_timeseries.data = tsRows;
      alasql.tables.drought_state_latest.data = stateRows;
      alasql.tables.hydro_outlook.data = hydroRows;

      return { ts: tsRows, states: stateRows, hydro: hydroRows };
    })();
    return _loaded;
  }

  // ===========================================================================
  // SCHEMA — the full description handed to the LLM (no data, just structure)
  // ===========================================================================
  function schemaDoc() {
    return [
      "You can query an in-browser SQL database (AlaSQL dialect) with THREE tables.",
      "",
      "TABLE drought_timeseries  -- national weekly Combined Drought Index (CDI) area, Jul 2021 to present, one row per week",
      "  year        INT",
      "  month       INT",
      "  day         INT",
      "  date        TEXT    -- 'YYYY-MM-DD' (the week-ending date)",
      "  normal_pct  FLOAT   -- % of India area in NO drought that week",
      "  d0_pct      FLOAT   -- % area in D0 (Abnormally Dry) OR WORSE   [CUMULATIVE]",
      "  d1_pct      FLOAT   -- % area in D1 (Moderate) or worse         [CUMULATIVE]",
      "  d2_pct      FLOAT   -- % area in D2 (Severe) or worse           [CUMULATIVE]",
      "  d3_pct      FLOAT   -- % area in D3 (Extreme) or worse          [CUMULATIVE]",
      "  d4_pct      FLOAT   -- % area in D4 (Exceptional)               [CUMULATIVE]",
      "  -- NOTE: d0..d4 are CUMULATIVE ('or worse'); normal_pct + d0_pct = 100.",
      "  -- The latest week is the row with MAX(date).",
      "",
      "TABLE drought_state_latest  -- per state/UT drought breakdown for the MOST RECENT week only",
      "  state        TEXT    -- state or UT name",
      "  none_pct     FLOAT   -- % of the state in no drought",
      "  d0_pct       FLOAT   -- % of the state exactly in class D0 (NOT cumulative here)",
      "  d1_pct       FLOAT   -- % exactly in D1",
      "  d2_pct       FLOAT   -- % exactly in D2",
      "  d3_pct       FLOAT   -- % exactly in D3",
      "  d4_pct       FLOAT   -- % exactly in D4",
      "  drought_pct  FLOAT   -- % of the state in ANY drought (= 100 - none_pct)",
      "  -- In THIS table the *_pct columns are per-class shares (NOT cumulative).",
      "",
      "TABLE hydro_outlook  -- India Hydrological Outlook national means, latest month (" + (META.hydroMonth || "") + ")",
      "  parameter             TEXT   -- one of: 'Rainfall', 'Surface Air Temperature',",
      "                               -- 'Relative Wetness (Soil Moisture)', 'Total Runoff', 'Evapotranspiration'",
      "  kind                  TEXT   -- 'percentile' (0-100, ~50 normal) or 'anomaly_degC' or 'anomaly_pct' (0 = normal, negative = below normal)",
      "  current_month         FLOAT  -- national mean for the latest observed month",
      "  forecast_month        FLOAT  -- national mean for the one-month-ahead forecast",
      "  prev_1                FLOAT  -- one month before current",
      "  prev_2                FLOAT",
      "  prev_3                FLOAT",
      "  prev_4                FLOAT  -- four months before current",
      "  last_year_same_month  FLOAT  -- same calendar month, previous year",
      "  driest                FLOAT  -- historically driest/lowest analogue month",
      "  wettest               FLOAT  -- historically wettest/highest analogue month",
      "  -- Only Rainfall is a 0-100 percentile; the others are anomalies where negative = below normal.",
      "",
      "Rules for writing SQL:",
      "- Output a SINGLE read-only SELECT statement. No INSERT/UPDATE/DELETE/DROP/CREATE.",
      "- Use only the tables and columns above. Use exact column names.",
      "- NATIONAL questions about how much of India / the country is in drought, or trends over time,",
      "  MUST use drought_timeseries (one row = whole of India). For the current national figure,",
      "  filter to the latest week: ... ORDER BY date DESC LIMIT 1. 'In drought' = d0_pct; 'not in",
      "  drought'/normal = normal_pct. NEVER read a national figure from drought_state_latest.",
      "- PER-STATE questions (a named state, 'which states', rankings across states) use",
      "  drought_state_latest. Each row is ONE state; never present a single state's value as national.",
      "- Rainfall / temperature / soil-moisture / runoff / ET questions use hydro_outlook (match",
      "  the parameter name exactly)."
    ].join("\n");
  }

  // ===========================================================================
  // SUMMARY GENERATION
  // ===========================================================================
  // Build a compact NUMERIC context for the weekly national summary.
  function buildSummaryContext(data) {
    var ts = data.ts, states = data.states;
    var cur = ts[ts.length - 1], prev = ts[ts.length - 2] || cur, monthAgo = ts[ts.length - 5] || ts[0];
    function p(x) { return (x == null ? "n/a" : x.toFixed(1) + "%"); }
    var worst = states.slice().sort(function (a, b) { return b.drought_pct - a.drought_pct; }).slice(0, 6);
    var best = states.slice().sort(function (a, b) { return a.drought_pct - b.drought_pct; }).slice(0, 4);
    var delta = cur.d0_pct - prev.d0_pct;
    var trend = delta > 0.3 ? "expanded" : (delta < -0.3 ? "contracted" : "held roughly steady");
    var lines = [];
    lines.push("Week ending: " + cur.date);
    lines.push("National area by drought class (cumulative, % of India):");
    lines.push("  Normal: " + p(cur.normal_pct) + "; D0+: " + p(cur.d0_pct) + "; D1+: " + p(cur.d1_pct) +
               "; D2+: " + p(cur.d2_pct) + "; D3+: " + p(cur.d3_pct) + "; D4: " + p(cur.d4_pct));
    lines.push("Total drought area (D0+): this week " + p(cur.d0_pct) + ", last week " + p(prev.d0_pct) +
               ", ~1 month ago " + p(monthAgo.d0_pct) + " => drought " + trend +
               " (" + (delta >= 0 ? "+" : "") + delta.toFixed(1) + " pts week-on-week).");
    lines.push("Most-affected states (by % area in any drought):");
    worst.forEach(function (r) {
      lines.push("  " + r.state + ": " + p(r.drought_pct) + " in drought (D2+ " +
                 (r.d2_pct + r.d3_pct + r.d4_pct).toFixed(1) + "%, D3+ " + (r.d3_pct + r.d4_pct).toFixed(1) + "%)");
    });
    lines.push("Least-affected states: " + best.map(function (r) { return r.state + " (" + p(r.drought_pct) + ")"; }).join(", "));
    return lines.join("\n");
  }

  var SUMMARY_SYSTEM =
    "You are a hydroclimatology analyst writing the weekly national summary for the India Drought " +
    "Monitor (IDM), produced by the Water and Climate Lab, IIT Gandhinagar. The IDM uses a Combined " +
    "Drought Index (CDI) with six classes: Normal, D0 (Abnormally Dry), D1 (Moderate), D2 (Severe), " +
    "D3 (Extreme), D4 (Exceptional). Write clear, factual, neutral prose for a general audience. " +
    "CRITICAL: use ONLY the numbers in the data provided. Do NOT invent figures, place names, dates, " +
    "or trends not supported by the data.";

  // Generate the summary live via the LLM. Returns the text.
  async function generateSummary() {
    var data = await loadData();
    var ctx = buildSummaryContext(data);
    var prompt =
      "Here is this week's India Drought Monitor data:\n\n" + ctx + "\n\n" +
      "Note: the national class areas are CUMULATIVE (e.g. 'D2 or worse' already includes D3 and D4).\n\n" +
      "Write a concise national drought summary of about 150-180 words in plain paragraphs " +
      "(no headings, no bullet points, no markdown), covering: (1) a one-sentence overview of " +
      "national conditions this week, (2) the week-on-week trend, and (3) which regions/states are " +
      "most affected and any that are notably better.";
    var text = await llm(prompt, { system: SUMMARY_SYSTEM, temperature: 0.7, maxTokens: 600 });
    return { text: text, weekEnding: data.ts[data.ts.length - 1].date };
  }

  // Try the cached summary file first (production: a weekly job writes it); fall
  // back to null if absent so the caller can offer live generation.
  async function loadCachedSummary() {
    try {
      var t = await fetchText(CFG.paths.cachedSummary);
      if (t && t.trim()) return t.trim();
    } catch (e) {}
    return null;
  }

  // ===========================================================================
  // CHATBOT — text -> AlaSQL -> answer
  // ===========================================================================
  function extractSQL(text) {
    var s = (text || "").trim();
    var m = s.match(/```(?:sql)?\s*([\s\S]*?)```/i);
    if (m) s = m[1].trim();
    // first statement only
    s = s.split(";")[0].trim();
    // strip a leading "SQL:" label if the model adds one
    s = s.replace(/^sql\s*:/i, "").trim();
    return s;
  }
  function isSafeSelect(sql) {
    var low = sql.toLowerCase();
    if (low.indexOf("select") !== 0) return false;
    return !/(insert|update|delete|drop|alter|create|attach|truncate|\binto\b)/i.test(sql);
  }
  function runSQL(sql) {
    if (!isSafeSelect(sql)) throw new Error("Refused non-SELECT query");
    return alasql(sql);
  }

  var SQL_SYSTEM_BASE =
    "You translate a user's question about Indian drought and hydrological-outlook data into a " +
    "single AlaSQL SELECT query. Output ONLY the SQL statement — no explanation, no markdown fences, " +
    "no prose. Use only the tables and columns described below. Prefer simple queries.\n\n";

  var ANSWER_SYSTEM =
    "You are the India Drought Monitor assistant (Water and Climate Lab, IIT Gandhinagar). " +
    "You are given the user's question, the SQL that was run, and the exact result ROWS as JSON. " +
    "Answer ONLY from those rows. Every number in your answer must appear in the rows verbatim — " +
    "never estimate, recall, or infer figures from general knowledge. Do not convert a single " +
    "state's value into a national figure. If the rows do not contain what's needed to answer, say " +
    "you don't have that figure rather than guessing. Quote values with their correct meaning and " +
    "units (% of area, percentile, or anomaly) exactly as the column describes. Be concise; plain " +
    "prose, no markdown.";

  // Full 3-step ask: returns {answer, sql, rows, error?}. `onStage` optional callback.
  // Format prior turns as a compact transcript the model can use for follow-ups
  // ("what about Kerala?", "and last year?"). history = [{role:'user'|'assistant', content}]
  function historyBlock(history) {
    if (!history || !history.length) return "";
    var lines = history.slice(-8).map(function (m) {   // last few turns is plenty
      return (m.role === "user" ? "User" : "Assistant") + ": " + m.content;
    });
    return "Conversation so far (for context; the latest user question is below):\n" +
           lines.join("\n") + "\n\n";
  }

  // Full multi-turn ask. `history` is the prior turns (excluding the current
  // question); `onStage` is an optional progress callback.
  async function ask(question, history, onStage) {
    // tolerate the old 2-arg signature ask(question, onStage)
    if (typeof history === "function") { onStage = history; history = []; }
    history = history || [];
    await loadData();
    var sys = SQL_SYSTEM_BASE + schemaDoc();
    var hist = historyBlock(history);
    if (onStage) onStage("sql", null);

    var sql = extractSQL(await llm(
      hist + "Latest user question: " + question + "\n\n" +
      "Write a single AlaSQL SELECT that retrieves what's needed to answer the LATEST question " +
      "(resolve any references like 'that state' or 'last year' using the conversation above):",
      { system: sys, temperature: 0.2, maxTokens: 220 }));
    if (onStage) onStage("sql", sql);

    var rows;
    try {
      rows = runSQL(sql);
    } catch (e1) {
      if (onStage) onStage("retry", String(e1.message || e1));
      sql = extractSQL(await llm(
        hist + "Latest user question: " + question + "\n\nA previous attempt produced this query:\n" + sql +
        "\n\nwhich failed with: " + (e1.message || e1) + "\nFix it. Output only the corrected SELECT:",
        { system: sys, temperature: 0.2, maxTokens: 220 }));
      if (onStage) onStage("sql", sql);
      try { rows = runSQL(sql); }
      catch (e2) { return { error: "Could not run a valid query: " + (e2.message || e2), sql: sql, rows: [] }; }
    }
    if (onStage) onStage("rows", rows);

    var preview = JSON.stringify((rows || []).slice(0, 30));
    var answer = await llm(
      hist + "Latest user question: " + question + "\n\nSQL used: " + sql +
      "\n\nResult rows (JSON): " + preview + "\n\nWrite a short, direct answer to the latest question.",
      { system: ANSWER_SYSTEM, temperature: 0.7, maxTokens: 350 });
    if (onStage) onStage("answer", answer);
    return { answer: answer, sql: sql, rows: rows };
  }

  // ---- expose ---------------------------------------------------------------
  window.IDM_AI = {
    configure: configure,
    llm: llm,
    loadData: loadData,
    schemaDoc: schemaDoc,
    META: META,
    // summary
    generateSummary: generateSummary,
    loadCachedSummary: loadCachedSummary,
    buildSummaryContext: buildSummaryContext,
    // chatbot
    ask: ask,
    extractSQL: extractSQL,
    isSafeSelect: isSafeSelect,
    runSQL: runSQL
  };
})();
