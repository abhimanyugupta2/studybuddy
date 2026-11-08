"""
Phase 3: ask questions about a PDF, answered only from its most relevant chunks.
Phase 4: ask across every PDF in a folder; skip the LLM when nothing matches.
Phase 5: remember the conversation, so follow-ups like "explain that simpler" work.

Pipeline:  (question + history) -> search plan (standalone question, rephrasings, imagined
           answer) -> best chunks (retrieve.py) -> prompt with context + history -> streamed answer
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
NUM_CTX = 8192             # Ollama's default window is smaller; summaries and quizzes need room


def chat(messages, stream=False, format=None, temperature=0.2):
    """Send messages to the LLM. Yields text pieces if stream=True, else returns the full reply.
    format can be "json" or a JSON schema, to force structured output."""
    payload = {"model": CHAT_MODEL, "messages": messages, "stream": stream,
               "options": {"temperature": temperature, "num_ctx": NUM_CTX}}
    if format:
        payload["format"] = format
    response = requests.post(OLLAMA_CHAT_URL, json=payload, stream=stream)
    response.raise_for_status()
    if not stream:
        return response.json()["message"]["content"]
    return stream_pieces(response)


def stream_pieces(response):
    """Yield text as it arrives. Closing this generator closes the connection, which makes
    Ollama stop generating - that's how the Stop button really stops the model."""
    try:
        for line in response.iter_lines():
            if line:
                yield json.loads(line)["message"]["content"]
    finally:
        response.close()


SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "rephrasings": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
        "hypothetical_answer": {"type": "string"},
    },
    "required": ["question", "rephrasings", "hypothetical_answer"],
}


def plan_search(question, history=(), snippets=()):
    """Turn the user's question into what search needs, in one model call:
      question            - standalone (follow-ups like "why is that?" resolved), spelling fixed
      rephrasings         - 2 other wordings with synonyms ("approval" -> "confirmed", "said yes")
      hypothetical_answer - a made-up answer phrased like the document would phrase it (HyDE).
    Answers rarely repeat the question's words, so searching with all of these finds them far
    more reliably. `snippets` (a quick first search) show the model the documents' real names and
    words; without them it invented "Professor Thompson" and the answer step got confused.
    Only used for searching - never shown as an answer."""
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-HISTORY_TURNS:])
    peek = "\n".join(f"- {s[:300]}" for s in snippets)
    prompt = (
        "Help search a student's documents (notes, emails, letters) for the answer to a question.\n"
        "- 'question': the question rewritten to make sense on its own (use the conversation to "
        "resolve words like 'it' or 'that'), with spelling fixed.\n"
        "- 'rephrasings': 2 different ways to ask the SAME question, using synonyms and the words "
        "the documents use (e.g. approved / confirmed / agreed / said yes).\n"
        "- In 'question' and 'rephrasings', never add names, subjects or details that are not in "
        "the question, the conversation or the snippets. Don't change what is being asked.\n"
        "- 'hypothetical_answer': 1-2 sentences written the way the source document itself would "
        "state the answer, using the snippets' names and wording. Only this one may guess "
        "details; it is used only to search.\n\n"
        + (f"Snippets from the user's documents that may be relevant:\n{peek}\n\n" if peek else "")
        + (f"Conversation so far:\n{convo}\n\n" if convo else "")
        + f"Question: {question}"
    )
    try:
        plan = json.loads(chat([{"role": "user", "content": prompt}], format=SEARCH_PLAN_SCHEMA,
                               temperature=0))
        rewritten = plan["question"].strip() or question
        return {"question": rewritten,
                "queries": [question, rewritten, *[r for r in plan["rephrasings"] if r.strip()]],
                "hypothetical": plan["hypothetical_answer"].strip() or None}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {"question": question, "queries": [question], "hypothetical": None}


def find_answer_chunks(question, history, chunks, vectors):
    """The whole search: peek -> plan -> search. Returns (plan, results)."""
    peek = search([question], chunks, vectors, budget=900, min_score=0)
    plan = plan_search(question, history, [c["text"] for _, c in peek])
    # Only the user's own question (and its spelling-fixed standalone rewrite) decide
    # "off-topic or not": rephrasings written after peeking at the documents always sound on-topic.
    results = search(plan["queries"], chunks, vectors, hypothetical=plan["hypothetical"], gate=2)
    return plan, results


def format_context(results):
    """Label each chunk with its file and pages so the model (and you) can cite them.
    Chunks go in document order, so an email thread or a chapter reads as written."""
    ordered = sorted((c for _, c in results), key=lambda c: (c["source"], c["id"]))
    return "\n\n".join(f"[{label(c)}]\n{c['text']}" for c in ordered)


def stream_answer(question, results, history=(), rephrasings=()):
    """Yield the answer piece by piece, using only the retrieved chunks as facts.
    `rephrasings` (from plan_search) tell the model what the question means in the document's
    own words, e.g. that "semester ending" is the letter's "end of term"."""
    hint = ("\n\nThe question may also be phrased as: " + " / ".join(rephrasings)) if rephrasings else ""
    system = (
        "You are a helpful study assistant. Answer ONLY using the context below, which comes "
        "from the user's own documents. You may use the conversation to understand what the user "
        "means, but facts must come from the context.\n"
        "Rules:\n"
        "- People may be named by first name, full name, title (e.g. 'the professor') or email "
        "address; work out who is who from the context.\n"
        "- Email threads and letters can contain several messages. Check the dates: the most "
        "recent message shows the current situation.\n"
        "- Never answer 'no' or 'not yet' just because something isn't mentioned.\n"
        "- First find the exact sentence(s) in the context that answer the question. Give a "
        "clear answer in 1-3 full sentences (say who, what and when where it helps), then on a "
        "new line write 'Evidence:' followed by that sentence in quotes.\n"
        f"- If no sentence in the context answers it, reply exactly: \"{NOT_FOUND}\""
        f"{hint}\n\nContext:\n{format_context(results)}"
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
        plan, results = find_answer_chunks(question, history, chunks, vectors)
        print(f"  (searching for: {' | '.join(plan['queries'][1:])})")
        print("StudyBuddy: ", end="")
        if not results:
            reply = NOT_FOUND                     # nothing relevant: don't let the LLM guess
            print(reply)
        else:
            reply = ""
            for piece in stream_answer(question, results, history, plan["queries"][2:]):
                print(piece, end="", flush=True)
                reply += piece
            print(f"\n  Sources: {', '.join(f'{label(c)} ({s:.2f})' for s, c in results)}")
        print()
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": reply}]
