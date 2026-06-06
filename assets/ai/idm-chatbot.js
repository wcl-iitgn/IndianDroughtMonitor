/* =============================================================================
 * idm-chatbot.js — floating "Ask IDM" chat widget (site-wide, ENGLISH ONLY)
 * -----------------------------------------------------------------------------
 * A launcher button bottom-right; clicking opens a chat panel. Questions run
 * through IDM_AI.ask() (text -> AlaSQL -> answer). The language model behind it
 * is the WCL OpenAI API (Flask on PythonAnywhere); the OpenAI key lives only on
 * that server, so there is nothing to configure in the browser — no provider
 * switch, no API-key field. Only the API's standard (non-admin) endpoints are
 * used.
 *
 * The assistant is intentionally limited to ENGLISH: when the site language is
 * anything other than English the widget is not shown at all.
 * ========================================================================== */
(function () {
  "use strict";

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
          '<div class="idm-chat-headbtns">' +
            '<button class="idm-chat-expand" aria-label="' + esc(t("expand_aria", "Expand")) + '" title="' + esc(t("expand_aria", "Expand")) + '">&#x2922;</button>' +
            '<button class="idm-chat-close" aria-label="' + esc(t("close_aria", "Close")) + '">&times;</button>' +
          '</div>' +
        '</header>' +
        '<div class="idm-chat-auth" id="idm-chat-auth" hidden>' +
          '<div class="idm-chat-auth-title">' + esc(t("signin_title", "Sign in to chat")) + '</div>' +
          '<input type="text" id="idm-chat-username" class="idm-chat-auth-input" autocomplete="username" spellcheck="false" placeholder="' + esc(t("signin_user", "Username")) + '" />' +
          '<input type="password" id="idm-chat-password" class="idm-chat-auth-input" autocomplete="current-password" placeholder="' + esc(t("signin_pass", "Password — leave blank for 5-minute access")) + '" />' +
          '<button type="button" id="idm-chat-signin" class="idm-chat-signin">' + esc(t("signin_button", "Start chatting")) + '</button>' +
          '<div class="idm-chat-auth-err" id="idm-chat-auth-err"></div>' +
          '<div class="idm-chat-auth-note">' + esc(t("signin_note", "Use the same username on another device to continue this chat. Temporary sessions last 5 minutes, then you sign in again.")) + '</div>' +
        '</div>' +
        '<div class="idm-chat-userbar" id="idm-chat-userbar" hidden>' +
          '<span class="idm-chat-whoami" id="idm-chat-whoami"></span>' +
          '<span class="idm-chat-userbtns">' +
            '<button type="button" class="idm-chat-priv" id="idm-chat-priv" title="' + esc(t("priv_title", "Ask the administrators for a permanent password (no 5-minute limit)")) + '">' + esc(t("priv_button", "Request permanent access")) + '</button>' +
            '<button type="button" class="idm-chat-signout" id="idm-chat-signout">' + esc(t("signout", "Sign out")) + '</button>' +
          '</span>' +
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
    var authBox = panel.querySelector("#idm-chat-auth");
    var authUser = panel.querySelector("#idm-chat-username");
    var authPass = panel.querySelector("#idm-chat-password");
    var authBtn = panel.querySelector("#idm-chat-signin");
    var authErr = panel.querySelector("#idm-chat-auth-err");
    var userBar = panel.querySelector("#idm-chat-userbar");
    var whoami = panel.querySelector("#idm-chat-whoami");
    var signoutBtn = panel.querySelector("#idm-chat-signout");
    var privBtn = panel.querySelector("#idm-chat-priv");
    var expandBtn = panel.querySelector(".idm-chat-expand");
    var busy = false;
    var history = [];
    var authed = false;

    // --- expandable panel (state remembered per browser) ----------------------
    function applyExpanded(on) {
      panel.classList.toggle("idm-chat-max", !!on);
      expandBtn.innerHTML = on ? "&#x2921;" : "&#x2922;";
      var lbl = on ? t("shrink_aria", "Restore size") : t("expand_aria", "Expand");
      expandBtn.setAttribute("aria-label", lbl); expandBtn.title = lbl;
      lsSet("idm_chat_expanded", on ? "1" : "");
    }
    applyExpanded(lsGet("idm_chat_expanded") === "1");
    expandBtn.addEventListener("click", function () {
      applyExpanded(!panel.classList.contains("idm-chat-max"));
    });

    // --- view switching: sign-in form vs the chat itself ---------------------
    function showAuth(prefillUser) {
      authed = false;
      authBox.hidden = false;
      userBar.hidden = true;
      form.style.display = "none";
      suggestWrap.style.display = "none";
      if (prefillUser) authUser.value = prefillUser;
      authErr.textContent = "";
      setTimeout(function () { authUser.focus(); }, 50);
    }
    function showChat(username, isPrivileged) {
      authed = true;
      authBox.hidden = true;
      userBar.hidden = false;
      whoami.textContent = t("signed_in_as", "Signed in as") + " " + username;
      // Privileged users already have a permanent password — hide the request.
      privBtn.style.display = isPrivileged ? "none" : "";
      form.style.display = "";
      suggestWrap.style.display = "";
      authPass.value = "";
      setTimeout(function () { input.focus(); }, 50);
    }

    // Render the server-held conversation (what makes the same chat appear when
    // the person signs in from another device).
    async function restoreHistory() {
      var msgs = await IDM_AI.fetchSessionHistory();
      if (!msgs.length) return false;
      log.innerHTML = "";
      history = [];
      msgs.forEach(function (m) {
        var p = IDM_AI.parseHistoryEntry(m);
        if (!p.text) return;
        if (p.role === "user") { addUser(p.text); history.push({ role: "user", content: p.text }); }
        else { addBot(p.text); history.push({ role: "assistant", content: p.text }); }
      });
      if (history.length > 16) history = history.slice(-16);
      log.dataset.greeted = "1";
      return true;
    }

    async function doSignin() {
      var u = authUser.value.trim();
      var p = authPass.value;
      authErr.textContent = "";
      if (!u) { authErr.textContent = t("signin_need_user", "Please enter a username."); authUser.focus(); return; }
      authBtn.disabled = true;
      try {
        var who = await IDM_AI.login(u, p);
        showChat(who.username, who.isPrivileged);
        var th = addThinking();
        var had = await restoreHistory();
        if (th.parentNode) th.remove();
        if (!had && !log.dataset.greeted) {
          addBot(t("greeting", "Hi! Ask me about India\u2019s current drought conditions, the weekly trend, the worst-affected states, or the monthly hydrological outlook (rainfall, temperature, soil moisture, runoff, ET)."));
          log.dataset.greeted = "1";
        }
      } catch (e) {
        authErr.textContent = String(e && e.message || t("err_generic", "Something went wrong, please try again."));
      } finally {
        authBtn.disabled = false;
      }
    }
    authBtn.addEventListener("click", doSignin);
    authPass.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); doSignin(); } });
    authUser.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); doSignin(); } });

    signoutBtn.addEventListener("click", async function () {
      await IDM_AI.logoutSession();
      log.innerHTML = "";
      delete log.dataset.greeted;
      history = [];
      showAuth("");
    });

    privBtn.addEventListener("click", async function () {
      if (privBtn.disabled) return;
      privBtn.disabled = true;
      try {
        var m = await IDM_AI.requestPrivilege();
        addBot((m || "") + " " + t("priv_followup", "An administrator will review it; once approved, sign in with the permanent password they give you."));
      } catch (e) {
        if (e && (e.code === "SESSION_EXPIRED" || e.code === "NO_SESSION")) {
          addBot(t("err_expired", "Your 5-minute session has ended. Sign in again to keep chatting \u2014 use the same username on any device to continue where you left off while a session is active."));
          showAuth((IDM_AI.getSession() || {}).username || authUser.value || "");
        } else {
          addBot(String(e && e.message || t("err_generic", "Something went wrong, please try again.")));
        }
      } finally {
        privBtn.disabled = false;
      }
    });

    function open() {
      panel.hidden = false; launcher.classList.add("is-open");
      // Resume the stored session if the server still considers it active;
      // otherwise ask the person to sign in.
      IDM_AI.verifySession().then(function (s) {
        if (s) {
          showChat(s.username);
          var th = addThinking();
          restoreHistory().then(function (had) {
            if (th.parentNode) th.remove();
            if (!had && !log.dataset.greeted) {
              addBot(t("greeting", "Hi! Ask me about India\u2019s current drought conditions, the weekly trend, the worst-affected states, or the monthly hydrological outlook (rainfall, temperature, soil moisture, runoff, ET)."));
              log.dataset.greeted = "1";
            }
          });
        } else {
          showAuth((IDM_AI.getSession() || {}).username || "");
        }
      });
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
      if (!authed) { showAuth((IDM_AI.getSession() || {}).username || ""); return; }
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
        var code = e && e.code;
        var msg = String(e && e.message || e);
        if (code === "SESSION_EXPIRED" || code === "NO_SESSION") {
          addBot(t("err_expired", "Your 5-minute session has ended. Sign in again to keep chatting \u2014 use the same username on any device to continue where you left off while a session is active."));
          showAuth((IDM_AI.getSession() || {}).username || authUser.value || "");
        } else if (code === "SESSION_FULL") {
          addBot(t("err_full", "This session reached its 10-message limit. Sign in again to start a fresh one."));
          IDM_AI.logoutSession().then(function () { showAuth(authUser.value || ""); });
        } else if (/Failed to fetch|NetworkError|HTTP 0|load failed|CORS/i.test(msg)) {
          addBot(t("err_backend", "I couldn\u2019t reach the WCL AI service. Check your internet connection and try again in a moment (the service must also allow this site\u2019s domain)."));
        } else if (/too many requests|429/i.test(msg)) {
          addBot(t("err_busy", "The AI service is busy right now. Please try again in a minute."));
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
    build();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
  // mount/unmount when the site language changes
  document.addEventListener("idm:languagechange", function () { mount(); });
})();
