"""
Study tools built on the same chunks the Q&A uses: one-click summaries and quizzes.

Both are generators of event dicts, so the server can stream progress and the user can stop them.
  summary_events(name, chunks, cache_path) -> {"type": "progress"|"token"|"done", ...}
  quiz_events(chunks, n)                   -> {"type": "progress"|"quiz"|"done", ...}
"""
import json
import random

from ask import chat
from retrieve import label

GROUP_CHARS = 6000         # ~1,500 tokens of notes per summarising call
QUIZ_MIN_CHUNK = 250       # tiny chunks (a name, a date line) make poor quiz questions
QUIZ_SPARES = 2            # extra questions written, so a few can fail the double-check

SUMMARY_STYLE = (
    "Write for a student revising. Start with a one-sentence overview in bold. Then give 5-10 "
    "bullet points (lines starting with '- ') covering the key ideas, definitions, names, dates "
    "and numbers. Use ONLY facts from the text; never add outside knowledge."
)


def group_chunks(chunks, max_chars=GROUP_CHARS):
    groups, current = [], []
    for c in chunks:
        if current and sum(len(x["text"]) for x in current) + len(c["text"]) > max_chars:
            groups.append(current)
            current = []
        current.append(c)
    return groups + [current] if current else groups


def summary_events(name, chunks, cache_path):
    """Short documents: one call. Long ones: summarise each part, then combine the part
    summaries ("map-reduce"), so even a 100-page PDF fits in the model's window."""
    if cache_path.exists() and cache_path.stat().st_mtime > cache_path.with_name(
            f"{name}.chunks.json").stat().st_mtime:
        yield {"type": "token", "text": cache_path.read_text(encoding="utf-8")}
        yield {"type": "done", "cached": True}
        return

    groups = group_chunks(chunks)
    if len(groups) == 1:
        notes = "\n\n".join(c["text"] for c in groups[0])
    else:
        parts = []
        for i, group in enumerate(groups, 1):
            yield {"type": "progress", "text": f"Reading part {i} of {len(groups)}…"}
            text = "\n\n".join(c["text"] for c in group)
            parts.append(chat([{"role": "user", "content":
                                f"Summarise this part of a document in 5-8 short bullet points. "
                                f"Use ONLY facts from the text.\n\n{text}"}]))
        yield {"type": "progress", "text": "Combining the parts…"}
        notes = "\n\n".join(parts)

    summary = ""
    for piece in chat([{"role": "user", "content":
                        f"Summarise the document \"{name}\". {SUMMARY_STYLE}\n\nText:\n{notes}"}],
                      stream=True):
        summary += piece
        yield {"type": "token", "text": piece}
    cache_path.write_text(summary, encoding="utf-8")
    yield {"type": "done", "cached": False}


# The correct answer is written out as text, not as an option number: small models often point
# at the wrong option, but rarely write down the wrong answer.
QUIZ_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "excerpt": {"type": "integer"},
            "question": {"type": "string"},
            "correct_answer": {"type": "string"},
            "wrong_answers": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string"},
        },
        "required": ["excerpt", "question", "correct_answer", "wrong_answers", "explanation"],
    }}},
    "required": ["questions"],
}
def check_schema(n):
    """Exactly n answers, each A-D (without the length rule the model sometimes sends [])."""
    answers = {"type": "array", "items": {"type": "string", "enum": ["A", "B", "C", "D"]},
               "minItems": n, "maxItems": n}
    return {"type": "object", "properties": {"answers": answers}, "required": ["answers"]}


def quiz_events(chunks, n=5):
    """Write one multiple-choice question per randomly picked excerpt, then have the model
    answer its own quiz from the excerpts and keep only questions where both agree."""
    pool = [c for c in chunks if len(c["text"]) >= QUIZ_MIN_CHUNK] or chunks
    picked = random.sample(pool, min(n + QUIZ_SPARES, len(pool)))   # spares replace dropped ones
    yield {"type": "progress", "text": f"Writing {min(n, len(picked))} questions…"}
    excerpts = "\n\n".join(f"[Excerpt {i}]\n{c['text']}" for i, c in enumerate(picked, 1))
    prompt = (
        f"Write {len(picked)} multiple-choice quiz questions for a student, exactly one per "
        "excerpt below. Each question must be answerable from its excerpt alone and test an "
        "important fact or idea (not trivia like email addresses or phone numbers). "
        "'correct_answer' is the right answer, stated exactly as the excerpt supports it. "
        "'wrong_answers' are 3 plausible answers that the excerpt shows are clearly WRONG; "
        "never a partly-correct or overlapping answer. 'explanation' says in one sentence why "
        f"the correct answer is right. 'excerpt' is the excerpt number.\n\n{excerpts}"
    )
    raw = chat([{"role": "user", "content": prompt}], format=QUIZ_SCHEMA, temperature=0.5)

    drafts = []
    for q in json.loads(raw).get("questions", []):
        correct = str(q.get("correct_answer", "")).strip()
        wrong = [str(w).strip() for w in q.get("wrong_answers", [])][:3]
        idx = q.get("excerpt")
        options = [correct, *wrong]
        if (not correct or len(wrong) != 3 or len({o.lower() for o in options}) != 4
                or not isinstance(idx, int) or not 1 <= idx <= len(picked)):
            continue                                    # drop anything malformed
        random.shuffle(options)
        drafts.append({"question": q["question"].strip(), "options": options,
                       "answer": options.index(correct), "explanation": q["explanation"].strip(),
                       "chunk": picked[idx - 1]})

    yield {"type": "progress", "text": "Double-checking the answers…"}
    verified = [d for d, ok in zip(drafts, check_answers(drafts)) if ok]
    questions = verified[:n]
    for q in questions:
        source = q.pop("chunk")
        q["source"] = {"label": label(source), "name": source["source"], "page": source["pages"][0],
                       "text": source["text"]}       # so the viewer can highlight it
    yield {"type": "quiz", "questions": questions, "dropped": max(0, min(n, len(drafts)) - len(questions))}
    yield {"type": "done"}


def check_answers(drafts):
    """Answer each draft question from its excerpt alone (a fresh, independent attempt).
    Returns [True/False] per question: does that attempt pick the intended answer?"""
    if not drafts:
        return []
    blocks = []
    for i, d in enumerate(drafts, 1):
        opts = "\n".join(f"{'ABCD'[j]}. {o}" for j, o in enumerate(d["options"]))
        blocks.append(f"[Question {i}]\nExcerpt: {d['chunk']['text']}\n{d['question']}\n{opts}")
    prompt = (f"There are {len(drafts)} questions. Answer each one using ONLY its excerpt. Reply "
              f"with exactly {len(drafts)} letters (A, B, C or D), one per question, in order, "
              "in the 'answers' list.\n\n" + "\n\n".join(blocks))
    for _ in range(2):                                 # one retry if the reply is malformed
        try:
            reply = json.loads(chat([{"role": "user", "content": prompt}],
                                    format=check_schema(len(drafts)), temperature=0))["answers"]
        except (ValueError, KeyError, TypeError):
            continue
        letters = [str(a).strip().upper()[:1] for a in reply]
        if len(letters) == len(drafts):
            return [letters[i] == "ABCD"[d["answer"]] for i, d in enumerate(drafts)]
    return [True] * len(drafts)                        # checker broke twice: keep the quiz, don't lose it
