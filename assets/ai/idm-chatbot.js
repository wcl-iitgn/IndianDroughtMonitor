/* =============================================================================
 * idm-chatbot.js — floating "Ask IDM" chat widget (site-wide, ENGLISH ONLY)
 * -----------------------------------------------------------------------------
 * A launcher button bottom-right; clicking opens a chat panel. Questions run
 * through IDM_AI.ask() (text -> AlaSQL -> answer). A small settings strip lets
 * the user switch the language model between:
 *   - DeepSeek (cloud)  — OpenAI-compatible API; needs an API key (stored only
 *                         in the browser's localStorage; never sent anywhere but
 *                         to the DeepSeek endpoint)
 *   - Ollama (local)    — the LAN Ollama server
 *
 * The assistant is intentionally limited to ENGLISH: when the site language is
 * anything other than English the widget is not shown at all.
 * ========================================================================== */
(function () {
  "use strict";

  // --- persistence keys -------------------------------------------------------
  var LS_PROVIDER = "idm_chat_provider";       // "deepseek" | "ollama"
  var LS_KEY = "idm_chat_deepseek_key";        // the user's DeepSeek API key

  // The DeepSeek API key is hardcoded in idm-ai.js (CFG.deepseek.apiKey). A key
  // typed into the settings field is stored here and overrides it for that browser.

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // i18n helper (the widget only ever renders in English, but keep the hook).
  function t(key, fallback) {
    if (window.IDM_I18N && typeof window.IDM_I18N.t === "function") return window.IDM_I18N.t("chatbot." + key, fallback);
    return fallback;
  }

  // The widget is English-only. Mirror i18n's precedence: ?lang= > localStorage > current.
  function currentLang() {
    var m = /[?&]lang=([^&]+)/.exec(location.search);
    if (m) return decodeURIComponent(m[1]);
    var ls = lsGet("idm_lang");
    if (ls) return ls;
    return (window.IDM_I18N && window.IDM_I18N.current) || "English";
  }
  function isEnglish() { return currentLang() === "English"; }

  // Push the stored provider + (optional) key override into IDM_AI. Returns {provider, key}.
  function applyProviderConfig() {
    var provider = lsGet(LS_PROVIDER) || "deepseek";
    var cfg = { provider: provider };
    var storedKey = lsGet(LS_KEY);            // per-browser override of the hardcoded key
    if (storedKey) cfg.deepseek = { apiKey: storedKey };
    if (window.IDM_AI && IDM_AI.configure) IDM_AI.configure(cfg);
    var effKey = (window.IDM_AI && IDM_AI.getConfig) ? (IDM_AI.getConfig().deepseek.apiKey || "") : (storedKey || "");
    return { provider: provider, key: effKey };
  }
  function deepseekKeyMissing() {
    if ((lsGet(LS_PROVIDER) || "deepseek") !== "deepseek") return false;
    var k = (window.IDM_AI && IDM_AI.getConfig) ? IDM_AI.getConfig().deepseek.apiKey : "";
    return !(k && String(k).trim());
  }

  function suggestions() {
    return [
      t("suggest_1", "How much of India is in drought right now?"),
      t("suggest_2", "Which five states are worst affected?"),
      t("suggest_3", "Has drought expanded or contracted since last week?"),
      t("suggest_4", "What's the rainfall outlook this month?"),
      t("suggest_5", "Which states have the most extreme (D3+) drought?")
    ];
  }

  function elFromHTML(html) {
    var d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
    });
  }

  function build() {
    var launcher = elFromHTML(
      '<button class="idm-chat-launch" aria-label="' + esc(t("launch_aria", "Open the drought data assistant")) + '" title="' + esc(t("launch_title", "Ask about the data")) + '">' +
      '<span class="idm-chat-launch-icon">&#128172;</span><span class="idm-chat-launch-text">' + esc(t("launch", "Ask IDM")) + '</span></button>');

    var panel = elFromHTML(
      '<section class="idm-chat-panel" hidden aria-label="' + esc(t("launch_aria", "Drought data assistant")) + '">' +
        '<header class="idm-chat-head">' +
          '<div><strong>' + esc(t("head_title", "Ask IDM")) + '</strong><span class="idm-chat-sub">' + esc(t("head_sub", "drought & hydro-outlook data")) + '</span></div>' +
          '<button class="idm-chat-close" aria-label="' + esc(t("close_aria", "Close")) + '">&times;</button>' +
        '</header>' +
        '<div class="idm-chat-settings">' +
          '<label class="idm-chat-set-label" for="idm-chat-provider">' + esc(t("model", "Model")) + '</label>' +
          '<select id="idm-chat-provider" class="idm-chat-provider" aria-label="' + esc(t("model", "Model")) + '">' +
            '<option value="deepseek">' + esc(t("provider_deepseek", "DeepSeek (cloud)")) + '</option>' +
            '<option value="ollama">' + esc(t("provider_ollama", "Ollama (local)")) + '</option>' +
          '</select>' +
          '<input type="password" id="idm-chat-key" class="idm-chat-key" autocomplete="off" spellcheck="false" placeholder="' + esc(t("key_placeholder", "DeepSeek API key")) + '" />' +
        '</div>' +
        '<div class="idm-chat-log" id="idm-chat-log"></div>' +
        '<div class="idm-chat-suggest" id="idm-chat-suggest"></div>' +
        '<form class="idm-chat-form" id="idm-chat-form">' +
          '<input type="text" id="idm-chat-input" autocomplete="off" placeholder="' + esc(t("input_placeholder", "Ask about drought or the hydrological outlook…")) + '" />' +
          '<button type="submit" class="idm-chat-send" aria-label="' + esc(t("send_aria", "Send")) + '">&#10148;</button>' +
        '</form>' +
        '<div class="idm-chat-foot">' + esc(t("foot", "Answers are generated from the site\u2019s data. Verify important figures.")) + '</div>' +
      '</section>');

    document.body.appendChild(launcher);
    document.body.appendChild(panel);

    var log = panel.querySelector("#idm-chat-log");
    var form = panel.querySelector("#idm-chat-form");
    var input = panel.querySelector("#idm-chat-input");
    var suggestWrap = panel.querySelector("#idm-chat-suggest");
    var providerSel = panel.querySelector("#idm-chat-provider");
    var keyInput = panel.querySelector("#idm-chat-key");
    var busy = false;
    var history = [];

    // initialise settings from storage
    var conf = applyProviderConfig();
    providerSel.value = conf.provider;
    keyInput.value = conf.key;
    function syncKeyVisibility() { keyInput.style.display = (providerSel.value === "deepseek") ? "" : "none"; }
    syncKeyVisibility();
    providerSel.addEventListener("change", function () {
      lsSet(LS_PROVIDER, providerSel.value);
      IDM_AI.configure({ provider: providerSel.value });
      syncKeyVisibility();
    });
    keyInput.addEventListener("input", function () {
      var v = keyInput.value.trim();
      lsSet(LS_KEY, v);
      IDM_AI.configure({ deepseek: { apiKey: v } });
    });

    function open() {
      panel.hidden = false; launcher.classList.add("is-open");
      setTimeout(function () { (deepseekKeyMissing() ? keyInput : input).focus(); }, 50);
      if (!log.dataset.greeted) {
        addBot(t("greeting", "Hi! Ask me about India\u2019s current drought conditions, the weekly trend, the worst-affected states, or the monthly hydrological outlook (rainfall, temperature, soil moisture, runoff, ET)."));
        log.dataset.greeted = "1";
      }
    }
    function close() { panel.hidden = true; launcher.classList.remove("is-open"); }

    launcher.addEventListener("click", function () { panel.hidden ? open() : close(); });
    panel.querySelector(".idm-chat-close").addEventListener("click", close);

    suggestions().forEach(function (q) {
      var b = elFromHTML('<button type="button" class="idm-chat-chip">' + esc(q) + "</button>");
      b.addEventListener("click", function () { if (!busy) { input.value = q; submit(); } });
      suggestWrap.appendChild(b);
    });

    function scroll() { log.scrollTop = log.scrollHeight; }
    function addUser(text) { log.appendChild(elFromHTML('<div class="idm-msg idm-msg-user">' + esc(text) + "</div>")); scroll(); }
    function addBot(text) { var m = elFromHTML('<div class="idm-msg idm-msg-bot">' + esc(text) + "</div>"); log.appendChild(m); scroll(); return m; }
    function addThinking() {
      var m = elFromHTML('<div class="idm-msg idm-msg-bot idm-msg-think"><span class="idm-dot"></span><span class="idm-dot"></span><span class="idm-dot"></span></div>');
      log.appendChild(m); scroll(); return m;
    }
    function addQueryDetails(sql) {
      if (!sql) return;
      var d = elFromHTML('<details class="idm-chat-sql"><summary>' + esc(t("show_query", "Show query")) + '</summary><pre></pre></details>');
      d.querySelector("pre").textContent = sql;
      log.appendChild(d); scroll();
    }

    async function submit() {
      var q = input.value.trim();
      if (!q || busy) return;
      // DeepSeek selected but no key: guide the user instead of failing cryptically.
      if (deepseekKeyMissing()) {
        addUser(q); input.value = "";
        addBot(t("need_key", "Add your DeepSeek API key in the box above, or switch the model to Ollama (local)."));
        setTimeout(function () { keyInput.focus(); }, 50);
        return;
      }
      busy = true; input.value = ""; suggestWrap.style.display = "none";
      addUser(q);
      var thinking = addThinking();
      try {
        var res = await IDM_AI.ask(q, history.slice());
        thinking.remove();
        if (res.error) {
          addBot(t("err_no_answer", "Sorry, I couldn\u2019t find an answer to that. Try rephrasing your question."));
          if (res.sql) addQueryDetails(res.sql);
        } else {
          addBot(res.answer || t("err_no_answer", "Sorry, I couldn\u2019t find an answer to that."));
          if (res.sql) addQueryDetails(res.sql);
          history.push({ role: "user", content: q });
          history.push({ role: "assistant", content: res.answer || "" });
          if (history.length > 16) history = history.slice(-16);
        }
      } catch (e) {
        thinking.remove();
        var msg = String(e && e.message || e);
        var prov = lsGet(LS_PROVIDER) || "deepseek";
        if (/api key/i.test(msg)) {
          addBot(t("need_key", "Add your DeepSeek API key in the box above, or switch the model to Ollama (local)."));
        } else if (/Failed to fetch|NetworkError|HTTP 0|load failed|CORS/i.test(msg)) {
          if (prov === "ollama") {
            addBot(t("err_ollama", "I couldn\u2019t reach the local Ollama server. Make sure it\u2019s running and reachable from this page."));
          } else {
            addBot(t("err_deepseek", "I couldn\u2019t reach the DeepSeek API. Check your internet connection and that your API key is valid (browser calls also require the API to allow this origin)."));
          }
        } else {
          addBot(t("err_generic", "Something went wrong, please try again."));
        }
      } finally {
        busy = false;
      }
    }

    form.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
  }

  function removeWidget() {
    document.querySelectorAll(".idm-chat-launch, .idm-chat-panel").forEach(function (n) { n.remove(); });
  }

  function mount() {
    removeWidget();
    if (!isEnglish()) return;     // assistant is English-only
    applyProviderConfig();
    build();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
  // mount/unmount when the site language changes
  document.addEventListener("idm:languagechange", function () { mount(); });
})();
