#!/usr/bin/env python3
# =============================================================================
# build_hydro_manifests.py
#   Rebuild the two JSON manifests the website's Hydrological Outlook pages read,
#   straight from the files the hydro build just produced:
#
#     assets/hydro/hydro-manifest.json     dashboards + individual maps + month label
#     assets/hydro/reports-manifest.json   per-month PDF list (resolved at runtime
#                                           under Output/<Language>/PDF_Archive/)
#
#   Without this step those manifests stay frozen at whatever was committed, so the
#   page shows a stale month, an old map list, and "No reports available" regardless
#   of new builds. build.py runs this automatically after the hydro stage.
#
#   Standalone:  python3 build_hydro_manifests.py [--date YYYY_MM_DD]
#   The data month is taken from --date, else from the newest PDF filename, else today.
# =============================================================================

import argparse
import calendar
import datetime
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent
OUT_BASE = REPO / "Hydrologic_Outlook" / "Output"
DASH_DIR = OUT_BASE / "Dashboards"
MAPS_DIR = OUT_BASE / "All_Maps"
HYDRO_MANIFEST = REPO / "assets" / "hydro" / "hydro-manifest.json"
REPORTS_MANIFEST = REPO / "assets" / "hydro" / "reports-manifest.json"

# Canonical parameter order. Descriptions are preserved from the existing manifest
# when present; these are only a fallback for a fresh checkout.
PARAM_ORDER = ["Rainfall", "Temperature", "Relative_Wetness", "Total_Runoff", "Evapotranspiration"]
FALLBACK_META = {
    "Rainfall": ("Rainfall",
                 "Monthly rainfall as a percentile of the historical record. Low percentiles (red) indicate drier-than-normal conditions; high percentiles (blue) indicate wetter."),
    "Temperature": ("Surface Air Temperature",
                    "Monthly mean surface air temperature as a percentile of the historical record."),
    "Relative_Wetness": ("Relative Wetness (Soil Moisture)",
                         "Root-zone soil moisture expressed as a percentile of the historical record."),
    "Total_Runoff": ("Total Runoff",
                     "Monthly total runoff as a percentile of the historical record."),
    "Evapotranspiration": ("Evapotranspiration",
                          "Monthly evapotranspiration as a percentile of the historical record."),
}

_PDF_DATE = re.compile(r"Hydrolook_(\d{4})_(\d{2})_(\d{2})\.pdf$", re.I)


def _existing_meta():
    """folder -> {key, name, description} pulled from the current manifest, if any."""
    meta = {}
    if HYDRO_MANIFEST.exists():
        try:
            cur = json.loads(HYDRO_MANIFEST.read_text(encoding="utf-8"))
            for p in cur.get("parameters", []):
                f = p.get("folder") or p.get("key")
                if f:
                    meta[f] = {"key": p.get("key", f), "name": p.get("name", f),
                               "description": p.get("description", "")}
        except Exception:  # noqa: BLE001
            pass
    return meta


def _all_pdf_dirs():
    """Every <Language>/PDF_Archive that exists, English first."""
    if not OUT_BASE.exists():
        return []
    dirs = [d / "PDF_Archive" for d in OUT_BASE.iterdir()
            if d.is_dir() and d.name not in ("Dashboards", "All_Maps", "PDF_Archive")
            and (d / "PDF_Archive").is_dir()]
    dirs.sort(key=lambda p: (p.parent.name != "English", p.parent.name))
    return dirs


def _reports():
    """List of {file,label,date} newest-first, from the canonical language's PDF_Archive.
    Filenames are identical across languages, so the page resolves them per language."""
    dirs = _all_pdf_dirs()
    if not dirs:
        return []
    src = dirs[0]
    rows = []
    for pdf in src.glob("Hydrolook_*.pdf"):
        m = _PDF_DATE.search(pdf.name)
        if not m:
            continue
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt = datetime.date(y, mo, d)
        except ValueError:
            continue
        rows.append((dt, {"file": pdf.name,
                          "label": dt.strftime("%B %Y"),
                          "date": dt.isoformat()}))
    rows.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in rows]


def _base_date(arg_date, reports):
    if arg_date:
        m = re.match(r"(\d{4})_(\d{2})_(\d{2})", arg_date)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    if reports:
        return datetime.date.fromisoformat(reports[0]["date"])
    return datetime.date.today()


def _maps_for(folder, cur_name, fc_name):
    d = MAPS_DIR / folder
    if not d.is_dir():
        return []
    out = []
    for png in d.glob("*.png"):
        label = png.stem
        if label == cur_name:
            role = "current"
        elif label == fc_name:
            role = "forecast"
        else:
            role = "other"
        out.append({"file": png.name, "label": label, "role": role})
    rank = {"current": 0, "forecast": 1, "other": 2}
    out.sort(key=lambda m: (rank[m["role"]], m["label"]))
    return out


def main():
    ap = argparse.ArgumentParser(description="Rebuild the website hydro manifests from build outputs.")
    ap.add_argument("--date", default=None, help="data date YYYY_MM_DD (else inferred from PDFs)")
    args = ap.parse_args()

    reports = _reports()
    base = _base_date(args.date, reports)
    cur_name = base.strftime("%B")
    nxt = base.replace(day=1)
    nxt = (nxt.replace(year=nxt.year + 1, month=1) if nxt.month == 12
           else nxt.replace(month=nxt.month + 1))
    fc_name = nxt.strftime("%B")

    meta = _existing_meta()
    params = []
    for folder in PARAM_ORDER:
        dash = DASH_DIR / ("%s_dashboard.png" % folder)
        maps = _maps_for(folder, cur_name, fc_name)
        if not dash.exists() and not maps:
            continue  # this parameter wasn't produced
        m = meta.get(folder) or {}
        fb = FALLBACK_META.get(folder, (folder, ""))
        params.append({
            "key": m.get("key", folder),
            "name": m.get("name", fb[0]),
            "folder": folder,
            "description": m.get("description") or fb[1],
            "dashboard": ("%s_dashboard.png" % folder) if dash.exists() else "",
            "maps": maps,
        })

    hydro = {
        "month_label": base.strftime("%B %Y"),
        "generated": base.isoformat(),
        "parameters": params,
    }
    HYDRO_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    HYDRO_MANIFEST.write_text(json.dumps(hydro, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORTS_MANIFEST.write_text(json.dumps({"reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")

    print("  hydro-manifest.json   : %s | %d parameters | maps current=%s forecast=%s"
          % (hydro["month_label"], len(params), cur_name, fc_name))
    print("  reports-manifest.json : %d report(s)%s"
          % (len(reports), (" (newest %s)" % reports[0]["file"]) if reports else " — none found yet"))
    if not OUT_BASE.exists():
        print("  note: %s does not exist; wrote empty manifests (run the hydro build first)." % OUT_BASE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
