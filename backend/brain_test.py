import json
import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.1:8b"  # change to "llama3.1:8b" if that's the one you pulled


def ask(question, context=None):
    system = "You are a helpful study assistant."
    if context:
        system += (
            " Answer ONLY using the context below. If the answer is not in the "
            "context, reply exactly: \"I couldn't find that in your notes.\""
            f"\n\nContext:\n{context}"
        )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        "stream": True,
        "options": {"temperature": 0.2},
    }

    with requests.post(OLLAMA_URL, json=payload, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            print(chunk["message"]["content"], end="", flush=True)
            if chunk.get("done"):
                print("\n")


notes = """
Greenfield School Science Fair 2026:
The fair is held on 14 February in the Main Hall.
Projects must be submitted by 1 February.
The winning team receives Rs 5000 and a visit to an ISRO centre.
Each team can have at most 3 students.
"""

print("TEST 1: no notes given")
ask("When is the Greenfield School science fair?")

print("TEST 2: with notes")
ask("When is the science fair and what does the winner get?", context=notes)

print("TEST 3: question not in the notes")
ask("Who is the principal of Greenfield School?", context=notes)