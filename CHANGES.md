# India Drought Monitor — Change Summary (v10: show-query + Query the Data page)

## Verified: the chatbot's national answer is now correct

Confirmed against the raw data — for "How much of India is in drought right now?" the bot
answers **15.303% in drought (d0_pct) / 84.697% normal (normal_pct)**, which exactly matches
the latest weekly row (2026-05-20), via the query
`SELECT normal_pct, d0_pct FROM drought_timeseries ORDER BY date DESC LIMIT 1`.

## 1. "Show query" in the chatbot

Each chatbot answer is now followed by a collapsible **"Show query"** disclosure that reveals
the exact AlaSQL that produced it (shown in a dark code block, collapsed by default). It also
appears on the "couldn't answer" path so you can see what was attempted. This is an explicit,
opt-in transparency control — distinct from the model narrating its process in prose.

## 2. New page — "Query the Data" (data-query.html)

A page where users run their own **read-only SQL** (AlaSQL) against the same three in-browser
tables the assistant uses. It includes:

- A SQL editor with **Run** (also Ctrl/Cmd+Enter), **Clear**, and **Download CSV**.
- A results table (sortable-width, horizontally scrollable on small screens) with a row count.
- A **schema reference** in the sidebar: every table and column with its type and meaning, plus
  the key notes (cumulative vs per-class, percentile vs anomaly).
- **Eight example queries** (current national drought, worst/least states, week-on-week trend,
  D3+ filter, all-time peak, hydro outlook, rainfall detail) that load and run on click.
- The same **read-only guard** as the chatbot: only a single `SELECT` runs; INSERT/UPDATE/
  DELETE/DROP/CREATE are rejected with a clear message. Everything executes locally in the
  browser — nothing is sent to a server.

Linked from the Data landing page (a new card) and the footer Resources column on every page.

## Files added / touched

- `assets/ai/idm-chatbot.js` — collapsible "Show query" after each answer.
- `data-query.html` (new), `assets/query/query.js` (new), `assets/query/query.css` (new).
- `data.html` — "Query the Data" card; footer "Query the Data" link added site-wide.

## Validation

All 18 pages load with the chatbot present and **zero JS errors**. The new page was verified
headless: schema (3 tables) and 8 examples render; example + custom queries return correct rows
(e.g. worst states Delhi 52.2%, Arunachal 39.7%, J&K 38.2%); the read-only guard blocks `DROP`;
CSV export works. The chatbot "Show query" reveals the exact SQL that ran.
