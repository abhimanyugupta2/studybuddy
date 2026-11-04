"""
Phase 3: embed chunks and find the ones that best match a question.
Phase 4: search a whole folder of PDFs at once, and drop weak matches.

Pipeline:  PDF -> chunks.json (ingest.py) -> embedding per chunk -> vectors.npy
           question -> embedding -> cosine similarity -> top-k chunks above MIN_SCORE
Run:       python retrieve.py data            (ingest + index every PDF in data/)
           python retrieve.py data "query"    (search them all)
"""
import json
import sys
from pathlib import Path

import numpy as np
import requests

from ingest import chunks_path_for, ingest_and_save

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
# nomic-embed-text was trained with these prefixes; using them improves matching.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
TOP_K = 3
MIN_SCORE = 0.58   # on-topic questions scored 0.60-0.75, off-topic ones ~0.54


# ---------- Embedding ----------
def embed(texts):
    """Return an (n, dim) array of unit-length vectors, one per text."""
    response = requests.post(OLLAMA_EMBED_URL, json={"model": EMBED_MODEL, "input": texts})
    response.raise_for_status()
    vectors = np.array(response.json()["embeddings"], dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)   # so dot product = cosine


# ---------- Index: chunks + their vectors, saved side by side ----------
def vectors_path(chunks_path):
    return Path(str(chunks_path).replace(".chunks.json", ".vectors.npy"))


def build_index(chunks_path):
    chunks = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
    vectors = embed([DOC_PREFIX + c["text"] for c in chunks])
    np.save(vectors_path(chunks_path), vectors)
    return chunks, vectors


def load_index(chunks_path):
    """Load the saved index, building it first if it's missing or older than the chunks."""
    chunks_path, vec_path = Path(chunks_path), vectors_path(chunks_path)
    if not vec_path.exists() or vec_path.stat().st_mtime < chunks_path.stat().st_mtime:
        return build_index(chunks_path)
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    return chunks, np.load(vec_path)


# ---------- Library: every PDF in a folder, as one big index ----------
def load_library(data_dir):
    """Ingest new/changed PDFs, then stack all their chunks and vectors together."""
    all_chunks, all_vectors = [], []
    for pdf_path in sorted(Path(data_dir).glob("*.pdf")):
        chunks_path = chunks_path_for(pdf_path)
        if not chunks_path.exists() or chunks_path.stat().st_mtime < pdf_path.stat().st_mtime:
            print(f"Ingesting {pdf_path.name} ...")
            ingest_and_save(pdf_path)
        chunks, vectors = load_index(chunks_path)
        for c in chunks:
            c["source"] = pdf_path.name             # so answers can say which file
        all_chunks += chunks
        all_vectors.append(vectors)
    if not all_chunks:
        sys.exit(f"No PDFs found in {data_dir}/ - drop some in and try again.")
    return all_chunks, np.vstack(all_vectors)


def label(chunk):
    return f"{chunk['source']} p{','.join(map(str, chunk['pages']))}"


# ---------- Search ----------
def search(question, chunks, vectors, k=TOP_K, min_score=MIN_SCORE):
    """Return up to k chunks scoring at least min_score as [(score, chunk), ...], best first."""
    query = embed([QUERY_PREFIX + question])[0]
    scores = vectors @ query
    best = np.argsort(scores)[::-1][:k]
    return [(float(scores[i]), chunks[i]) for i in best if scores[i] >= min_score]


if __name__ == "__main__":
    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    chunks, vectors = load_library(data_dir)
    if len(sys.argv) > 2:
        for score, c in search(sys.argv[2], chunks, vectors, min_score=0):   # show everything
            flag = "" if score >= MIN_SCORE else "  (below cutoff)"
            preview = c["text"].replace("\n", " | ")[:70]
            print(f"{score:.3f}  {label(c):<20} {preview}{flag}")
    else:
        files = len({c["source"] for c in chunks})
        print(f"Library: {files} PDFs, {len(chunks)} chunks, {vectors.shape[1]}-dim vectors")
