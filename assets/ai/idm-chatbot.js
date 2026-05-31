/* =============================================================================
 * idm-chatbot.js — floating "Ask IDM" chat widget (site-wide)
 * -----------------------------------------------------------------------------
 * A small launcher button bottom-right; clicking opens a chat panel. Questions
 * run through IDM_AI.ask() (text -> AlaSQL -> answer). Shows the generated SQL
 * in a collapsible "details" line so the data path is transparent.
 *
 * Include on a page with:
 *   <script src="assets/vendor/papaparse.min.js"></script>
 *   <script src="assets/vendor/alasql.min.js"></script>
 *   <script src="assets/ai/idm-ai.js"></script>
 *   <script src="assets/ai/idm-chatbot.js"></script>
 * (optionally call IDM_AI.configure({apiUrl: '...'}) first to point at your LAN host)
 * ========================================================================== */
(function () {
  "use strict";

  var SUGGESTIONS = [
    "How much of India is in drought right now?",
    "Which five states are worst affected?",
    "Has drought expanded or contracted since last week?",
    "What's the rainfall outlook this month?",
    "Which states have the most extreme (D3+) drought?"
  ];

  function elFromHTML(html) {
    var d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
    });
  }

  function build() {
    // launcher
    var launcher = elFromHTML(
      '<button class="idm-chat-launch" aria-label="Open the drought data assistant" title="Ask about the data">' +
      '<span class="idm-chat-launch-icon">&#128172;</span><span class="idm-chat-launch-text">Ask IDM</span></button>');

    var panel = elFromHTML(
      '<section class="idm-chat-panel" hidden aria-label="Drought data assistant">' +
        '<header class="idm-chat-head">' +
          '<div><strong>Ask IDM</strong><span class="idm-chat-sub">drought &amp; hydro-outlook data</span></div>' +
          '<button class="idm-chat-close" aria-label="Close">&times;</button>' +
        '</header>' +
        '<div class="idm-chat-log" id="idm-chat-log"></div>' +
        '<div class="idm-chat-suggest" id="idm-chat-suggest"></div>' +
        '<form class="idm-chat-form" id="idm-chat-form">' +
          '<input type="text" id="idm-chat-input" autocomplete="off" placeholder="Ask about drought or the hydrological outlook…" />' +
          '<button type="submit" class="idm-chat-send" aria-label="Send">&#10148;</button>' +
        '</form>' +
        '<div class="idm-chat-foot">Answers are generated from the site\u2019s data. Verify important figures.</div>' +
      '</section>');

    document.body.appendChild(launcher);
    document.body.appendChild(panel);

    var log = panel.querySelector("#idm-chat-log");
    var form = panel.querySelector("#idm-chat-form");
    var input = panel.querySelector("#idm-chat-input");
    var suggestWrap = panel.querySelector("#idm-chat-suggest");
    var busy = false;
    var history = [];   // [{role:'user'|'assistant', content}] — full conversation

    function open() {
      panel.hidden = false; launcher.classList.add("is-open");
      setTimeout(function () { input.focus(); }, 50);
      if (!log.dataset.greeted) {
        addBot("Hi! Ask me about India\u2019s current drought conditions, the weekly trend, the worst-affected states, or the monthly hydrological outlook (rainfall, temperature, soil moisture, runoff, ET).");
        log.dataset.greeted = "1";
      }
    }
    function close() { panel.hidden = true; launcher.classList.remove("is-open"); }

    launcher.addEventListener("click", function () { panel.hidden ? open() : close(); });
    panel.querySelector(".idm-chat-close").addEventListener("click", close);

    SUGGESTIONS.forEach(function (q) {
      var b = elFromHTML('<button type="button" class="idm-chat-chip">' + esc(q) + "</button>");
      b.addEventListener("click", function () { if (!busy) { input.value = q; submit(); } });
      suggestWrap.appendChild(b);
    });

    function scroll() { log.scrollTop = log.scrollHeight; }
    function addUser(text) {
      log.appendChild(elFromHTML('<div class="idm-msg idm-msg-user">' + esc(text) + "</div>")); scroll();
    }
    function addBot(text) {
      var m = elFromHTML('<div class="idm-msg idm-msg-bot">' + esc(text) + "</div>"); log.appendChild(m); scroll(); return m;
    }
    function addThinking() {
      var m = elFromHTML('<div class="idm-msg idm-msg-bot idm-msg-think"><span class="idm-dot"></span><span class="idm-dot"></span><span class="idm-dot"></span></div>');
      log.appendChild(m); scroll(); return m;
    }
    // Collapsible "show query" — opt-in transparency about the AlaSQL that was run.
    function addQueryDetails(sql) {
      if (!sql) return;
      var d = elFromHTML(
        '<details class="idm-chat-sql"><summary>Show query</summary><pre></pre></details>');
      d.querySelector("pre").textContent = sql;   // textContent avoids any HTML-escaping issues
      log.appendChild(d); scroll();
    }

    async function submit() {
      var q = input.value.trim();
      if (!q || busy) return;
      busy = true; input.value = ""; suggestWrap.style.display = "none";
      addUser(q);
      var thinking = addThinking();
      try {
        var res = await IDM_AI.ask(q, history.slice());
        thinking.remove();
        if (res.error) {
          addBot("Sorry, I couldn't find an answer to that. Try rephrasing your question.");
          if (res.sql) addQueryDetails(res.sql);   // still show what it attempted
          // don't poison history with a failed turn
        } else {
          addBot(res.answer || "Sorry, I couldn't find an answer to that.");
          if (res.sql) addQueryDetails(res.sql);
          // record the successful exchange so follow-up questions have context
          history.push({ role: "user", content: q });
          history.push({ role: "assistant", content: res.answer || "" });
          if (history.length > 16) history = history.slice(-16);
        }
      } catch (e) {
        thinking.remove();
        var msg = String(e && e.message || e);
        if (/Failed to fetch|NetworkError|HTTP 0|load failed/i.test(msg)) {
          addBot("I couldn\u2019t reach the language model service. The assistant needs the LAN Ollama " +
                 "server to be running and reachable from this page.");
        } else {
          addBot("Something went wrong, please try again.");
        }
      } finally {
        busy = false;
      }
    }

    form.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
