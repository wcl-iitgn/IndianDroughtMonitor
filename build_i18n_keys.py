#!/usr/bin/env python3
"""
build_i18n_keys.py  (build-time helper, run once / when copy changes)
=====================================================================
Externalize the website's English text into `Texts/English/ui.json` and inject
matching `data-i18n` / `data-i18n-attr` attributes into the HTML pages so the runtime
(`assets/i18n/i18n.js`) can swap languages.

What it does for every *.html in the repo root:
  * Finds user-visible text in "leaf" elements (no element children, just text) and
    assigns it a stable key.  Shared regions (header / nav / footer) are keyed under a
    single `common.*` scope and reused across pages (so "About", "Contact", etc. are
    translated once).  Everything else is keyed under the page id, e.g. `index.title1`.
  * Also externalizes translatable attributes: alt, title, placeholder, aria-label,
    and <option> labels / button labels.
  * Writes Texts/English/ui.json (merging, so re-runs are stable) and rewrites the HTML
    in place with data-i18n attributes.  Idempotent: elements already carrying data-i18n
    are left untouched.

It is conservative: it never externalizes <script>, <style>, code/pre, elements that are
purely dynamic placeholders (—, &mdash;, &hellip;), or strings with no letters.

Run:  python build_i18n_keys.py            (process all pages)
      python build_i18n_keys.py index.html summary.html   (specific pages)
"""

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Comment

REPO = Path(__file__).resolve().parent
TEXTS_EN = REPO / "Texts" / "English"
UI_JSON = TEXTS_EN / "ui.json"

SKIP_TAGS = {"script", "style", "code", "pre", "svg", "canvas", "noscript"}
ATTR_KEYS = ["alt", "title", "placeholder", "aria-label"]
# regions that are shared across pages -> keyed under common.*
COMMON_SELECTORS = [
    ("header.usdm-header", "common_header"),
    ("nav.usdm-nav", "common_nav"),
    ("footer.usdm-footer", "common_footer"),
]

PLACEHOLDER_ONLY = re.compile(r"^[\s\u2014\u2013\-\u2026.|/]*$")  # em/en dash, ellipsis, pipes


def has_letters(s):
    return re.search(r"[A-Za-z]", s) is not None


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def slugify(text, maxlen=28):
    w = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    w = re.sub(r"_+", "_", w)
    return (w[:maxlen]).strip("_") or "t"


def page_id(path):
    return re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")


class KeyAllocator:
    def __init__(self, scope, existing):
        self.scope = scope
        self.used = set(existing.get(scope, {}).keys())
        self.text_to_key = {v: k for k, v in existing.get(scope, {}).items()}
        self.counter = {}

    def key_for(self, text):
        if text in self.text_to_key:
            return self.text_to_key[text]
        base = slugify(text)
        cand = base
        n = 2
        while cand in self.used:
            cand = "%s_%d" % (base, n); n += 1
        self.used.add(cand)
        self.text_to_key[text] = cand
        return cand


def load_ui():
    if UI_JSON.exists():
        return json.loads(UI_JSON.read_text(encoding="utf-8"))
    return {}


def in_common_region(el):
    for sel, scope in COMMON_SELECTORS:
        tag, _, cls = sel.partition(".")
        anc = el.find_parent(tag, class_=cls or None)
        if anc is not None:
            return scope
    return None


INLINE_OK = {"strong", "em", "b", "i", "u", "small", "span", "a", "br", "sub", "sup", "code"}
# Only these inline tags trigger "collapse the whole block to innerHTML".
# <a> is intentionally excluded so nav menus and link lists get their <a>s keyed individually.
INLINE_FORMAT = {"strong", "em", "b", "i", "u", "small", "sub", "sup", "code", "br"}


def inner_html(el):
    return "".join(str(c) for c in el.children).strip()


def is_block_with_inline(el):
    """Element whose element-children are ONLY formatting inline tags (no anchors, no spans
    with ids) -> translate as a single unit via innerHTML. This captures paragraphs that
    contain <strong>/<em> etc. without splitting them, but leaves nav/link lists alone."""
    if el.name in SKIP_TAGS:
        return False
    child_tags = [c for c in el.children if getattr(c, "name", None)]
    if not child_tags:
        return False
    if any(c.name not in INLINE_FORMAT for c in child_tags):
        return False
    txt = norm(el.get_text())
    return bool(txt) and has_letters(txt) and not PLACEHOLDER_ONLY.match(txt)


def is_leaf_text_element(el):
    """True if el has no element children and meaningful text."""
    if el.name in SKIP_TAGS:
        return False
    child_tags = [c for c in el.children if getattr(c, "name", None)]
    if child_tags:
        return False
    txt = norm(el.get_text())
    return bool(txt) and has_letters(txt) and not PLACEHOLDER_ONLY.match(txt)


def process_page(path, ui, log=print):
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    pid = page_id(path)
    changed = 0

    # allocators per scope (lazily created)
    allocators = {}
    def alloc(scope):
        if scope not in allocators:
            allocators[scope] = KeyAllocator(scope, ui)
            ui.setdefault(scope, {})
        return allocators[scope]

    # 1) text elements — prefer keying a block (with simple inline children) as one unit
    keyed_blocks = []  # elements we've claimed; skip their descendants
    def inside_keyed(el):
        for b in keyed_blocks:
            if el is not b and b in el.parents:
                return True
        return False

    for el in soup.find_all(True):
        if el.name in SKIP_TAGS:
            continue
        if el.has_attr("data-i18n") or el.has_attr("data-i18n-skip"):
            keyed_blocks.append(el); continue
        if inside_keyed(el):
            continue
        scope = in_common_region(el) or pid
        if is_block_with_inline(el):
            val = norm(inner_html(el))
            a = alloc(scope); key = a.key_for(val)
            ui[scope][key] = val
            el["data-i18n"] = scope + "." + key
            el["data-i18n-html"] = "1"
            keyed_blocks.append(el)
            changed += 1
        elif is_leaf_text_element(el):
            text = norm(el.get_text())
            a = alloc(scope); key = a.key_for(text)
            ui[scope][key] = text
            el["data-i18n"] = scope + "." + key
            keyed_blocks.append(el)
            changed += 1

    # 1b) loose text nodes: significant text sitting directly inside an element that was
    #     NOT keyed as a whole (e.g. "<span class=swatch></span> No Drought"). Wrap in a span.
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS or el.has_attr("data-i18n") or inside_keyed(el):
            continue
        for child in list(el.children):
            if isinstance(child, NavigableString) and not isinstance(child, Comment):
                raw = str(child)
                text = norm(raw)
                if not text or not has_letters(text) or PLACEHOLDER_ONLY.match(text):
                    continue
                scope = in_common_region(el) or pid
                a = alloc(scope); key = a.key_for(text)
                ui[scope][key] = text
                span = soup.new_tag("span")
                span["data-i18n"] = scope + "." + key
                span.string = text
                lead = raw[:len(raw) - len(raw.lstrip())]
                trail = raw[len(raw.rstrip()):]
                child.replace_with(span)
                if lead:
                    span.insert_before(NavigableString(lead))
                if trail:
                    span.insert_after(NavigableString(trail))
                changed += 1

    # 2) attributes (alt/title/placeholder/aria-label) on any element
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS:
            continue
        attr_pairs = []
        existing = el.get("data-i18n-attr", "")
        already = set(p.split(":")[0].strip() for p in existing.split(";") if ":" in p)
        for attr in ATTR_KEYS:
            if not el.has_attr(attr):
                continue
            if attr in already:
                continue
            val = norm(el.get(attr))
            if not val or not has_letters(val) or PLACEHOLDER_ONLY.match(val):
                continue
            scope = in_common_region(el) or pid
            a = alloc(scope)
            clean = el.get(attr).strip()
            key = a.key_for(clean)          # dedup on the actual stored value (stable across pages)
            ui[scope][key] = clean
            attr_pairs.append("%s:%s.%s" % (attr, scope, key))
        if attr_pairs:
            merged = (existing + ";" if existing else "") + ";".join(attr_pairs)
            el["data-i18n-attr"] = merged.strip(";")
            changed += 1

    if changed:
        path.write_text(str(soup), encoding="utf-8")
    log("  %-22s scope=%s  +%d keys" % (path.name, pid, changed))
    return changed


def main():
    pages = sys.argv[1:]
    if pages:
        targets = [REPO / p for p in pages]
    else:
        targets = sorted(REPO.glob("*.html"))
    TEXTS_EN.mkdir(parents=True, exist_ok=True)
    ui = load_ui()
    total = 0
    for p in targets:
        if not p.exists():
            print("  ! not found:", p); continue
        total += process_page(p, ui)
    # write ui.json with stable ordering
    ordered = {}
    # common scopes first
    for _, scope in COMMON_SELECTORS:
        if scope in ui:
            ordered[scope] = ui[scope]
    for k in sorted(ui.keys()):
        if k not in ordered:
            ordered[k] = ui[k]
    UI_JSON.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWrote %s (%d scopes); injected %d attributes total." % (UI_JSON, len(ordered), total))


if __name__ == "__main__":
    main()
