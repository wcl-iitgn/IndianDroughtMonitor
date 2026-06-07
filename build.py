#!/usr/bin/env python3
# =============================================================================
# build.py  --  ONE command to (re)build the India Drought Monitor portal.
# -----------------------------------------------------------------------------
# This is the whole workflow. It runs the generators in the correct order and
# points them all at a single Ollama model (gemma4:e2b) for BOTH prose and
# translation, so there is nothing to sequence by hand: text generation and
# translation happen inside each generator, in one pass.
#
#   python3 build.py                 # full build, all 23 languages, via gemma4
#   python3 build.py --langs English # English only (fast; no translation)
#   python3 build.py --skip-llm      # no model at all (template summary, English)
#   python3 build.py --only hydro    # run just one stage (hydro|summary|forecast|schema|districts)
#   python3 build.py --with-districts# also (re)fetch + rebuild district data
#   python3 build.py --model gemma4:e2b --ollama-url http://localhost:11434/api/generate
#
# Order (default): hydro -> summary -> forecast -> schema
#   * hydro must run first: it writes the weekly English summary + index.json that
#     the summary stage reads. The summary/forecast stages then translate that text
#     into every language with gemma4 and render the per-language PDFs.
#   * districts is a static, network-dependent data refresh; it is NOT in the
#     default run. Use --with-districts (or --only districts) when boundaries change.
#
# When it finishes it prints the exact git commands to commit the result.
# =============================================================================

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
PY = sys.executable
ALL_STAGES = ["districts", "pdfstrings", "uistrings", "hydro", "summary", "forecast", "schema"]
DEFAULT_STAGES = ["pdfstrings", "uistrings", "hydro", "summary", "forecast", "schema"]


def langs_all():
    data = json.loads((REPO / "Texts" / "languages.json").read_text(encoding="utf-8"))
    return [l["key"] for l in data["languages"]]


def run(title, cmd):
    print("\n" + "=" * 74)
    print("  " + title)
    print("  $ " + " ".join(cmd))
    print("=" * 74, flush=True)
    rc = subprocess.call(cmd, cwd=str(REPO), env=os.environ.copy())
    if rc != 0:
        print("\n!! stage FAILED (exit %d): %s" % (rc, title))
        print("   Nothing after this point ran. Fix the error above and re-run "
              "(finished stages are cached / idempotent).")
        sys.exit(rc)


def main():
    ap = argparse.ArgumentParser(description="Sequentially (re)build the IDM portal via gemma4.")
    ap.add_argument("--langs", nargs="+", default=None,
                    help="languages to build (default: every language in Texts/languages.json). e.g. --langs English Hindi")
    ap.add_argument("--only", default=None,
                    help="comma-separated subset of stages: " + ",".join(ALL_STAGES))
    ap.add_argument("--with-districts", action="store_true",
                    help="also run the (network) district-data refresh in a default build")
    ap.add_argument("--skip-llm", action="store_true",
                    help="do not use the model: template summary, English only")
    ap.add_argument("--force", action="store_true",
                    help="re-translate per-language text even if it already exists")
    ap.add_argument("--ollama-url", default=None,
                    help="override Ollama /api/generate URL (else $IDM_OLLAMA_URL or LAN default)")
    ap.add_argument("--model", default=None,
                    help="override model (else $IDM_LLM_MODEL or gemma4:e2b)")
    ap.add_argument("--pdf-engine", default="latex", choices=["latex", "matplotlib"],
                    help="hydro PDF engine (default: latex / XeLaTeX)")
    ap.add_argument("--date", default=None, help="hydro forecast date YYYY_MM_DD (default: latest)")
    args = ap.parse_args()

    # Configure the shared LLM endpoint for EVERY child script (idm_llm reads these).
    if args.ollama_url:
        os.environ["IDM_OLLAMA_URL"] = args.ollama_url
    if args.model:
        os.environ["IDM_LLM_MODEL"] = args.model
    ollama_url = os.environ.get("IDM_OLLAMA_URL", "http://10.0.60.193:11434/api/generate")
    model = os.environ.get("IDM_LLM_MODEL", "gemma4:e2b")

    if args.only:
        stages = [s.strip() for s in args.only.split(",") if s.strip() in ALL_STAGES]
    else:
        stages = list(DEFAULT_STAGES)
        if args.with_districts:
            stages = ["districts"] + stages
    langs = ["English"] if args.skip_llm else (args.langs or langs_all())

    print("=" * 74)
    print("India Drought Monitor — build")
    print("  model      : %s" % model)
    print("  ollama url : %s" % ollama_url)
    print("  stages     : %s" % ", ".join(stages))
    print("  languages  : %d (%s%s)" % (len(langs), ", ".join(langs[:6]),
                                        " ..." if len(langs) > 6 else ""))
    print("  skip-llm   : %s" % args.skip_llm)
    print("=" * 74)

    # Fail fast if the model is needed but unreachable.
    if not args.skip_llm:
        sys.path.insert(0, str(REPO))
        import idm_llm  # imported AFTER env is set so it picks up the overrides
        ok, msg = idm_llm.health()
        if not ok:
            print("\n!! LLM health check FAILED:\n   %s" % str(msg)[:300])
            print("\n   Start Ollama and pull the model, e.g.:")
            print("     ollama pull %s" % model)
            print("     ollama serve            # if not already running")
            print("   Or do an English-only build with:  python3 build.py --skip-llm")
            sys.exit(2)
        print("LLM health: OK  ->  %s" % str(msg)[:80])

    # ---- districts (optional, network) -------------------------------------
    if "districts" in stages:
        run("Districts — state/grid/district stats (fetches GeoJSON)",
            [PY, "build_districts.py"])

    # ---- pdf static strings — translate the hydro PDF labels/headings (cached) --
    if "pdfstrings" in stages and not args.skip_llm:
        cmd = [PY, "translate_pdf_strings.py", "--langs"] + langs
        if args.force:
            cmd += ["--force"]
        run("Hydro PDF static text — gemma4 translation (cached, one-time)", cmd)

    # ---- website UI strings — translate the site's static text for the picker (cached) --
    if "uistrings" in stages and not args.skip_llm:
        cmd = [PY, "translate_ui_strings.py", "--langs"] + langs
        if args.force:
            cmd += ["--force"]
        run("Website UI text — gemma4 translation (cached, one-time; ~480 strings/lang, slow)", cmd)

    # ---- hydro — maps, dashboards, English summary, all-language hydro PDFs --
    if "hydro" in stages:
        cmd = [PY, "generate_hydro_outputs.py", "--repo", ".",
               "--api", ollama_url, "--model", model,
               "--translate-api", ollama_url, "--translate-model", model,
               "--pdf-engine", args.pdf_engine]
        if args.date:
            cmd += ["--date", args.date]
        if args.skip_llm:
            cmd += ["--no-llm"]
        cmd += ["--langs"] + langs
        run("Hydro outputs — gemma4 prose + per-language translation", cmd)
        # refresh the website manifests (dashboards/maps/month + per-language PDF list)
        mcmd = [PY, "build_hydro_manifests.py"]
        if args.date:
            mcmd += ["--date", args.date]
        run("Website hydro manifests (dashboards, maps, per-language reports)", mcmd)

    # ---- summary — weekly CDI summary PDFs (translate + render) -------------
    if "summary" in stages:
        cmd = [PY, "generate_summary_pdfs.py", "--langs"] + langs
        if args.force:
            cmd += ["--force"]
        run("National summary PDFs — gemma4 translation + render", cmd)

    # ---- forecast — forecast PDFs (self-seeds index, translate + render) ----
    if "forecast" in stages:
        cmd = [PY, "generate_forecast_pdfs.py", "--langs"] + langs
        if args.force:
            cmd += ["--force"]
        run("Forecast PDFs — gemma4 translation + render", cmd)

    # ---- schema — database schema PDF (no LLM) -----------------------------
    if "schema" in stages:
        run("Database schema PDF", [PY, "generate_schema_pdf.py"])

    print("\n" + "=" * 74)
    print("Build complete.  Review the changes, then commit and push:")
    print('    git add -A')
    print('    git commit -m "Rebuild portal (gemma4:e2b, %d languages)"' % len(langs))
    print('    git push')
    print("=" * 74)


if __name__ == "__main__":
    main()
