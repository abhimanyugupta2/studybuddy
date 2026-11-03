"""
Phase 3: embed chunks and find the ones that best match a question.

Pipeline:  chunks.json -> embedding per chunk -> vectors.npy
           question -> embedding -> cosine similarity -> top-k chunks
Run:       python retrieve.py data/resume.chunks.json          (build the index)
           python retrieve.py data/resume.chunks.json "query"  (search it)
"""
import json
import sys
from pathlib import Path

import numpy as np
import requests

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
# nomic-embed-text was trained with these prefixes; using them improves matching.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
TOP_K = 3


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


# ---------- Search ----------
def search(question, chunks, vectors, k=TOP_K):
    """Return the k best chunks as [(score, chunk), ...], best first."""
    query = embed([QUERY_PREFIX + question])[0]
    scores = vectors @ query
    best = np.argsort(scores)[::-1][:k]
    return [(float(scores[i]), chunks[i]) for i in best]


if __name__ == "__main__":
    chunks_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/resume.chunks.json")
    if len(sys.argv) > 2:
        chunks, vectors = load_index(chunks_path)
        for score, c in search(sys.argv[2], chunks, vectors):
            preview = c["text"].replace("\n", " | ")[:90]
            print(f"{score:.3f}  #{c['id']:>2} p{c['pages']}  {preview}")
    else:
        chunks, vectors = build_index(chunks_path)
        print(f"Embedded {len(chunks)} chunks -> {vectors.shape[1]}-dim vectors")
        print(f"Saved to {vectors_path(chunks_path)}")
