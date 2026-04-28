# OC4 Study Build Trainer

A microservice that builds and queries a corpus of historical
protocol-to-form pairs, used to improve EDC build quality in the
oc-ai-pipeline via Retrieval-Augmented Generation (RAG).

**Status:** Skeleton — Phase 1 in progress. Not yet runnable end-to-end.
**Repo location (Phase 1):** `oc-ai-pipeline/services/study-build-trainer/`
**Repo location (later):** intended to extract to `oc-study-build-trainer`
**Planning doc:** `oc-ai-pipeline/TODO/FUTURE-PROJECT-rag-and-trainer.md`

## What this service does

There are two responsibilities, sequenced in time.

### Phase 1 — Internal RAG (current)

1. **Corpus ingest.** Humans drop form designs (and optionally protocols)
   into a monday board. The trainer parses the form, infers a study
   fingerprint (sponsor / intervention / indication / phase / study type),
   either uses the supplied protocol or searches clinicaltrials.gov for one,
   then embeds and indexes the form-protocol pair into a local vector store.

2. **Runtime retrieval.** When the oc-ai-pipeline starts processing a new
   protocol, it calls `POST /retrieve` with the protocol text or a
   pre-computed fingerprint. The trainer returns the top-k similar past
   pairs, which the pipeline then injects into its EDC structure prompt as
   few-shot examples.

### Phase 2-3 — Productized trainer (future)

The same machinery, packaged for customers to run on their own historical
data. Out of scope for the current skeleton.

## Microservice discipline (even though it lives inside oc-ai-pipeline)

This service is built so it can be lifted out of `oc-ai-pipeline` into its
own repo with minimal pain later. To make that easy:

- It is its own FastAPI app with its own entry point.
- **The pipeline calls it over HTTP** (`POST localhost:8001/retrieve`),
  even when both run in the same Railway container.
- It has its own dependency list (`pyproject.toml` here, separate from the
  pipeline's `requirements.txt`).
- **No shared imports** between trainer code and pipeline code. If both
  need an OC client or a monday client, each gets its own copy.

When we extract: `git filter-repo` to split the history, point a new
Railway service at the new repo, change `localhost:8001` to a real
hostname in the pipeline. ~Half a day of work, no logic changes.

## Architecture at a glance

```
                    ┌─────────────────────────┐
                    │  monday board           │
                    │  "Study Build Trainer   │
                    │   — Corpus" (18410424473)│
                    └────────┬────────────────┘
                             │ webhook on column change
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  oc-study-build-trainer (FastAPI, port 8001)                 │
│                                                              │
│  POST /webhook/monday  ──► queue ──► ingest_worker           │
│  POST /retrieve        ──► vector_store.query()              │
│  POST /ingest          ──► queue ──► ingest_worker (manual)  │
│  GET  /jobs/{id}       ──► job_status                        │
│  GET  /corpus/stats    ──► corpus metadata                   │
│                                                              │
│  ingest_worker:                                              │
│    parse form → extract fingerprint → search ct.gov          │
│    → score candidates → write to monday → wait for human     │
│    → embed → store in vector_store                           │
└──────────────────────────────────────────────────────────────┘
```

## Tech choices (Phase 1)

| Concern              | Choice                       | Why                                  |
|----------------------|------------------------------|--------------------------------------|
| Web framework        | FastAPI                      | Same as oc-ai-pipeline; async-native |
| Embedding model      | BAAI/bge-large-en-v1.5       | Local, no third-party data exposure  |
| Vector store         | SQLite + sqlite-vec          | Zero infra; ~1-10K vectors fits easily|
| Form parsing — XML   | lxml                         | Industry standard for ODM XML        |
| Form parsing — XLSX  | openpyxl                     | Already used elsewhere in the codebase |
| Form parsing — PDF   | pdfplumber                   | Better text extraction than pypdf     |
| LLM (fingerprint)    | Claude via anthropic SDK     | Already configured in pipeline        |
| Job queue            | In-process asyncio queue     | Phase 1 — single container is fine    |
| HTTP client          | httpx                        | Async; works inside FastAPI cleanly   |

## Local development

Not yet runnable — files are skeletons. See individual module docstrings
for what each piece is supposed to do and the TODOs that block it.

```bash
# Future (once skeletons are filled in):
cd services/study-build-trainer
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # then fill in real values
uvicorn app.main:app --port 8001 --reload
```

## Layout

```
study-build-trainer/
├── README.md                  this file
├── pyproject.toml             dependencies + project metadata
├── .env.example               env var template
├── Dockerfile                 (later) for Railway deploy
├── railway.toml               (later) Railway service config
│
├── app/                       FastAPI surface
│   ├── main.py                app factory, startup/shutdown
│   ├── config.py              env-var-backed settings
│   ├── deps.py                shared dependencies (clients, etc.)
│   └── routes/
│       ├── health.py          GET /health
│       ├── webhook.py         POST /webhook/monday
│       ├── ingest.py          POST /ingest (manual trigger)
│       ├── retrieve.py        POST /retrieve (called by pipeline)
│       └── jobs.py            GET /jobs/{id}, GET /corpus/stats
│
├── core/                      domain logic (no FastAPI imports here)
│   ├── form_parser/
│   │   ├── base.py            abstract Parser + ParsedForm dataclass
│   │   ├── odm_xml.py         parse ODM XML
│   │   ├── xlsform.py         parse XLSForm .xlsx
│   │   └── pdf.py             parse PDF (text extraction)
│   ├── fingerprint.py         Claude-based study fingerprint extraction
│   ├── ctgov_client.py        clinicaltrials.gov API client
│   ├── matcher.py             score CT.gov candidates against fingerprint
│   ├── embed.py               BAAI/bge-large-en-v1.5 wrapper
│   ├── vector_store.py        sqlite-vec wrapper
│   └── monday_client.py       monday API client (this service's copy)
│
├── workers/                   long-running async tasks
│   ├── queue.py               in-process FIFO queue
│   └── ingest_worker.py       runs the ingest pipeline per job
│
├── corpus/                    LOCAL DATA — gitignored
│   └── (vector DB, raw files cache, etc.)
│
└── tests/
    ├── fixtures/              sample form designs + protocols for tests
    └── test_smoke.py          basic smoke tests
```

## Open questions (track in TODO/FUTURE-PROJECT-rag-and-trainer.md)

- What's the embedding input format? Full protocol text vs structured
  metadata summary vs both?
- Index granularity: protocol-level, form-level, or both?
- Confidence threshold tuning: starting at ≥0.9 with `(sponsor) AND
  (intervention) AND (indication OR phase)` — needs validation against
  real corpus.
- How does the pipeline call this service? (HTTP today; might revisit
  if both run in same container and we want to skip the loopback.)
