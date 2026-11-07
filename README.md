# StudyBuddy

Ask questions about your own PDFs and get answers from a local AI, with the file and page
each answer came from. Everything runs on your computer through [Ollama](https://ollama.com).

## Setup (once)

1. Install Ollama, then pull the two models:
   ```bash
   ollama pull llama3.1:8b
   ollama pull nomic-embed-text
   ```
2. Create the Python environment:
   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

## Run the web app

```bash
cd backend
.venv\Scripts\activate
uvicorn server:app --reload
```

Open http://localhost:8000, add PDFs with **＋ Add PDF** (or drag them onto it), and ask away.
Follow-up questions like "explain that more simply" use the conversation so far.

## Run in the terminal instead

Put PDFs in `backend/data/`, then:

```bash
python ask.py data
```

## How it works

| File | Job |
|---|---|
| `backend/ingest.py` | PDF → cleaned text → chunks of ~800 characters, split at section headings |
| `backend/retrieve.py` | Chunks → 768-number vectors (`nomic-embed-text`); question → top 3 chunks by cosine similarity, ignoring any below `MIN_SCORE` |
| `backend/ask.py` | Rewrites follow-ups into standalone questions, then has `llama3.1:8b` answer **only** from the retrieved chunks |
| `backend/server.py` | FastAPI: upload/list/delete PDFs, stream answers, serve the web page |
| `frontend/` | The chat page (plain HTML, CSS and JavaScript) |

Your PDFs and their index files live in `backend/data/`, which is not committed to git.
