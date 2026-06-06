# Running the India Drought Monitor pipeline

One model — **`gemma4:e2b`** on a local Ollama server — does the *build-time* text:
the PDF prose, the weekly national summary, and the translation of that text (and
the PDFs' static headings/About text) into all 22 languages. There is no separate
translation service and no multi-stage ordering to manage by hand.

The **chatbot is separate**: at runtime it talks to the WCL OpenAI API on
PythonAnywhere (`wcliitgnopenaiapi.pythonanywhere.com`) — Ollama is *not* needed to
run or test the chatbot. See section 3 for the port requirement that implies.

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

## 3. Serve & test locally — **use port 5500**

```bash
python3 -m http.server 5500        # NOT 8000
open http://localhost:5500
```

The chatbot's backend only allows the origins `localhost:5500`, `127.0.0.1:5500`
and `indiadroughtmonitor.in` (+www). On any other port the site works but every
chatbot call is blocked by CORS. (If production is still served from
`wcl-iitgn.github.io`, Pranav must add that origin to the CORS list in
`backend.py` before the chatbot works there.)

Browser checklist after a build:

1. **Current page** — hover the map: District fills in the readout; drag
   *Smoothness* 1→6; switch a week; Download PNG.
2. **Languages** — switch to Hindi etc.; untranslated strings (the pending
   Gemini batch) intentionally show in English.
3. **Chatbot** — sign in with any username (blank password = 5-minute session);
   ask two questions; open a second browser, sign in with the *same* username:
   the chat follows you. Wait 5 minutes → it asks you to sign in again. Try
   "Request permanent access".
4. **Admin console** — `http://localhost:5500/chatbot-admin.html` (unlinked).
   Sign in with the seeded admin, approve a pending privilege request (copy the
   one-time password it shows), then rotate the seeded admin: create a new
   admin user, sign in as it, delete the old `admin` row.
5. **Hydro / Summary / Forecast** — open the latest PDFs and the publications,
   disclaimer and about (acknowledgements section) pages.

## 4. Verified end-to-end run (fast path, no model)

This exact sequence was run on a clean unzip of the delivered build and timed:

```bash
python3 -m pip install --break-system-packages numpy scipy pandas matplotlib reportlab requests
python3 build.py --only schema     --skip-llm                            # ~1 s
python3 build.py --only uistrings  --skip-llm                            # no-op (languages exist)
python3 build.py --only pdfstrings --skip-llm                            # no-op (caches exist)
python3 build.py --only hydro      --skip-llm --pdf-engine matplotlib    # ~52 s
python3 build.py --only summary    --skip-llm                            # ~75 s (incremental: 1 new week)
python3 build.py --only forecast   --skip-llm                            # ~27 s
python3 -m http.server 5500
```

Notes from that run:

* Stages are **incremental** — summary/forecast render only weeks that are
  missing, so routine re-runs take minutes, not hours.
* `--pdf-engine matplotlib` is the fallback when XeLaTeX/MacTeX isn't
  installed; on the lab Mac with MacTeX, omit it to get the LaTeX PDFs.
* Without `--skip-llm`, build.py first health-checks Ollama; if the LAN server
  is unreachable that check burns ~3 minutes in timeouts before falling back —
  pass `--skip-llm` (or `--ollama-url http://localhost:11434/api/generate`)
  when you're away from the lab network.
* The full multilingual build is the same commands without `--skip-llm`
  (Ollama reachable); per-language text is cached, so only missing
  translations are generated.
