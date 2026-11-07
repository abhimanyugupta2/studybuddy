"""
Phase 3: ask questions about a PDF, answered only from its most relevant chunks.
Phase 4: ask across every PDF in a folder; skip the LLM when nothing matches.
Phase 5: remember the conversation, so follow-ups like "explain that simpler" work.

Pipeline:  (question + history) -> standalone question -> top-k chunks (retrieve.py)
           -> prompt with context + recent history -> streamed LLM answer
Run:       python ask.py data
"""
import json
import sys
from pathlib import Path

import requests

from retrieve import label, load_library, search

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
CHAT_MODEL = "llama3.1:8b"
NOT_FOUND = "I couldn't find that in your notes."
HISTORY_TURNS = 6          # last 3 questions + 3 answers are sent along with each question


def chat(messages, stream=False):
    """Send messages to the LLM. Yields text pieces if stream=True, else returns the full reply."""
    payload = {"model": CHAT_MODEL, "messages": messages, "stream": stream,
               "options": {"temperature": 0.2}}
    response = requests.post(OLLAMA_CHAT_URL, json=payload, stream=stream)
    response.raise_for_status()
    if not stream:
        return response.json()["message"]["content"]
    return (json.loads(line)["message"]["content"] for line in response.iter_lines() if line)


def standalone_question(question, history):
    """Rewrite a follow-up ("why is that?") into a question that makes sense on its own,
    because search only sees this one string, not the conversation."""
    if not history:
        return question
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-HISTORY_TURNS:])
    prompt = (
        "Rewrite the follow-up question so it can be understood without the conversation. "
        "Keep it short. If it is already standalone, return it unchanged. "
        "Reply with ONLY the rewritten question.\n\n"
        f"Conversation:\n{convo}\n\nFollow-up question: {question}"
    )
    return chat([{"role": "user", "content": prompt}]).strip().strip('"')


def format_context(results):
    """Label each chunk with its file and pages so the model (and you) can cite them."""
    return "\n\n".join(f"[{label(c)}]\n{c['text']}" for _, c in results)


def stream_answer(question, results, history=()):
    """Yield the answer piece by piece, using only the retrieved chunks as facts."""
    system = (
        "You are a helpful study assistant. Answer ONLY using the context below. "
        "You may use the conversation to understand what the user means, but facts must "
        f"come from the context. If the answer is not in the context, reply exactly: \"{NOT_FOUND}\""
        f"\n\nContext:\n{format_context(results)}"
    )
    messages = [{"role": "system", "content": system}, *history[-HISTORY_TURNS:],
                {"role": "user", "content": question}]
    yield from chat(messages, stream=True)


if __name__ == "__main__":
    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    chunks, vectors = load_library(data_dir)
    files = sorted({c["source"] for c in chunks})
    print(f"Loaded {len(chunks)} chunks from {len(files)} PDFs: {', '.join(files)}")
    print("Ask away (empty line to quit).\n")

    history = []
    while question := input("You: ").strip():
        query = standalone_question(question, history)
        if query != question:
            print(f"  (searching for: {query})")
        results = search(query, chunks, vectors)
        print("StudyBuddy: ", end="")
        if not results:
            reply = NOT_FOUND                     # nothing relevant: don't let the LLM guess
            print(reply)
        else:
            reply = ""
            for piece in stream_answer(question, results, history):
                print(piece, end="", flush=True)
                reply += piece
            print(f"\n  Sources: {', '.join(f'{label(c)} ({s:.2f})' for s, c in results)}")
        print()
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": reply}]
