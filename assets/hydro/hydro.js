/* =============================================================================
 * hydro.js — India Hydrological Outlook (IHO) front-end controller
 * -----------------------------------------------------------------------------
 * Drives three pages from two JSON manifests (no build step, vanilla JS):
 *   - hydro.html          dashboards (one composite PNG per variable)
 *   - hydro-maps.html     the individual maps that make up each dashboard
 *   - hydro-reports.html  the PDF report archive (inline preview + download)
 *
 * Image/PDF files live under Hydrologic_Outlook/Output/. Filenames contain
 * spaces and parentheses, so every path is URL-encoded before use.
 * ========================================================================== */
(function () {
  "use strict";

  var BASE = "Hydrologic_Outlook/Output";
  var DASH_DIR = BASE + "/Dashboards";
  var MAPS_DIR = BASE + "/All_Maps";
  var PDF_DIR = BASE + "/PDF_Archive";
  var MANIFEST_URL = "assets/hydro/hydro-manifest.json";
  var REPORTS_URL = "assets/hydro/reports-manifest.json";

  // Encode a path that may contain spaces/parentheses, keeping the slashes.
  function encPath(p) {
    return p.split("/").map(encodeURIComponent).join("/");
  }
  function el(id) { return typeof id === "string" ? document.getElementById(id) : id; }

  var _manifest = null;
  function loadManifest() {
    if (_manifest) return Promise.resolve(_manifest);
    return fetch(MANIFEST_URL).then(function (r) {
      if (!r.ok) throw new Error("could not load hydro manifest (" + r.status + ")");
      return r.json();
    }).then(function (j) { _manifest = j; return j; });
  }

  // which parameter is selected, persisted in the URL hash so pages can deep-link
  function paramFromHash(manifest) {
    var h = (location.hash || "").replace(/^#/, "");
    var found = manifest.parameters.filter(function (p) { return p.key === h; })[0];
    return found || manifest.parameters[0];
  }
  function fillParamSelect(selectEl, manifest, selectedKey) {
    selectEl.innerHTML = "";
    manifest.parameters.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.key; o.textContent = p.name;
      if (p.key === selectedKey) o.selected = true;
      selectEl.appendChild(o);
    });
  }

  // ---------------------------------------------------------------------------
  // DASHBOARDS PAGE
  // ---------------------------------------------------------------------------
  function initDashboards(cfg) {
    var sel = el(cfg.select), img = el(cfg.img), link = el(cfg.link),
        cap = el(cfg.caption), dl = el(cfg.download), loading = el(cfg.loading),
        monthBadge = el(cfg.monthBadge);

    loadManifest().then(function (m) {
      if (monthBadge && m.month_label) monthBadge.textContent = m.month_label;
      var current = paramFromHash(m);
      fillParamSelect(sel, m, current.key);

      function show(p) {
        var src = encPath(DASH_DIR + "/" + p.dashboard);
        if (loading) loading.style.display = "flex";
        img.style.visibility = "hidden";
        img.onload = function () {
          if (loading) loading.style.display = "none";
          img.style.visibility = "visible";
        };
        img.onerror = function () {
          if (loading) loading.innerHTML = "<div>Could not load the " + p.name + " dashboard.</div>";
        };
        img.src = src;
        img.alt = p.name + " hydrological outlook dashboard for " + (m.month_label || "");
        link.href = src;
        if (cap) cap.textContent = p.name + " \u2014 " + (p.description || "");
        if (dl) {
          dl.href = src;
          dl.setAttribute("download", p.key + "_dashboard.png");
        }
        if (location.hash.replace(/^#/, "") !== p.key) history.replaceState(null, "", "#" + p.key);
      }

      show(current);
      sel.addEventListener("change", function () {
        var p = m.parameters.filter(function (x) { return x.key === sel.value; })[0];
        if (p) show(p);
      });

      // keep the "see individual maps" buttons pointing at the current variable
      var mapsBtns = document.querySelectorAll('#dash-maps, #view-maps-link');
      function syncMapsLinks() {
        mapsBtns.forEach(function (a) { a.href = "hydro-maps.html#" + sel.value; });
      }
      syncMapsLinks();
      sel.addEventListener("change", syncMapsLinks);
    }).catch(function (e) {
      if (loading) loading.innerHTML = "<div>" + e.message + "</div>";
    });
  }

  // ---------------------------------------------------------------------------
  // INDIVIDUAL MAPS PAGE
  // ---------------------------------------------------------------------------
  function initMaps(cfg) {
    var sel = el(cfg.select), grid = el(cfg.grid), loading = el(cfg.loading),
        desc = el(cfg.desc), monthBadge = el(cfg.monthBadge);
    var lb = el(cfg.lightbox), lbImg = el(cfg.lightboxImg),
        lbCap = el(cfg.lightboxCap), lbClose = el(cfg.lightboxClose);

    function openLightbox(src, label) {
      if (!lb) return;
      lbImg.src = src; lbImg.alt = label; lbCap.textContent = label;
      lb.hidden = false; document.body.style.overflow = "hidden";
    }
    function closeLightbox() {
      if (!lb) return;
      lb.hidden = true; lbImg.src = ""; document.body.style.overflow = "";
    }
    if (lb) {
      lbClose.addEventListener("click", closeLightbox);
      lb.addEventListener("click", function (e) { if (e.target === lb) closeLightbox(); });
      document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeLightbox(); });
    }

    loadManifest().then(function (m) {
      if (monthBadge && m.month_label) monthBadge.textContent = m.month_label;
      var current = paramFromHash(m);
      fillParamSelect(sel, m, current.key);

      function render(p) {
        if (loading) loading.style.display = "flex";
        if (desc) desc.textContent = p.description || "";
        grid.innerHTML = "";
        var pending = p.maps.length;
        if (!pending && loading) loading.style.display = "none";
        p.maps.forEach(function (mp) {
          var src = encPath(MAPS_DIR + "/" + p.folder + "/" + mp.file);
          var fig = document.createElement("figure");
          fig.className = "hydro-map-card" + (mp.role && mp.role !== "other" ? " is-" + mp.role : "");
          var roleTag = "";
          if (mp.role === "current") roleTag = '<span class="hydro-map-tag tag-current">Current</span>';
          else if (mp.role === "forecast") roleTag = '<span class="hydro-map-tag tag-forecast">Forecast</span>';
          fig.innerHTML =
            '<button type="button" class="hydro-map-thumb" aria-label="Enlarge ' + mp.label + '">' +
            roleTag +
            '<img loading="lazy" alt="' + p.name + " \u2014 " + mp.label + '" />' +
            '</button>' +
            '<figcaption>' + mp.label + '</figcaption>';
          var imgEl = fig.querySelector("img");
          imgEl.addEventListener("load", function () { if (--pending <= 0 && loading) loading.style.display = "none"; });
          imgEl.addEventListener("error", function () { if (--pending <= 0 && loading) loading.style.display = "none"; });
          imgEl.src = src;
          fig.querySelector(".hydro-map-thumb").addEventListener("click", function () {
            openLightbox(src, p.name + " \u2014 " + mp.label);
          });
          grid.appendChild(fig);
        });
        if (location.hash.replace(/^#/, "") !== p.key) history.replaceState(null, "", "#" + p.key);
      }

      render(current);
      sel.addEventListener("change", function () {
        var p = m.parameters.filter(function (x) { return x.key === sel.value; })[0];
        if (p) render(p);
      });
    }).catch(function (e) {
      if (loading) loading.innerHTML = "<div>" + e.message + "</div>";
    });
  }

  // ---------------------------------------------------------------------------
  // PDF REPORTS PAGE
  // ---------------------------------------------------------------------------
  function initReports(cfg) {
    var listEl = el(cfg.list), frame = el(cfg.frame), title = el(cfg.title),
        dl = el(cfg.download), fallback = el(cfg.fallback), fallbackLink = el(cfg.fallbackLink);

    fetch(REPORTS_URL).then(function (r) {
      if (!r.ok) throw new Error("could not load reports manifest (" + r.status + ")");
      return r.json();
    }).then(function (j) {
      var reports = j.reports || [];
      if (!reports.length) {
        listEl.innerHTML = "<li class='hydro-empty'>No reports available yet.</li>";
        return;
      }

      function select(rep, liEl) {
        var src = encPath(PDF_DIR + "/" + rep.file);
        // cache-bust-free; #toolbar=1 keeps the native PDF toolbar where supported
        frame.src = src + "#view=FitH";
        if (title) title.textContent = "India Hydrological Outlook \u2014 " + rep.label;
        if (dl) { dl.href = src; dl.setAttribute("download", rep.file); dl.style.display = ""; }
        if (fallback) fallback.hidden = true;
        if (fallbackLink) fallbackLink.href = src;
        Array.prototype.forEach.call(listEl.children, function (c) { c.classList.remove("active"); });
        if (liEl) liEl.classList.add("active");
      }

      reports.forEach(function (rep, i) {
        var li = document.createElement("li");
        li.innerHTML =
          '<button type="button" class="hydro-report-btn">' +
          '<span class="hydro-report-label">' + rep.label + '</span>' +
          '<span class="hydro-report-sub">' + (rep.date || rep.file) + '</span>' +
          '</button>' +
          '<a class="hydro-report-dl" href="' + encPath(PDF_DIR + "/" + rep.file) + '" download title="Download ' + rep.label + ' report">&#11015;</a>';
        li.querySelector(".hydro-report-btn").addEventListener("click", function () { select(rep, li); });
        listEl.appendChild(li);
        if (i === 0) select(rep, li);   // preview the newest by default
      });

      // If the iframe fails to render a PDF (some browsers block inline PDFs), show fallback.
      frame.addEventListener("error", function () { if (fallback) fallback.hidden = false; });
    }).catch(function (e) {
      listEl.innerHTML = "<li class='hydro-empty'>" + e.message + "</li>";
    });
  }

  window.IHO = {
    initDashboards: initDashboards,
    initMaps: initMaps,
    initReports: initReports,
  };
})();
