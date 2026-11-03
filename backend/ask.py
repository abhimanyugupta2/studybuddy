"""
Phase 3: ask questions about a PDF, answered only from its most relevant chunks.

Pipeline:  question -> top-k chunks (retrieve.py) -> prompt with context -> LLM answer
Run:       python ask.py data/resume.chunks.json
"""
import json
import sys
from pathlib import Path

import requests

from retrieve import load_index, search

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
CHAT_MODEL = "llama3.1:8b"
NOT_FOUND = "I couldn't find that in your notes."


def format_context(results):
    """Label each chunk with its pages so the model (and you) can cite them."""
    return "\n\n".join(f"[pages {', '.join(map(str, c['pages']))}]\n{c['text']}"
                       for _, c in results)


def answer(question, results):
    system = (
        "You are a helpful study assistant. Answer ONLY using the context below. "
        f"If the answer is not in the context, reply exactly: \"{NOT_FOUND}\""
        f"\n\nContext:\n{format_context(results)}"
    )
    payload = {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        "stream": True,
        "options": {"temperature": 0.2},
    }
    with requests.post(OLLAMA_CHAT_URL, json=payload, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            print(chunk["message"]["content"], end="", flush=True)
    print()


if __name__ == "__main__":
    chunks_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/resume.chunks.json")
    chunks, vectors = load_index(chunks_path)
    print(f"Loaded {len(chunks)} chunks from {chunks_path}. Ask away (empty line to quit).\n")

    while question := input("You: ").strip():
        results = search(question, chunks, vectors)
        sources = ", ".join(f"#{c['id']} ({score:.2f})" for score, c in results)
        print(f"  [retrieved {sources}]")
        print("StudyBuddy: ", end="")
        answer(question, results)
        print()
