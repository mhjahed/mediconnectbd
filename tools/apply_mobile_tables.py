#!/usr/bin/env python3
"""
Adds mobile 'Variant A (Stat Stack)' support to every on-screen .data-table:

  1. The <table> gets class "data-table stack-mobile".
  2. Every top-level <td> gains data-label="<its column header>".

Skipped on purpose:
  - templates/emails/*            -> Outlook cannot render cards; must stay tables
  - anything inside .a4-sheet     -> borderless print documents (receipt, print_rx)

Idempotent: re-running does not duplicate classes or attributes.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"emails"}
SKIP_MARKERS = ("a4-sheet",)

TD_RE = re.compile(r"<td\b([^>]*)>")
TR_OPEN_RE = re.compile(r"<tr\b[^>]*>")
TH_RE = re.compile(r"<th\b[^>]*>(.*?)</th>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(text: str) -> str:
    """Strip tags/entities down to the label the user actually reads."""
    text = text.replace("&amp;", "&")           # decode the only entity we keep
    text = re.sub(r"&\w+;", " ", text)          # other entities -> space
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"\{\{.*?\}\}", " ", text, flags=re.S)   # template vars in headers
    text = WS_RE.sub(" ", text).strip()
    return text or "Detail"


def header_labels(thead_html: str):
    row = TR_OPEN_RE.search(thead_html)
    if not row:
        return []
    chunk = thead_html[row.end():]
    chunk = chunk.split("</tr>")[0]
    return [clean(m.group(1)) for m in TH_RE.finditer(chunk)]


def label_rows(tbody: str, labels):
    """Walk the tbody and add data-label to top-level <td> tags only."""
    out = []
    pos = 0
    depth = 0          # how many <td> are currently open (handles nested markup)
    col = 0
    for m in TD_RE.finditer(tbody):
        tag = m.group(1)
        out.append(tbody[pos:m.start()])
        pos = m.end()
        if 'colspan' in tag or depth > 0:
            out.append(tbody[m.start():m.end()])
            continue
        if "data-label" in tag:
            out.append(tbody[m.start():m.end()])
            continue
        label = labels[col] if col < len(labels) else "Detail"
        label = label.replace('"', "&quot;")
        out.append('<td%s data-label="%s">' % (tag, label))
        col += 1
    out.append(tbody[pos:])
    # reset column counter at each new top-level row
    return "".join(out)


def process(text: str):
    """Rewrite one file's worth of tables."""
    result = []
    pos = 0
    changed_tds = 0
    changed_tables = 0
    for m in re.finditer(r'<table\b[^>]*class="([^"]*)"[^>]*>', text):
        classes = m.group(1).split()
        if "data-table" not in classes:
            continue
        # find this table's closing tag (no nested tables in this project)
        end = text.find("</table>", m.end())
        if end == -1:
            continue
        block = text[m.end():end]

        thead_m = re.search(r"<thead\b[^>]*>(.*?)</thead>", block, re.S)
        labels = header_labels(thead_m.group(1)) if thead_m else []

        new_block = block
        # label rows: process tbody per <tr> so column numbering restarts
        def fix_tbody(tb_match):
            inner = tb_match.group(1)
            rows = []
            last = 0
            for tr in TR_OPEN_RE.finditer(inner):
                start = tr.start()
                if start > last:
                    rows.append(inner[last:start])
                # row body ends at next <tr ...> at same level
                nxt = TR_OPEN_RE.search(inner, tr.end())
                row_body = inner[tr.end(): nxt.start() if nxt else len(inner)]
                if "<td" in row_body and 'colspan' not in row_body[:400]:
                    row_body = label_rows(row_body, labels)
                    rows.append(inner[start:tr.end()] + row_body)
                else:
                    rows.append(inner[start: nxt.start() if nxt else len(inner)])
                last = nxt.start() if nxt else len(inner)
            rows.append(inner[last:])
            return "<tbody>" + "".join(rows) + "</tbody>"

        fixed, n_tb = re.subn(r"<tbody>(.*?)</tbody>", fix_tbody, new_block, flags=re.S)
        if n_tb:
            new_block = fixed
            changed_tds += new_block.count("data-label")
            changed_tables += 1

        new_classes = classes + ["stack-mobile"]
        result.append(text[pos:m.start()])
        result.append('<table class="%s">' % " ".join(dict.fromkeys(new_classes)))
        result.append(new_block)
        pos = end
    result.append(text[pos:])
    return "".join(result), changed_tables, changed_tds


def main():
    targets = []
    for path in sorted(ROOT.glob("templates/*/*.html")):
        if path.parent.name in SKIP_DIRS:
            continue
        raw = path.read_text(encoding="utf-8")
        if 'class="data-table"' not in raw:
            continue
        if any(marker in raw for marker in SKIP_MARKERS):
            print("  skip (print doc)  %s" % path.relative_to(ROOT))
            continue
        targets.append(path)

    total_t = total_d = 0
    for path in targets:
        raw = path.read_text(encoding="utf-8")
        if "stack-mobile" in raw:
            print("  already done      %s" % path.relative_to(ROOT))
            continue
        new, nt, nd = process(raw)
        if new == raw:
            print("  no change         %s" % path.relative_to(ROOT))
            continue
        path.write_text(new, encoding="utf-8")
        print("  %2d table(s), %2d cell(s)  %s" % (nt, nd, path.relative_to(ROOT)))
        total_t += nt
        total_d += nd
    print("\n%d table(s) upgraded, %d cell labels added." % (total_t, total_d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
