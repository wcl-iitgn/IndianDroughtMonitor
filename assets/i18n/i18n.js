/* India Drought Monitor — runtime internationalization (i18n)
 *
 * - Loads Texts/<Language>/ui.json for the active language and replaces the text of
 *   every element carrying a data-i18n="scope.key" attribute (and attributes via
 *   data-i18n-attr="attr:scope.key;attr2:scope.key2").
 * - English is the source of truth; its file lives at Texts/English/ui.json. When a
 *   key is missing in a target language, the existing (English) DOM text is kept.
 * - Renders a language <select> into [data-i18n-switcher] (added to the header).
 * - Remembers the choice in localStorage and honors ?lang=Hindi in the URL.
 *
 * No build step, no framework. Safe to include on every page.
 */
(function () {
  "use strict";

  var LS_KEY = "idm_lang";
  var BASE = "Texts/";                 // relative to the page (site served from repo root)
  var LANGS_URL = BASE + "languages.json";

  function qs(name) {
    var m = new RegExp("[?&]" + name + "=([^&]+)").exec(window.location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }

  // Resolve "scope.key" (dot path) inside the loaded dictionary.
  function lookup(dict, dotted) {
    var parts = dotted.split(".");
    var cur = dict;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null) return undefined;
      cur = cur[parts[i]];
    }
    return typeof cur === "string" ? cur : undefined;
  }

  function applyDict(dict) {
    // text content
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      var val = lookup(dict, key);
      if (val != null) {
        // keep child elements that are marked to be preserved (e.g. icons) — replace only text nodes
        if (el.hasAttribute("data-i18n-html")) el.innerHTML = val;
        else el.textContent = val;
      }
    });
    // attributes: data-i18n-attr="placeholder:home.search;title:home.searchTitle"
    document.querySelectorAll("[data-i18n-attr]").forEach(function (el) {
      el.getAttribute("data-i18n-attr").split(";").forEach(function (pair) {
        pair = pair.trim(); if (!pair) return;
        var bits = pair.split(":");
        if (bits.length !== 2) return;
        var attr = bits[0].trim(), key = bits[1].trim();
        var val = lookup(dict, key);
        if (val != null) el.setAttribute(attr, val);
      });
    });
  }

  function buildSwitcher(languages, active, onChange) {
    var hosts = document.querySelectorAll("[data-i18n-switcher]");
    if (!hosts.length) return;
    hosts.forEach(function (host) {
      host.innerHTML = "";
      var label = document.createElement("span");
      label.className = "idm-lang-label";
      label.textContent = "\uD83C\uDF10";          // globe
      label.setAttribute("aria-hidden", "true");
      var sel = document.createElement("select");
      sel.className = "idm-lang-select";
      sel.setAttribute("aria-label", "Choose language");
      languages.forEach(function (l) {
        var o = document.createElement("option");
        o.value = l.key;
        o.textContent = l.native && l.native !== l.label ? (l.label + " — " + l.native) : l.label;
        if (l.key === active) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", function () { onChange(sel.value); });
      host.appendChild(label);
      host.appendChild(sel);
    });
  }

  function setHtmlLang(langObj) {
    if (!langObj) return;
    document.documentElement.setAttribute("lang", langObj.code ? langObj.code.split("_")[0] : "en");
    document.documentElement.setAttribute("dir", langObj.dir || "ltr");
  }

  function fetchJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }

  var IDM_I18N = {
    languages: [],
    current: "English",
    dict: {},
    /** translate a key at runtime (for JS-built UI, e.g. the chatbot) */
    t: function (key, fallback) {
      var v = lookup(this.dict, key);
      return v != null ? v : (fallback != null ? fallback : key);
    },
    /** expose the active language object */
    lang: function () {
      var self = this;
      return this.languages.filter(function (l) { return l.key === self.current; })[0] || null;
    },
    setLanguage: function (key) {
      var self = this;
      var langObj = this.languages.filter(function (l) { return l.key === key; })[0];
      if (!langObj) { key = "English"; langObj = this.languages.filter(function (l){return l.key==="English";})[0]; }
      return fetchJSON(BASE + encodeURIComponent(key) + "/ui.json")
        .catch(function () { return fetchJSON(BASE + "English/ui.json"); })
        .then(function (dict) {
          self.current = key;
          self.dict = dict;
          try { localStorage.setItem(LS_KEY, key); } catch (e) {}
          setHtmlLang(langObj);
          applyDict(dict);
          buildSwitcher(self.languages, key, function (k) { self.setLanguage(k); });
          document.dispatchEvent(new CustomEvent("idm:languagechange", { detail: { language: key, dict: dict } }));
          return dict;
        });
    },
    init: function () {
      var self = this;
      return fetchJSON(LANGS_URL).then(function (data) {
        self.languages = data.languages || [];
        var initial = qs("lang");
        if (!initial) { try { initial = localStorage.getItem(LS_KEY); } catch (e) {} }
        if (!initial) initial = data.default || "English";
        if (!self.languages.some(function (l) { return l.key === initial; })) initial = data.default || "English";
        return self.setLanguage(initial);
      }).catch(function (e) {
        // languages.json missing/unreachable: leave the page in its built-in English
        if (window.console) console.warn("i18n init skipped:", e && e.message);
      });
    }
  };

  window.IDM_I18N = IDM_I18N;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { IDM_I18N.init(); });
  } else {
    IDM_I18N.init();
  }
})();
