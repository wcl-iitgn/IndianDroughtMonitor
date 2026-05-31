/* =============================================================================
 * query.js — "Query the Data" page controller
 * -----------------------------------------------------------------------------
 * Lets a user run read-only AlaSQL SELECTs against the three IDM tables loaded by
 * IDM_AI.loadData(). Renders the schema, example queries, and results as a table.
 * All execution is local (in-browser); only SELECT is permitted.
 * ========================================================================== */
(function () {
  "use strict";

  // Human-readable schema for the three tables (mirrors the schema given to the LLM).
  var SCHEMA = [
    {
      name: "drought_timeseries",
      desc: "National weekly drought area (Combined Drought Index), one row per week, July 2021 to present.",
      cols: [
        ["date", "TEXT", "Week-ending date, 'YYYY-MM-DD' (latest week = MAX(date))"],
        ["year, month, day", "INT", "Components of the date"],
        ["normal_pct", "FLOAT", "% of India in NO drought that week"],
        ["d0_pct", "FLOAT", "% area in D0 (Abnormally Dry) or worse — cumulative"],
        ["d1_pct", "FLOAT", "% area in D1 (Moderate) or worse — cumulative"],
        ["d2_pct", "FLOAT", "% area in D2 (Severe) or worse — cumulative"],
        ["d3_pct", "FLOAT", "% area in D3 (Extreme) or worse — cumulative"],
        ["d4_pct", "FLOAT", "% area in D4 (Exceptional) — cumulative"]
      ],
      note: "d0…d4 are cumulative (‘or worse’); normal_pct + d0_pct = 100. ‘In drought’ = d0_pct."
    },
    {
      name: "drought_state_latest",
      desc: "Per state / UT drought breakdown for the most recent week only.",
      cols: [
        ["state", "TEXT", "State or Union Territory name"],
        ["none_pct", "FLOAT", "% of the state in no drought"],
        ["d0_pct … d4_pct", "FLOAT", "% of the state in exactly that class (NOT cumulative)"],
        ["drought_pct", "FLOAT", "% of the state in ANY drought (= 100 − none_pct)"]
      ],
      note: "Per-class shares (not cumulative). Each row is ONE state — never a national total."
    },
    {
      name: "hydro_outlook",
      desc: "India Hydrological Outlook national means, latest month.",
      cols: [
        ["parameter", "TEXT", "'Rainfall', 'Surface Air Temperature', 'Relative Wetness (Soil Moisture)', 'Total Runoff', 'Evapotranspiration'"],
        ["kind", "TEXT", "'percentile' (0–100, ~50 normal) or 'anomaly_degC' / 'anomaly_pct' (0 = normal, negative = below normal)"],
        ["current_month", "FLOAT", "National mean for the latest observed month"],
        ["forecast_month", "FLOAT", "National mean for the one-month-ahead forecast"],
        ["prev_1 … prev_4", "FLOAT", "The four months before current"],
        ["last_year_same_month", "FLOAT", "Same calendar month, previous year"],
        ["driest, wettest", "FLOAT", "Historically most extreme analogue months"]
      ],
      note: "Only Rainfall is a 0–100 percentile; the others are anomalies (negative = below normal)."
    }
  ];

  var EXAMPLES = [
    {
      label: "Current national drought",
      sql: "SELECT date, normal_pct, d0_pct\nFROM drought_timeseries\nORDER BY date DESC\nLIMIT 1"
    },
    {
      label: "Five worst-affected states",
      sql: "SELECT state, drought_pct, d3_pct, d4_pct\nFROM drought_state_latest\nORDER BY drought_pct DESC\nLIMIT 5"
    },
    {
      label: "Least-affected states",
      sql: "SELECT state, drought_pct\nFROM drought_state_latest\nORDER BY drought_pct ASC\nLIMIT 5"
    },
    {
      label: "Week-on-week trend (last 8 weeks)",
      sql: "SELECT date, d0_pct, d2_pct\nFROM drought_timeseries\nORDER BY date DESC\nLIMIT 8"
    },
    {
      label: "States with >5% in extreme+ (D3+D4)",
      sql: "SELECT state, (d3_pct + d4_pct) AS d3plus\nFROM drought_state_latest\nWHERE (d3_pct + d4_pct) > 5\nORDER BY d3plus DESC"
    },
    {
      label: "Peak national drought ever recorded",
      sql: "SELECT date, d0_pct\nFROM drought_timeseries\nORDER BY d0_pct DESC\nLIMIT 1"
    },
    {
      label: "Hydrological outlook — all variables",
      sql: "SELECT parameter, kind, current_month, forecast_month\nFROM hydro_outlook"
    },
    {
      label: "Rainfall outlook detail",
      sql: "SELECT *\nFROM hydro_outlook\nWHERE parameter = 'Rainfall'"
    }
  ];

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
    });
  }

  var input, statusEl, resultEl, csvBtn, lastRows = null;

  function setStatus(s, isErr) {
    statusEl.textContent = s || "";
    statusEl.classList.toggle("is-error", !!isErr);
  }

  function renderSchema() {
    var box = el("schema-box");
    box.innerHTML = SCHEMA.map(function (t) {
      var rows = t.cols.map(function (c) {
        return "<tr><td class=\"col\">" + esc(c[0]) + "</td><td class=\"typ\">" + esc(c[1]) +
               "</td><td class=\"dsc\">" + esc(c[2]) + "</td></tr>";
      }).join("");
      return "<div class=\"schema-table\">" +
        "<h3>" + esc(t.name) + "</h3>" +
        "<p class=\"schema-desc\">" + esc(t.desc) + "</p>" +
        "<table>" + rows + "</table>" +
        (t.note ? "<p class=\"schema-note\">" + esc(t.note) + "</p>" : "") +
        "</div>";
    }).join("");
  }

  function renderExamples() {
    var ul = el("examples");
    EXAMPLES.forEach(function (ex) {
      var li = document.createElement("li");
      li.innerHTML = "<button type=\"button\" class=\"example-btn\">" + esc(ex.label) + "</button>";
      li.querySelector("button").addEventListener("click", function () {
        input.value = ex.sql;
        run();
        input.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      ul.appendChild(li);
    });
  }

  function renderTable(rows) {
    if (!rows || !rows.length) {
      resultEl.innerHTML = "<p class=\"query-hint\">The query ran successfully but returned no rows.</p>";
      return;
    }
    // collect column order from the union of keys (first row usually suffices)
    var cols = Object.keys(rows[0]);
    rows.forEach(function (r) { Object.keys(r).forEach(function (k) { if (cols.indexOf(k) < 0) cols.push(k); }); });
    var thead = "<tr>" + cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr>";
    var tbody = rows.map(function (r) {
      return "<tr>" + cols.map(function (c) {
        var v = r[c];
        if (v === null || v === undefined) v = "";
        else if (typeof v === "number") v = (Math.round(v * 1000) / 1000);
        return "<td>" + esc(v) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    resultEl.innerHTML = "<div class=\"query-table-wrap\"><table class=\"query-table\"><thead>" +
      thead + "</thead><tbody>" + tbody + "</tbody></table></div>" +
      "<p class=\"query-rowcount\">" + rows.length + " row" + (rows.length === 1 ? "" : "s") + "</p>";
  }

  function toCSV(rows) {
    var cols = Object.keys(rows[0]);
    var esc2 = function (v) {
      if (v === null || v === undefined) return "";
      v = String(v);
      return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    };
    var lines = [cols.join(",")];
    rows.forEach(function (r) { lines.push(cols.map(function (c) { return esc2(r[c]); }).join(",")); });
    return lines.join("\n");
  }

  function run() {
    var sql = (input.value || "").trim();
    if (!sql) { setStatus("Enter a query first.", true); return; }
    if (!IDM_AI.isSafeSelect(sql)) {
      setStatus("Only a single read-only SELECT statement is allowed.", true);
      resultEl.innerHTML = "<p class=\"query-hint\">For safety, this page runs only <code>SELECT</code> queries " +
        "(no INSERT/UPDATE/DELETE/DROP, and one statement at a time).</p>";
      csvBtn.disabled = true; lastRows = null;
      return;
    }
    setStatus("Running…");
    try {
      var rows = IDM_AI.runSQL(sql);
      lastRows = rows;
      renderTable(rows);
      csvBtn.disabled = !(rows && rows.length);
      setStatus(rows && rows.length ? "" : "No rows.");
    } catch (e) {
      setStatus("Error: " + (e && e.message || e), true);
      resultEl.innerHTML = "<p class=\"query-error\">" + esc(String(e && e.message || e)) + "</p>" +
        "<p class=\"query-hint\">Check your column and table names against the schema on the right.</p>";
      csvBtn.disabled = true; lastRows = null;
    }
  }

  window.addEventListener("DOMContentLoaded", function () {
    input = el("sql-input"); statusEl = el("query-status");
    resultEl = el("query-result"); csvBtn = el("csv-btn");

    renderSchema();
    renderExamples();

    setStatus("Loading data…");
    IDM_AI.loadData().then(function () {
      setStatus("Ready. Write a query or pick an example.");
    }).catch(function (e) {
      setStatus("Could not load the data: " + (e && e.message || e), true);
    });

    el("run-btn").addEventListener("click", run);
    el("clear-btn").addEventListener("click", function () {
      input.value = ""; resultEl.innerHTML = "<p class=\"query-hint\">Cleared.</p>";
      csvBtn.disabled = true; lastRows = null; setStatus(""); input.focus();
    });
    csvBtn.addEventListener("click", function () {
      if (!lastRows || !lastRows.length) return;
      var blob = new Blob([toCSV(lastRows)], { type: "text/csv" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = "idm_query_result.csv";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
    // Ctrl/Cmd+Enter runs
    input.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); }
    });
  });
})();
