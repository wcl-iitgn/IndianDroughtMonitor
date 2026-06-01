#!/usr/bin/env python3
"""
translate_texts.py
==================
Generate the non-English text bundles for the India Drought Monitor by translating
the English source files in `Texts/English/` with the lab's `sarvam-translate` model,
served locally through Ollama.

It produces, for every language in `Texts/languages.json` (except English):
    Texts/<Language>/ui.json     (website strings)
    Texts/<Language>/pdf.json    (PDF report static strings)

The English files are the single source of truth; this script never edits them.

Backend (Ollama, sarvam-translate)
----------------------------------
    POST http://<host>:11434/api/generate
    {
      "model":  "sarvam-fp",
      "system": "Translate the text below to <Language>.",
      "prompt": "<one piece of source text>",
      "stream": false
    }  ->  {"response": "<translated text>", ...}

sarvam-translate is an instruction-tuned LLM that translates a whole passage per call
(identified by the LANGUAGE NAME in the system prompt — not an NLLB code), so we send
each JSON value as one request rather than splitting into sentences.

Usage
-----
    python3 translate_texts.py                      # translate ALL languages, ui + pdf
    python3 translate_texts.py --langs Hindi Tamil  # only these languages
    python3 translate_texts.py --only ui            # only the website bundle
    python3 translate_texts.py --only pdf
    python3 translate_texts.py --api http://10.0.60.193:11434/api/generate
    python3 translate_texts.py --model sarvam-fp
    python3 translate_texts.py --force              # re-translate even if the target file exists
    python3 translate_texts.py -v                   # verbose: log every string + timing

Notes
- Only string VALUES are translated; JSON keys and structure are preserved exactly.
- Placeholders like {observation_date}, {issue_date}, {n} and HTML-ish tokens are protected
  from translation and restored afterwards, so formatting/interpolation keeps working.
- Short "do-not-translate" values (emails, URLs, pure numbers, proper-noun codes) are skipped.
- The `chatbot` scope in ui.json is intentionally NOT translated (the assistant stays in English);
  the runtime falls back to the English chatbot strings for languages that omit them.
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error

REPO = Path(__file__).resolve().parent
TEXTS = REPO / "Texts"
ENGLISH = TEXTS / "English"
DEFAULT_API = "http://10.0.60.193:11434/api/generate"
DEFAULT_MODEL = "sarvam-fp"

# scopes (top-level keys in ui.json) to leave untranslated
SKIP_SCOPES = {"chatbot"}

log = logging.getLogger("translate")

# Tokens we must NOT send to the translator (restored verbatim afterwards).
PLACEHOLDER_RE = re.compile(r"(\{[a-zA-Z0-9_]+\}|<[^>]+>|&[a-z]+;|%[sd])")
# Values that should be passed through untranslated entirely.
SKIP_RE = re.compile(r"^\s*$|^[\W\d_]+$|@|https?://|www\.|\.(in|com|org|gov|ac)\b", re.I)
DONT_TRANSLATE_VALUES = {
    "IIT Gandhinagar", "vmishra@iitgn.ac.in", "paras.sharma@iitgn.ac.in",
    "24350007@iitgn.ac.in", "www.indiahydrolook.in",
}



def load_languages():
    data = json.loads((TEXTS / "languages.json").read_text(encoding="utf-8"))
    return data["languages"]


def should_skip(value):
    if not isinstance(value, str):
        return True
    v = value.strip()
    if v == "" or v in DONT_TRANSLATE_VALUES:
        return True
    # if, after removing placeholders, there are no letters, skip (pure symbols/numbers)
    bare = PLACEHOLDER_RE.sub("", v)
    if not re.search(r"[A-Za-z\u00C0-\u024F]", bare):
        return True
    if SKIP_RE.match(v) and not re.search(r"[A-Za-z]{3,}", bare):
        return True
    return False


def protect(text):
    """Replace placeholders with sentinels the MT model will leave intact."""
    mapping = {}
    def repl(m):
        token = m.group(0)
        key = "\u2486%d\u2487" % len(mapping)  # rare bracketed digits, survive MT well
        mapping[key] = token
        return key
    return PLACEHOLDER_RE.sub(repl, text), mapping


def restore(text, mapping):
    for key, token in mapping.items():
        text = text.replace(key, token)
    # MT sometimes drops the exact sentinel; tolerate minor spacing around it
    return text


# Leading/trailing UI glyphs (play/stop/arrows/download/bullet/ellipsis…) confuse the
# translator (it loops or balloons the output on an almost-textless prompt). Peel them off
# before translating and re-attach afterwards so they pass through untouched.
_AFFIX_GLYPHS = (
    "\u25b6\u25c0\u25a0\u25aa\u23f5\u23f8\u23f9"   # ▶ ◀ ■ ▪ ⏵ ⏸ ⏹
    "\u2b07\u2b06\u2b05\u27a1\u2193\u2191\u2190\u2192\u21bb\u21ba\u27f3"  # ⬇ ⬆ ⬅ ➡ ↓ ↑ ← → ↻ ↺ ⟳
    "\u2022\u00b7\u2219\u2026\u2014\u2013\u00bb\u00ab\u203a\u2039"  # • · ∙ … — – » « › ‹
    "\u2605\u2606\u2713\u2717\u2192\ufe0f \t"
)
_AFFIX_RE_L = re.compile(r"^[\s%s]+" % re.escape(_AFFIX_GLYPHS))
_AFFIX_RE_R = re.compile(r"[\s%s]+$" % re.escape(_AFFIX_GLYPHS))

def split_affix_glyphs(text):
    """Return (lead, core, trail): UI glyphs/whitespace stripped from the ends, plus the
    translatable core. core may be empty if the string was glyph-only."""
    lead_m = _AFFIX_RE_L.search(text)
    lead = lead_m.group(0) if lead_m else ""
    rest = text[len(lead):]
    trail_m = _AFFIX_RE_R.search(rest)
    trail = trail_m.group(0) if trail_m else ""
    core = rest[:len(rest) - len(trail)] if trail else rest
    return lead, core, trail


def _has_text(s):
    """True if the value contains letters worth translating (not pure punctuation/markup)."""
    bare = PLACEHOLDER_RE.sub("", s or "")
    return bool(re.search(r"[A-Za-z\u00C0-\u024F\u0900-\u0DFF]", bare))


def _clean_response(text):
    """sarvam-translate is well-behaved, but defensively strip any wrapping the model
    might add (code fences, surrounding quotes, a leading 'Translation:' label)."""
    if text is None:
        return ""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = re.sub(r"^(?:translation|translated text|output)\s*[:\-]\s*", "", t, flags=re.I)
    if len(t) >= 2 and t[0] in "\"'\u201c\u2018" and t[-1] in "\"'\u201d\u2019":
        t = t[1:-1].strip()
    return t


class Translator:
    """Translate text into a target language with sarvam-translate via Ollama.

    The model is prompt-based: each call translates ONE passage, with the target
    language given by NAME in the system prompt. We therefore translate one JSON value
    per request (not sentence-batched), protect placeholders, cache by value, and retry
    transient failures. Rich progress logging is emitted via the module logger."""

    def __init__(self, api_url, tgt_lang_name, model=DEFAULT_MODEL, retries=4, sleep=2.0,
                 timeout=300, temperature=0.1, keep_alive="30m", fail_soft=True):
        self.api_url = api_url
        self.tgt_lang = tgt_lang_name          # human-readable language NAME (e.g. "Hindi")
        self.model = model
        self.retries = retries
        self.sleep = sleep
        self.timeout = timeout
        self.temperature = temperature
        self.keep_alive = keep_alive           # keep the model resident between calls
        self.fail_soft = fail_soft             # on give-up, keep English instead of aborting
        self.system = "Translate the text below to %s." % tgt_lang_name
        self._cache = {}
        # stats for logging
        self.calls = 0
        self.cache_hits = 0
        self.server_seconds = 0.0
        self.errors = 0
        self.fallbacks = 0

    def _num_predict(self, prompt):
        """Cap output tokens so a degenerate generation (e.g. on an icon-only string) can't
        run until the socket times out. Indic scripts need more tokens than English, so be
        generous but bounded: ~6x the word count, min 48, max 1024."""
        words = max(1, len(prompt.split()))
        return max(48, min(1024, words * 6 + 24))

    def _post_one(self, prompt):
        body = json.dumps({
            "model": self.model,
            "system": self.system,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": self._num_predict(prompt),
            },
        }).encode("utf-8")
        req = urllib.request.Request(self.api_url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        resp_text = data.get("response")
        if resp_text is None:
            raise RuntimeError("Ollama response missing 'response' field: %.120s" % json.dumps(data))
        srv = data.get("total_duration")
        return resp_text, (srv / 1e9 if isinstance(srv, (int, float)) else None)

    def translate_one(self, value, idx=None, total=None):
        """Translate a single value (whole string). Returns the translated string.
        On repeated failure: keep the English value (fail_soft) or raise (strict)."""
        if value in self._cache:
            self.cache_hits += 1
            log.debug("  [cache] %s", _short(value))
            return self._cache[value]
        if not _has_text(value):
            self._cache[value] = value
            return value

        # peel UI glyphs off the ends so the model only sees real text
        lead, core, trail = split_affix_glyphs(value)
        if not _has_text(core):
            self._cache[value] = value           # glyph-only -> leave as-is
            return value

        prot, mapping = protect(core)
        prefix = ("[%d/%d] " % (idx, total)) if (idx is not None and total) else ""
        last_err = None
        for attempt in range(1, self.retries + 1):
            try:
                t0 = time.time()
                raw, srv = self._post_one(prot)
                wall = time.time() - t0
                self.calls += 1
                if srv:
                    self.server_seconds += srv
                out_core = restore(_clean_response(raw), mapping)
                out = lead + out_core + trail     # re-attach glyphs
                expected = set(mapping.values())
                missing = [tok for tok in expected if tok not in out]
                if missing:
                    log.warning("  %splaceholder(s) missing after translate, restoring inline: %s",
                                prefix, ", ".join(missing[:4]))
                log.info("  %s%s  (%.2fs%s)  %s  ->  %s",
                         prefix, _short(value), wall,
                         (", srv %.2fs" % srv) if srv else "",
                         _len(value), _len(out))
                self._cache[value] = out
                return out
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as e:
                last_err = e
                log.warning("  %sattempt %d/%d failed: %s", prefix, attempt, self.retries, e)
                if attempt < self.retries:
                    time.sleep(min(30.0, self.sleep * (2 ** (attempt - 1))))   # exponential backoff
        # exhausted retries
        self.errors += 1
        if self.fail_soft:
            self.fallbacks += 1
            log.error("  %sgiving up after %d tries; keeping English for this string: %s",
                      prefix, self.retries, _short(value))
            self._cache[value] = value          # keep English so the run continues
            return value
        raise RuntimeError("translation failed after %d retries (is Ollama reachable at %s, model %r?): %s"
                           % (self.retries, self.api_url, self.model, last_err))

    def translate_many(self, texts, log=None):
        """Translate a list of strings (one Ollama call each). `log` arg kept for API
        compatibility with earlier callers; progress goes through the module logger."""
        results = []
        total = len(texts)
        for i, t in enumerate(texts, 1):
            results.append(self.translate_one(t, idx=i, total=total))
        return results


def _short(s, n=60):
    s = re.sub(r"\s+", " ", s or "").strip()
    return (s[:n] + "\u2026") if len(s) > n else s

def _len(s):
    w = len((s or "").split())
    return "%dw" % w

def _wps(translator, elapsed):
    return (translator.calls / elapsed) if elapsed > 0 else 0.0


def collect_strings(obj, path=""):
    """Walk a JSON structure, yielding (path, value) for every translatable string."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from collect_strings(v, path + "/" + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from collect_strings(v, path + "/[%d]" % i)
    elif isinstance(obj, str):
        yield path, obj


def set_by_path(obj, path, value):
    parts = [p for p in path.split("/") if p != ""]
    cur = obj
    for p in parts[:-1]:
        if p.startswith("[") and p.endswith("]"):
            cur = cur[int(p[1:-1])]
        else:
            cur = cur[p]
    last = parts[-1]
    if last.startswith("[") and last.endswith("]"):
        cur[int(last[1:-1])] = value
    else:
        cur[last] = value


def _top_scope(path):
    """The leading scope name in a collect_strings path like '/common_nav/about'."""
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else ""


def translate_file(src_path, dst_path, translator):
    src = json.loads(src_path.read_text(encoding="utf-8"))
    pairs = list(collect_strings(src))

    skipped_scope = sum(1 for p, _ in pairs if _top_scope(p) in SKIP_SCOPES)
    # translate everything that has text AND isn't in a skipped scope (e.g. chatbot)
    to_translate = [(p, v) for p, v in pairs
                    if _top_scope(p) not in SKIP_SCOPES and not should_skip(v)]
    passthrough = len(pairs) - len(to_translate) - skipped_scope

    log.info("%s: %d strings  (translate %d, skip-scope %d [%s], passthrough %d)",
             src_path.name, len(pairs), len(to_translate), skipped_scope,
             ",".join(sorted(SKIP_SCOPES)), passthrough)

    out = json.loads(json.dumps(src))  # deep copy; untranslated values stay as English
    t0 = time.time()
    total = len(to_translate)
    for i, (path, value) in enumerate(to_translate, 1):
        tr = translator.translate_one(value, idx=i, total=total)
        set_by_path(out, path, tr)
    elapsed = time.time() - t0

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("-> wrote %s  (%d translated in %.1fs, %.2f/s; %d cache hits, %d errors, %d kept-English)",
             dst_path, total, elapsed, (total / elapsed if elapsed > 0 else 0.0),
             translator.cache_hits, translator.errors, translator.fallbacks)



def main():
    ap = argparse.ArgumentParser(description="Translate the English text bundles into other languages "
                                             "with sarvam-translate via Ollama.")
    ap.add_argument("--api", default=DEFAULT_API, help="Ollama /api/generate URL")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name (default: %s)" % DEFAULT_MODEL)
    ap.add_argument("--langs", nargs="*", default=None,
                    help="languages to build (default: all non-English in languages.json)")
    ap.add_argument("--only", choices=["ui", "pdf"], default=None, help="only one bundle")
    ap.add_argument("--force", action="store_true", help="re-translate even if target exists")
    ap.add_argument("--retries", type=int, default=4, help="retries per string on transient errors")
    ap.add_argument("--timeout", type=int, default=300, help="per-request timeout (seconds)")
    ap.add_argument("--keep-alive", default="30m", help="how long Ollama keeps the model resident")
    ap.add_argument("--strict", action="store_true",
                    help="abort on a string that fails all retries (default: keep English and continue)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every string + cache hits")
    ap.add_argument("-q", "--quiet", action="store_true", help="only warnings/errors")
    args = ap.parse_args()

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")

    langs = load_languages()
    targets = [l for l in langs if l["key"] != "English"]
    if args.langs:
        want = set(x.lower() for x in args.langs)
        targets = [l for l in targets if l["key"].lower() in want or l["code"].lower() in want]
        if not targets:
            sys.exit("No matching languages. Available: %s" % ", ".join(l["key"] for l in langs))

    bundles = ["ui", "pdf"] if args.only is None else [args.only]
    log.info("Translating %d language(s) via %s (model: %s)",
             len(targets), args.api, args.model)
    grand_t0 = time.time()
    built = 0
    for lang in targets:
        name = lang.get("label") or lang["key"]
        log.info("\n=== %s  [system prompt: \"Translate the text below to %s.\"] ===", lang["key"], name)
        tr = Translator(args.api, name, model=args.model, retries=args.retries,
                        timeout=args.timeout, keep_alive=args.keep_alive,
                        fail_soft=not args.strict)
        for b in bundles:
            src = ENGLISH / ("%s.json" % b)
            if not src.exists():
                log.warning("  ! missing source %s — skipping", src)
                continue
            dst = TEXTS / lang["key"] / ("%s.json" % b)
            if dst.exists() and not args.force:
                log.info("  %s exists (use --force to overwrite) — skipping", dst)
                continue
            translate_file(src, dst, tr)
            built += 1
        log.info("  %s totals: %d server calls, %.1fs server time, %d cache hits, %d errors, %d kept-English",
                 lang["key"], tr.calls, tr.server_seconds, tr.cache_hits, tr.errors, tr.fallbacks)
    log.info("\nDone. Built %d file(s) for %d language(s) in %.1fs.",
             built, len(targets), time.time() - grand_t0)


if __name__ == "__main__":
    main()
