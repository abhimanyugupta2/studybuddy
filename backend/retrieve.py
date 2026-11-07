"""
Phase 3: embed chunks and find the ones that best match a question.
Phase 4: search a whole folder of PDFs at once, and drop weak matches.
Phase 7: hybrid search - meaning (embeddings) + keywords (BM25), merged by rank.

Pipeline:  document -> chunks.json (ingest.py) -> embedding per chunk -> vectors.npy
           question -> meaning ranking + keyword ranking -> fused top-k chunks
Run:       python retrieve.py data            (ingest + index every document in data/)
           python retrieve.py data "query"    (search them all)
"""
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import requests

import ingest
from ingest import SUPPORTED, chunks_path_for, ingest_and_save

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
# nomic-embed-text was trained with these prefixes; using them improves matching.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
TOP_K = 5          # 3 was too few: the answer chunk often ranked 4th-5th behind name matches
MIN_SCORE = 0.58   # on-topic questions scored 0.60-0.75, off-topic ones ~0.54
RRF_K = 60         # standard Reciprocal Rank Fusion constant: smooths out small rank differences


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
    if not chunks:                                   # e.g. a scanned PDF with no text layer
        vectors = np.zeros((0, EMBED_DIM), dtype=np.float32)
    else:
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


# ---------- Library: every PDF/image in a folder, as one big index ----------
def list_documents(data_dir):
    return sorted(p for p in Path(data_dir).iterdir() if p.suffix.lower() in SUPPORTED)


def load_library(data_dir):
    """Ingest new/changed documents, then stack all their chunks and vectors together.
    Documents are also re-ingested when ingest.py itself changes (a better cleaner)."""
    all_chunks, all_vectors = [], []
    cleaner_changed_at = Path(ingest.__file__).stat().st_mtime
    for doc_path in list_documents(data_dir):
        chunks_path = chunks_path_for(doc_path)
        if (not chunks_path.exists()
                or chunks_path.stat().st_mtime < max(doc_path.stat().st_mtime, cleaner_changed_at)):
            print(f"Ingesting {doc_path.name} ...")
            ingest_and_save(doc_path)
        chunks, vectors = load_index(chunks_path)
        for c in chunks:
            c["source"] = doc_path.name             # so answers can say which file
        all_chunks += chunks
        all_vectors.append(vectors)
    if not all_chunks:
        sys.exit(f"No documents found in {data_dir}/ - drop some PDFs or photos in and try again.")
    return all_chunks, np.vstack(all_vectors)


def label(chunk):
    return f"{chunk['source']} p{','.join(map(str, chunk['pages']))}"


# ---------- Keyword search (BM25) ----------
STOPWORDS = set("""a an and are as at be by did do does for from got had has have he her his how i
in is it its me my of on or our she so that the their them they this to was we were what when where
which who why will with you your""".split())


def tokenize(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


def bm25_scores(question, chunks, k1=1.5, b=0.75):
    """Classic search-engine scoring. A word found in every chunk (like the author's name on
    every page) counts for almost nothing; a rare, specific word counts for a lot."""
    docs = [tokenize(c["text"]) for c in chunks]
    avg_len = sum(map(len, docs)) / len(docs)
    df = Counter(w for d in docs for w in set(d))
    idf = {w: math.log((len(docs) - n + 0.5) / (n + 0.5) + 1) for w, n in df.items()}
    scores = np.zeros(len(docs), dtype=np.float32)
    for i, d in enumerate(docs):
        tf = Counter(d)
        for w in set(tokenize(question)):
            if w in tf:
                scores[i] += idf[w] * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(d) / avg_len))
    return scores


# ---------- Hybrid search ----------
def search(question, chunks, vectors, k=TOP_K, min_score=MIN_SCORE):
    """Return up to k chunks as [(meaning_score, chunk), ...], best first.

    Meaning search (embeddings) and keyword search (BM25) each rank every chunk; the two
    rankings are merged with Reciprocal Rank Fusion, so a chunk ranked high by either one rises.
    If even the best meaning score is below min_score, the question is off-topic: return nothing.
    """
    meaning = vectors @ embed([QUERY_PREFIX + question])[0]
    if meaning.max() < min_score:
        return []
    keyword = bm25_scores(question, chunks)
    fused = np.zeros(len(chunks))
    for scores in (meaning, keyword):
        for rank, i in enumerate(np.argsort(scores)[::-1]):
            fused[i] += 1 / (RRF_K + rank)
    candidates = [i for i in np.argsort(fused)[::-1]
                  if meaning[i] >= min_score or keyword[i] > 0]   # unrelated chunks never sneak in
    return [(float(meaning[i]), chunks[i]) for i in candidates[:k]]


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
        print(f"Library: {files} documents, {len(chunks)} chunks, {vectors.shape[1]}-dim vectors")
