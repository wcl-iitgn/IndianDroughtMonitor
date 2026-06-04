# Running the India Drought Monitor pipeline

One model — **`gemma4:e2b`** on a local Ollama server — does everything: the PDF
prose, the weekly national summary, the translation of that text (and the PDFs'
static headings/About text) into all 22 languages, and the chatbot. There is no separate translation service and no
multi-stage ordering to manage by hand.

## 0. One-time setup (Mac)

```bash
# Python deps (no virtualenv; system Python)
python3 -m pip install --break-system-packages numpy scipy pandas matplotlib reportlab requests

# PDFs are built with XeLaTeX (MacTeX) + the language fonts you already installed
# (Carlito for English; Noto Sans Devanagari/Bengali/Gujarati/Gurmukhi/Oriya/Tamil/
#  Telugu/Kannada/Malayalam/Ol Chiki; Noto Naskh Arabic + Noto Nastaliq Urdu for RTL).

# The model:
ollama pull gemma4:e2b
ollama serve         # if it isn't already running
```

The pipeline talks to Ollama over plain HTTP and needs **no API key** — a local
Ollama server is unauthenticated. The only thing to point at is the server URL.

## 1. Tell the pipeline where Ollama is

Default is the lab LAN server `http://10.0.60.193:11434/api/generate`. To use a
different host (e.g. Ollama on the same Mac), either pass `--ollama-url` to
`build.py` or set it once in your shell:

```bash
export IDM_OLLAMA_URL="http://localhost:11434/api/generate"
export IDM_LLM_MODEL="gemma4:e2b"      # optional; this is the default
```

Sanity-check the model is reachable:

```bash
python3 idm_llm.py
# -> OK  ->  ok        (and the url/model it used)
```

## 2. Build — one command

```bash
python3 build.py
```

That runs, in order: **pdf-strings → hydro → summary → forecast → schema**.
`pdf-strings` translates the hydro PDF's *static* text (titles, section headings, the
long page-intros, the About/Disclaimer block) into each language — this is a one-time,
cached step (it skips a language whose `Texts/<lang>/pdf.json` already exists). Hydro
then generates the English prose + summary and writes `index.json`; the summary and
forecast stages translate that text into every language with gemma4 and render the
per-language PDFs. Each stage is idempotent, so re-running is safe.

Useful variants:

```bash
python3 build.py --langs English          # fast English-only pass (no translation)
python3 build.py --langs English Hindi     # just a couple of languages
python3 build.py --only hydro              # run a single stage
python3 build.py --skip-llm                # no model: English template summary
python3 build.py --force                   # re-translate even if text already exists
python3 build.py --with-districts          # also refresh district data (needs network)
python3 build.py --ollama-url http://localhost:11434/api/generate
```

The 22 languages are read from `Texts/languages.json`; edit that file to add or
remove a language (key, FLORES `code`, `native` label, `dir`, and `pdf_font`).

Because the static-text translation is slow on a small model (≈30–50 calls/language),
run it **once** and commit the result so it never repeats:

```bash
nohup python3 translate_pdf_strings.py > pdfstrings.log 2>&1 &   # all languages, cached
tail -f pdfstrings.log
git add Texts/*/pdf.json && git commit -m "Translated hydro PDF static strings"
```

After that, every `python3 build.py` skips `pdf-strings` instantly.

## 3. Commit — sequential, no surprises

`build.py` prints these at the end:

```bash
git add -A
git commit -m "Rebuild portal (gemma4:e2b, 22 languages)"
git push
```

## 4. Preview locally

```bash
python3 -m http.server 8000
# open http://localhost:8000
```

## Notes / current limits

* **Static UI text** (nav, buttons, page labels) and the on-page **language
  switcher** are deliberately *not* part of this pipeline yet — that's the
  separate one-time task. The per-language narrative text files are generated and
  committed now, so the switcher can consume them when it's added.
* For the same reason, **PDF labels/headings stay English**; only the dynamic
  prose (summary narrative, forecast narrative, hydro paragraphs) is translated.
* The chatbot uses **gemma4:e2b** when the provider is `ollama`, and keeps
  **DeepSeek** as the default provider (DeepSeek works on the public HTTPS site;
  the Ollama option is for local/intranet use, since browsers block plain-HTTP
  Ollama calls from an HTTPS page). The chatbot keeps **no conversation memory** —
  every question is a fresh inference.
* The forecast stage **self-seeds** `data/forecast_summaries/index.json` from the
  latest week if it's missing, so a clean checkout builds without manual setup.

## Website static text + language picker

The pages carry `data-i18n` keys on every static string. The localisation runtime
is `assets/i18n/i18n.js` (added to every page): it loads the chosen language,
swaps the text/attributes, injects a **language picker** into the header, and sets
`window.IDM_I18N` (which the summary/forecast/hydro pages read to load their
per-language content).

1. English strings are extracted into `Texts/ui.en.json`.
2. Translate them once (cached, like the PDF strings — slow, run in background):
   ```bash
   nohup python3 translate_ui_strings.py > uistrings.log 2>&1 &   # all languages
   git add assets/i18n/*.json && git commit -m "Translated website UI strings"
   ```
   For quality on harder scripts use a bigger model:
   `IDM_LLM_MODEL=gemma4:e4b python3 translate_ui_strings.py`.
3. Until a language's `assets/i18n/<Language>.json` exists, the picker falls back to
   English for that language (no error). English itself needs no file — its text is
   already in the HTML.

If you re-extract `Texts/ui.en.json` (e.g. after editing page text), re-run the
extractor that reads the `data-i18n` keys before re-translating.
