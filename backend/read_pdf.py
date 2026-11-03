"""Quick look at the raw text PyMuPDF extracts from each page (before any cleaning)."""
import sys

import pymupdf

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/resume.pdf"
OUTPUT_PATH = "data/extracted.txt"

doc = pymupdf.open(PDF_PATH)
print(f"Opened PDF with {doc.page_count} pages\n")

with open(OUTPUT_PATH, "w", encoding="utf-8") as out:
    for page in doc:
        page_num = page.number + 1
        text = page.get_text(sort=True)

        first_line = text.strip().split("\n")[0] if text.strip() else "(no text)"
        print(f"Page {page_num:>2}: {len(text):>5} characters | starts with: {first_line[:50]}")

        out.write("=" * 70 + "\n")
        out.write(f"PAGE {page_num}  ({len(text)} characters)\n")
        out.write("=" * 70 + "\n")
        out.write(text + "\n")

print(f"\nFull text saved to {OUTPUT_PATH}")
