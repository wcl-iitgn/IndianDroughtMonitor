#!/usr/bin/env python3
# =============================================================================
# idm_llm.py  --  the ONE place the India Drought Monitor talks to a language model
# -----------------------------------------------------------------------------
# Every LLM need in the pipeline goes through here:
#   * generate()  -> write English prose (the weekly summary, the PDF paragraphs)
#   * translate() -> translate that text into any supported language
#
# Both are backed by a single Ollama /api/generate server running ONE model
# (gemma4:e2b by default). That is the whole simplification: text generation and
# translation are the same model, so there is no separate translation service and
# no "generate first, translate second, with a different backend" juggling.
#
# Stateless by design: every call is a fresh, independent request. No KV cache
# reuse is relied on, no conversation history is kept.
#
# Configure with environment variables (so no script needs editing to retarget):
#   IDM_OLLAMA_URL   default  http://10.0.60.193:11434/api/generate
#   IDM_LLM_MODEL    default  gemma4:e2b
#   IDM_LLM_NUM_CTX  default  8192          (fits the chatbot/summary/translation prompts)
#   IDM_LLM_TIMEOUT  default  300           (seconds per request)
#
# Quick check that the server + model are reachable:
#   python3 idm_llm.py
# =============================================================================

import json
import os
import re
import time
import unicodedata
import urllib.request

OLLAMA_URL = os.environ.get("IDM_OLLAMA_URL", "http://10.0.60.193:11434/api/generate")
MODEL = os.environ.get("IDM_LLM_MODEL", "gemma4:e2b")
NUM_CTX = int(os.environ.get("IDM_LLM_NUM_CTX", "8192"))
TIMEOUT = int(os.environ.get("IDM_LLM_TIMEOUT", "300"))


def _post(body, timeout=None, retries=2, url=None):
    """POST to Ollama /api/generate with a couple of retries. Returns the parsed JSON."""
    endpoint = url or OLLAMA_URL
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                endpoint, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - surface a clean message after retries
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("Ollama request to %s failed: %s" % (endpoint, last))


def _clean(txt):
    """Strip any stray reasoning block (thinking is disabled, but be defensive) and trim.
    Handles the <think>...</think> form and, if it ever leaks, Gemma 4's channel form."""
    if not txt:
        return ""
    if "channel" in txt:
        # Gemma 4 thinking, if leaked: keep what follows the last close tag, drop tags.
        m = list(re.finditer(r"<\s*channel\s*\|?\s*>", txt))
        if m:
            txt = txt[m[-1].end():]
        txt = re.sub(r"<\s*\|?\s*channel\s*\|?\s*>", "", txt)
    if "</think>" in txt:
        txt = txt.split("</think>")[-1]
    return txt.strip()


# Leading "Here is the translation:" / "Sure:" style preambles a small model may emit.
_PREAMBLE_RE = re.compile(
    r"^\s*(?:here(?:\s+is|'s)\s+the\s+translation[^:\n]*|here(?:\s+is|'s)[^:\n]{0,40}|"
    r"sure[,!.]?|certainly[,!.]?|okay[,!.]?|translation)\s*:\s*",
    re.IGNORECASE)


def _sanitize_translation(text):
    """Make model output safe + clean for a LaTeX/PDF target.
    Removes any leading preamble, strips markdown/formatting symbols (which break or
    clutter LaTeX), and collapses line breaks so a blank line can't become a \\par
    inside a macro argument. The result is a single tidy paragraph."""
    if not text:
        return text
    t = text.strip()
    # peel a leading preamble, but only if real content follows
    m = _PREAMBLE_RE.match(t)
    if m and (len(t) - m.end()) > 10:
        t = t[m.end():].lstrip()
    # strip surrounding quotes the model sometimes adds
    if len(t) >= 2 and t[0] in "\"'\u201c\u00ab" and t[-1] in "\"'\u201d\u00bb":
        t = t[1:-1].strip()
    # remove markdown / formatting markers
    t = t.replace("**", "").replace("__", "").replace("`", "")
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)   # ATX headings at line starts
    t = t.replace("*", "")                          # stray emphasis asterisks
    # drop LaTeX grouping/command chars (prose never needs them; protects PDF + website)
    t = t.replace("\\", "").replace("{", "").replace("}", "")
    # collapse hard wraps / blank lines to single spaces (no \par inside LaTeX args)
    t = re.sub(r"\s*\n\s*", " ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def is_english(language):
    return str(language or "").strip().lower() in ("english", "eng", "en", "eng_latn")


def generate(prompt, system=None, temperature=0.7, top_p=0.9,
             num_predict=600, model=None, timeout=None, url=None):
    """One fresh-inference completion. Returns the model's text (thinking off)."""
    body = {
        "model": model or MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature, "top_p": top_p,
                    "num_ctx": NUM_CTX, "num_predict": num_predict},
    }
    if system:
        body["system"] = system
    return _clean(_post(body, timeout=timeout, url=url).get("response", ""))


def translate(text, target_language, model=None, timeout=None, url=None, num_predict=1536,
              temperature=0.1, extra_rules=None):
    """Translate English `text` into `target_language`. English is a no-op.

    The prompt forbids markdown and preambles; the output is then sanitized so it is
    safe to drop straight into a LaTeX/PDF document (no stray ``**``, ``#`` or blank
    lines). Numbers, units, dates and place names are preserved. `num_predict` caps the
    output length so a low-resource language can't ramble toward the context limit (the
    longest string we translate is a paragraph, well under this). `extra_rules` lets a
    caller append further numbered rules to the system prompt (e.g. a length budget —
    see translate_bounded)."""
    if not text or not text.strip() or is_english(target_language):
        return text
    system = (
        "You are a professional translator. Translate the user's text into %s.\n"
        "Rules:\n"
        "1. Output ONLY the translation - no introduction, no notes, no quotation marks.\n"
        "2. Use plain text with NO markdown or symbols: no *, no **, no _, no #, no backticks.\n"
        "3. Keep every number, unit, date and place name exactly as given.\n"
        "4. Write it as one flowing paragraph; do not insert blank lines."
        % target_language
    )
    if extra_rules:
        system += "\n" + extra_rules.strip()
    body = {
        "model": model or MODEL,
        "system": system,
        "prompt": text,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature, "top_p": 0.9,
                    "num_ctx": NUM_CTX, "num_predict": num_predict},
    }
    out = _clean(_post(body, timeout=timeout, url=url).get("response", text))
    return _sanitize_translation(out) or text


def translate_many(texts, target_language, model=None):
    """Translate a list of strings one-by-one (keeps each segment aligned)."""
    return [translate(t, target_language, model=model) for t in texts]


# --------------------------------------------------------------------------- length-bounded translation
# A translation that must fit a fixed-height PDF box cannot be longer than the
# English it replaces (the layout was budgeted around the English word targets).
# translate_bounded() enforces that contract:
#   1. it tells the model up front to match the source length,
#   2. it measures the result (in code points excluding non-spacing marks, so
#      viramas/anusvara/conjunct marks don't unfairly inflate the count),
#   3. if too long it retries with explicit "you were N% over" feedback,
#   4. it keeps the best attempt, and as a last resort trims whole sentences
#      (never mid-sentence) down to the hard budget.
TRANSLATE_SOFT_RATIO = 1.20    # accept immediately at or below this ratio to source
TRANSLATE_HARD_RATIO = 1.30    # after all attempts, sentence-clamp down to this
TRANSLATE_MAX_ATTEMPTS = 3     # 1 initial try + up to 2 "shorten" retries

_CONCISE_RULE = (
    "5. LENGTH LIMIT: the translation must be about the SAME LENGTH as the original "
    "text, and never longer. Translate faithfully but economically; do not elaborate, "
    "explain, or add anything that is not in the original."
)

# sentence terminators across the site's scripts: Latin . ! ?  Devanagari danda/double
# danda (। ॥), Urdu full stop (۔) and Arabic question mark (؟).
_SENT_SPLIT = re.compile(u"(?<=[.!?\u0964\u0965\u06d4\u061f])\\s+")


def visual_len(s):
    """Length excluding non-spacing marks (anusvara, virama, above/below vowel signs):
    a fairer cross-script size measure, since those marks take no horizontal space.
    Spacing matras still count, as they do consume width."""
    return sum(1 for ch in (s or "") if unicodedata.category(ch) not in ("Mn", "Me"))


def clamp_sentences_to_chars(text, max_chars):
    """Drop whole sentences from the end until visual_len(text) <= max_chars.
    Always keeps at least the first sentence; never cuts mid-sentence."""
    if visual_len(text) <= max_chars:
        return text
    parts = _SENT_SPLIT.split(text.strip())
    out, n = [], 0
    for s in parts:
        sl = visual_len(s) + (1 if out else 0)
        if out and n + sl > max_chars:
            break
        out.append(s)
        n += sl
    return " ".join(out).strip() or text


def translate_bounded(text, target_language, model=None, timeout=None, url=None,
                      soft_ratio=TRANSLATE_SOFT_RATIO, hard_ratio=TRANSLATE_HARD_RATIO,
                      attempts=TRANSLATE_MAX_ATTEMPTS, label=None, log=print):
    """Translate `text` with a length budget relative to the source, for prose that
    must fit a fixed PDF box. Returns text whose visual length is at most
    hard_ratio x the source (except a single over-long sentence, which is never cut)."""
    if not text or not text.strip() or is_english(target_language):
        return text
    src_len = max(1, visual_len(text))
    best = None  # (ratio, output)
    for attempt in range(1, attempts + 1):
        rules = _CONCISE_RULE
        if attempt > 1 and best is not None:
            over = max(1, int(round((best[0] - 1.0) * 100)))
            rules += ("\n6. Your previous translation was about %d%% longer than the "
                      "original. This attempt MUST be shorter: keep every fact, but cut "
                      "filler words and use the most compact natural phrasing." % over)
        out = translate(text, target_language, model=model, timeout=timeout, url=url,
                        temperature=(0.1 if attempt == 1 else 0.3), extra_rules=rules)
        ratio = visual_len(out) / float(src_len)
        if best is None or ratio < best[0]:
            best = (ratio, out)
        if ratio <= soft_ratio:
            return out
    ratio, out = best
    if ratio > hard_ratio:
        clamped = clamp_sentences_to_chars(out, int(src_len * hard_ratio))
        if label:
            log("    (%s: %.2fx source after %d tries -> clamped to %.2fx)"
                % (label, ratio, attempts, visual_len(clamped) / float(src_len)))
        return clamped
    if label:
        log("    (%s: %.2fx source, within the hard limit)" % (label, ratio))
    return out


def health():
    """Return (ok, message): does the server answer and the model load?"""
    try:
        out = generate("Reply with the single word: ok",
                        temperature=0.0, num_predict=8, timeout=60)
        return True, out
    except Exception as e:  # noqa: BLE001
        return False, str(e)


if __name__ == "__main__":
    ok, msg = health()
    print(("OK  -> " if ok else "FAIL -> ") + str(msg)[:300])
    print("url=%s  model=%s  num_ctx=%d" % (OLLAMA_URL, MODEL, NUM_CTX))
    raise SystemExit(0 if ok else 1)
