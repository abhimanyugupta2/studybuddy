"""
Phase 2: turn a document into clean, search-ready chunks.
Phase 7: one general "smart" cleaner for any PDF, scan or photo - no special cases.

Pipeline:  file -> layout-aware markdown per page (OCR where needed) -> clean -> chunks -> JSON
Run:       python ingest.py data/notes.pdf        (also works on .png / .jpg photos and scans)

How the cleaning works for any document:
  * A layout model (pymupdf4llm + pymupdf_layout) looks at where text sits on the page and how
    big/bold it is, so it knows headings, paragraphs, tables, page headers and page footers.
    Headers/footers are dropped; wrapped lines are rejoined into paragraphs.
  * Pages with no real text layer (scans, photos) are read with OCR (RapidOCR) automatically.
  * A line that repeats on most pages once digits are ignored ("Page 3 of 9", "...url 2/3")
    is boilerplate the layout model missed, and is dropped too.
"""
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf
import pymupdf.layout                     # noqa: F401  (switches pymupdf4llm to the layout model)
import pymupdf4llm

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
MAX_CHUNK_CHARS = 800      # roughly 150-200 words per chunk
OCR_DPI = 300              # 150-200 garbled small print in tests; 300 read it perfectly
INDEX_DIR_NAME = ".index"  # chunks/vectors live in data/.index/, out of the way of your files
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


# ---------- Stage 1: extract (layout + OCR) ----------
def open_as_pdf(path):
    """Images (photos, scans) become a one-page PDF so everything takes the same path."""
    doc = pymupdf.open(path)
    return doc if doc.is_pdf else pymupdf.open("pdf", doc.convert_to_pdf())


def extract_pages(path):
    """Return [{"page": 1, "text": "<markdown>"}, ...] with headers/footers already removed."""
    doc = open_as_pdf(path)
    pages = pymupdf4llm.to_markdown(doc, page_chunks=True, header=False, footer=False,
                                    ocr_dpi=OCR_DPI)
    doc.close()
    return [{"page": i + 1, "text": p["text"]} for i, p in enumerate(pages)]


# ---------- Stage 2: clean ----------
def line_key(line):
    """Compare lines ignoring numbers, so "Page 2 of 9" and "Page 3 of 9" count as the same line."""
    return re.sub(r"\d+", "#", line.strip().lower())


def find_repeated_lines(pages, min_share=0.5):
    """Lines that appear on at least half the pages are leftover headers/footers."""
    if len(pages) < 3:                            # too few pages to tell
        return set()
    counts = Counter()
    for p in pages:
        counts.update({line_key(l) for l in p["text"].splitlines() if l.strip()})
    return {key for key, n in counts.items() if n >= len(pages) * min_share}


def to_paragraphs(text, page_num, repeated):
    """Markdown -> [{"text", "page", "is_heading"}]. The layout model already joins wrapped
    lines, so each non-empty line is one paragraph, list item, table row or heading."""
    text = unicodedata.normalize("NFKC", text)    # ligatures like "ﬁ" -> "fi"
    paragraphs = []
    for line in text.splitlines():
        line = line.replace("<br>", " ").replace("**", "")      # markdown styling, not content
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line_key(line) in repeated or line == str(page_num):
            continue
        is_heading = line.startswith("#")
        if is_heading:
            line = line.lstrip("#").strip()
        paragraphs.append({"text": line, "page": page_num,
                           # "Dear Professor," is big/bold but a greeting, not a section title
                           "is_heading": is_heading and 0 < len(line) <= 120
                                         and not line.endswith(",")})
    return paragraphs


def split_long(par):
    """A paragraph longer than a chunk is split at sentence ends (or hard-cut as a last resort)."""
    if len(par["text"]) <= MAX_CHUNK_CHARS:
        return [par]
    pieces, current = [], ""
    for sentence in SENTENCE_END.split(par["text"]):
        while len(sentence) > MAX_CHUNK_CHARS:                 # one giant "sentence"
            pieces.append(sentence[:MAX_CHUNK_CHARS])
            sentence = sentence[MAX_CHUNK_CHARS:]
        if current and len(current) + len(sentence) + 1 > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    pieces.append(current)
    return [{**par, "text": p} for p in pieces if p]


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
            if sum(len(p["text"]) for p in current) + len(par["text"]) > MAX_CHUNK_CHARS:
                current = []                        # overlap would overflow: start clean
        current.append(par)
    save_chunk()
    return drop_title_sections(chunks)


def drop_title_sections(chunks, max_share=0.6):
    """A "section" covering most of the document is really its title. Stamped on every chunk it
    makes them all look alike to search, so keep it as metadata but take it out of the text."""
    if len(chunks) < 4:
        return chunks
    counts = Counter(c["section"] for c in chunks)
    for c in chunks:
        if c["section"] and counts[c["section"]] >= max_share * len(chunks):
            c["text"] = c["text"].removeprefix(f"Section: {c['section']}\n")
    return chunks


# ---------- The whole pipeline ----------
def ingest(path):
    pages = extract_pages(path)
    repeated = find_repeated_lines(pages)
    paragraphs = []
    for p in pages:
        for par in to_paragraphs(p["text"], p["page"], repeated):
            paragraphs += split_long(par)
    return pages, repeated, build_chunks(paragraphs)


def chunks_path_for(doc_path):
    doc_path = Path(doc_path)
    return doc_path.parent / INDEX_DIR_NAME / f"{doc_path.name}.chunks.json"


def ingest_and_save(path):
    pages, repeated, chunks = ingest(path)
    out_path = chunks_path_for(path)
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    return pages, repeated, chunks, out_path


def report(pages, repeated, chunks):
    sizes = [len(c["text"]) for c in chunks] or [0]
    print(f"Pages: {len(pages)} | Repeated lines removed: {len(repeated)} | Chunks: {len(chunks)}")
    print(f"Chunk size (characters): min {min(sizes)}, avg {sum(sizes) // len(sizes)}, max {max(sizes)}\n")
    for c in chunks:
        preview = c["text"].replace("\n", " | ")[:90]
        print(f"#{c['id']:>2} p{c['pages']} {len(c['text']):>4} chars  {preview}")


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/resume.pdf")
    pages, repeated, chunks, out_path = ingest_and_save(path)
    report(pages, repeated, chunks)
    print(f"\nSaved {len(chunks)} chunks to {out_path}")
