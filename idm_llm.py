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


def translate(text, target_language, model=None, timeout=None, url=None, num_predict=1536):
    """Translate English `text` into `target_language`. English is a no-op.

    The prompt forbids markdown and preambles; the output is then sanitized so it is
    safe to drop straight into a LaTeX/PDF document (no stray ``**``, ``#`` or blank
    lines). Numbers, units, dates and place names are preserved. `num_predict` caps the
    output length so a low-resource language can't ramble toward the context limit (the
    longest string we translate is a paragraph, well under this)."""
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
    body = {
        "model": model or MODEL,
        "system": system,
        "prompt": text,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "top_p": 0.9,
                    "num_ctx": NUM_CTX, "num_predict": num_predict},
    }
    out = _clean(_post(body, timeout=timeout, url=url).get("response", text))
    return _sanitize_translation(out) or text


def translate_many(texts, target_language, model=None):
    """Translate a list of strings one-by-one (keeps each segment aligned)."""
    return [translate(t, target_language, model=model) for t in texts]


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
