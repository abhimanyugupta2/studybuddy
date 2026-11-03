"""
Phase 2: turn a PDF into clean, search-ready chunks.

Pipeline:  PDF -> raw text per page -> clean paragraphs -> chunks -> JSON file
Run:       python ingest.py data/resume.pdf
"""
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

MAX_CHUNK_CHARS = 800      # roughly 150-200 words per chunk
HEADING_MAX_CHARS = 45     # section titles are short
BULLET_RE = re.compile(r"^[•◦▪‒–*\-]\s")
HEADING_RE = re.compile(r"[A-Z][\w&’' \-/]*")


# ---------- Stage 1: extract ----------
def extract_pages(pdf_path):
    """Return a list like [{"page": 1, "text": "..."}, ...]."""
    doc = pymupdf.open(pdf_path)
    pages = []
    for page in doc:
        text = page.get_text(sort=True)          # sort=True: top-to-bottom reading order
        pages.append({"page": page.number + 1, "text": text})
    doc.close()
    return pages


# ---------- Stage 2: clean ----------
def find_repeated_lines(pages, min_share=0.5):
    """Lines that appear on at least half the pages are headers/footers."""
    if len(pages) < 3:                            # too few pages to tell
        return set()
    counts = Counter()
    for p in pages:
        counts.update({line.strip() for line in p["text"].splitlines() if line.strip()})
    return {line for line, n in counts.items() if n >= len(pages) * min_share}


def clean_lines(text, page_num, repeated):
    text = unicodedata.normalize("NFKC", text)    # ligatures like "ﬁ" -> "fi"
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and line not in repeated]
    if lines and lines[-1] == str(page_num):      # page number at the bottom
        lines.pop()
    return lines


def is_heading(text):
    return (len(text) <= HEADING_MAX_CHARS
            and not BULLET_RE.match(text)
            and HEADING_RE.fullmatch(text) is not None)


def merge_into_paragraphs(lines):
    """Join lines that were only split because they hit the edge of the page."""
    if not lines:
        return []
    longest = max(len(line) for line in lines)
    paragraphs, last_line = [], ""
    for line in lines:
        # Did the previous line run to the right edge mid-sentence? Then this line continues it.
        continues = len(last_line) >= 0.75 * longest and not last_line.endswith(".")
        if paragraphs and len(line) <= 3:
            paragraphs[-1] += line                        # tiny fragment like "2" or ")"
            continue                                      # keep last_line as the long line
        if not paragraphs or BULLET_RE.match(line):
            paragraphs.append(line)                       # bullets always start fresh
        elif last_line.endswith("-"):
            if line[0].isupper():
                paragraphs[-1] += line                    # "Physics-" + "Informed"
            else:
                paragraphs[-1] = paragraphs[-1][:-1] + line   # "manage-" + "ment"
        elif continues:
            paragraphs[-1] += " " + line                  # wrapped sentence
        else:
            paragraphs.append(line)                       # heading or new item
        last_line = line
    return [re.sub(r"\s+", " ", p).replace(" )", ")") for p in paragraphs]


# ---------- Stage 3: chunk ----------
def build_chunks(paragraphs):
    """Pack paragraphs into chunks. A new section always starts a new chunk."""
    chunks, current, section = [], [], None

    def save_chunk():
        if not current:
            return
        body = "\n".join(p["text"] for p in current)
        chunks.append({
            "id": len(chunks),
            "section": section,
            "pages": sorted({p["page"] for p in current}),
            "text": f"Section: {section}\n{body}" if section else body,
        })

    for par in paragraphs:
        if par["is_heading"]:
            save_chunk()
            current, section = [], par["text"]
            continue
        size = sum(len(p["text"]) for p in current) + len(par["text"])
        if current and size > MAX_CHUNK_CHARS:
            save_chunk()
            current = [current[-1]]                 # overlap: repeat the last paragraph
        current.append(par)
    save_chunk()
    return chunks


# ---------- The whole pipeline ----------
def ingest(pdf_path):
    pages = extract_pages(pdf_path)
    repeated = find_repeated_lines(pages)
    paragraphs = []
    for p in pages:
        for text in merge_into_paragraphs(clean_lines(p["text"], p["page"], repeated)):
            paragraphs.append({"text": text, "page": p["page"], "is_heading": is_heading(text)})
    return pages, repeated, build_chunks(paragraphs)


def report(pages, repeated, chunks):
    sizes = [len(c["text"]) for c in chunks]
    print(f"Pages: {len(pages)} | Repeated lines removed: {len(repeated)} | Chunks: {len(chunks)}")
    print(f"Chunk size (characters): min {min(sizes)}, avg {sum(sizes) // len(sizes)}, max {max(sizes)}\n")
    for c in chunks:
        preview = c["text"].replace("\n", " | ")[:90]
        print(f"#{c['id']:>2} p{c['pages']} {len(c['text']):>4} chars  {preview}")


if __name__ == "__main__":
    pdf_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/resume.pdf")
    pages, repeated, chunks = ingest(pdf_path)
    report(pages, repeated, chunks)

    out_path = pdf_path.with_suffix(".chunks.json")
    out_path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {len(chunks)} chunks to {out_path}")
