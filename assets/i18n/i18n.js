/* =============================================================================
 * i18n.js — India Drought Monitor website localisation + language picker
 * -----------------------------------------------------------------------------
 * The pages already carry data-i18n="group.key" on every static string (and
 * data-i18n-attr="attr:group.key" for attributes like aria-label). This script:
 *   1. reads the chosen language from localStorage (default English),
 *   2. for non-English, fetches assets/i18n/<Language>.json and swaps the text
 *      of every [data-i18n] element and every [data-i18n-attr] attribute,
 *   3. injects a language picker into the header,
 *   4. exposes window.IDM_I18N = { current, t(key, fallback), languages },
 *      which the dynamic pages (summary / forecast / hydro / chatbot) read to
 *      load their per-language content.
 *
 * English needs no JSON — the English text is already in the HTML. The picker
 * list and per-language fonts/direction come from Texts/languages.json.
 * ============================================================================= */
(function () {
  "use strict";

  var STORAGE_KEY = "idm_lang";
  var DEFAULT_LANG = "English";
  var current = "";
  try { current = localStorage.getItem(STORAGE_KEY) || ""; } catch (e) { current = ""; }
  if (!current) current = DEFAULT_LANG;

  var strings = null;     // loaded translation table (null for English)
  var languages = [];     // [{key, native, dir, ...}] from languages.json

  // ---- key lookup: "group.key" -> string -----------------------------------
  function lookup(table, dottedKey) {
    if (!table) return undefined;
    var node = table;
    var parts = dottedKey.split(".");
    for (var i = 0; i < parts.length; i++) {
      if (node == null || typeof node !== "object") return undefined;
      node = node[parts[i]];
    }
    return (typeof node === "string") ? node : undefined;
  }

  function t(key, fallback) {
    var v = lookup(strings, key);
    if (v != null) return v;
    return (fallback != null) ? fallback : key;
  }

  // ---- apply translations to the DOM ---------------------------------------
  function setText(el, val) {
    if (el.children.length === 0) { el.textContent = val; return; }
    // element has child elements: replace only its own text node(s), keep children
    var done = false;
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 3 && n.textContent.trim()) {
        n.textContent = done ? "" : val; done = true;
      }
    }
    if (!done) el.insertBefore(document.createTextNode(val), el.firstChild);
  }

  function applyTranslations() {
    if (!strings) return;  // English: leave the HTML as-is
    var els = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < els.length; i++) {
      var key = els[i].getAttribute("data-i18n");
      var val = lookup(strings, key);
      if (val != null) setText(els[i], val);
    }
    var ael = document.querySelectorAll("[data-i18n-attr]");
    for (var j = 0; j < ael.length; j++) {
      var spec = ael[j].getAttribute("data-i18n-attr");  // "aria-label:index.primary, title:..."
      var pairs = spec.split(",");
      for (var k = 0; k < pairs.length; k++) {
        var idx = pairs[k].indexOf(":");
        if (idx === -1) continue;
        var attr = pairs[k].slice(0, idx).trim();
        var akey = pairs[k].slice(idx + 1).trim();
        var av = lookup(strings, akey);
        if (av != null) ael[j].setAttribute(attr, av);
      }
    }
  }

  // ---- language picker ------------------------------------------------------
  function nativeName(key) {
    for (var i = 0; i < languages.length; i++) if (languages[i].key === key) return languages[i].native || key;
    return key;
  }
  function dirOf(key) {
    for (var i = 0; i < languages.length; i++) if (languages[i].key === key) return languages[i].dir || "ltr";
    return "ltr";
  }

  function buildPicker() {
    if (!languages.length || document.getElementById("idm-lang-select")) return;
    var sel = document.createElement("select");
    sel.id = "idm-lang-select";
    sel.setAttribute("aria-label", "Choose language");
    sel.style.cssText =
      "margin-left:auto;padding:4px 8px;border:1px solid #b9c2c9;border-radius:6px;" +
      "background:#fff;color:#1a1a1a;font-size:13px;cursor:pointer;";
    for (var i = 0; i < languages.length; i++) {
      var o = document.createElement("option");
      o.value = languages[i].key;
      o.textContent = languages[i].native ? (languages[i].native + " — " + languages[i].key) : languages[i].key;
      if (languages[i].key === current) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener("change", function () { setLanguage(sel.value); });

    var nav = document.querySelector(".usdm-nav");
    if (nav) {
      nav.style.display = nav.style.display || "flex";
      nav.style.alignItems = nav.style.alignItems || "center";
      nav.appendChild(sel);
    } else {
      sel.style.position = "fixed"; sel.style.top = "8px"; sel.style.right = "8px"; sel.style.zIndex = "9999";
      document.body.appendChild(sel);
    }
  }

  function setLanguage(key) {
    try { localStorage.setItem(STORAGE_KEY, key); } catch (e) {}
    // simplest correct swap: reload so every script re-reads the new language
    window.location.reload();
  }

  // ---- boot -----------------------------------------------------------------
  function fetchJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
      return r.json();
    });
  }

  function start() {
    // direction first (cheap, avoids a flash for RTL)
    var loadStrings = (current === DEFAULT_LANG)
      ? Promise.resolve(null)
      : fetchJSON("assets/i18n/" + current + ".json").catch(function (e) {
          console.warn("i18n: could not load " + current + " (" + e.message + "); showing English.");
          return null;
        });

    var loadLangs = fetchJSON("Texts/languages.json")
      .then(function (d) { languages = (d && d.languages) || []; })
      .catch(function () { languages = []; });

    Promise.all([loadStrings, loadLangs]).then(function (res) {
      strings = res[0];
      // If the language's UI-strings file isn't there yet, keep the chosen language
      // selected anyway (the picker reflects the choice, dynamic content still switches);
      // only the static labels fall back to the English already in the HTML.
      if (strings === null && current !== DEFAULT_LANG) {
        console.info("i18n: assets/i18n/" + current + ".json not found — labels stay English "
                     + "until you run translate_ui_strings.py; dynamic content still uses " + current + ".");
      }
      document.documentElement.lang = current;
      document.documentElement.dir = dirOf(current);
      window.IDM_I18N.current = current;
      applyTranslations();
      buildPicker();
      // let dynamic pages know the language is settled
      try { window.dispatchEvent(new CustomEvent("idm:i18n-ready", { detail: { lang: current } })); } catch (e) {}
    });
  }

  // expose early (synchronously) so other scripts read the right current language
  window.IDM_I18N = { current: current, t: t, languages: languages, setLanguage: setLanguage };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
