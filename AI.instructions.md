# AI assistant instructions — Async Research Assistant

Shared project context for anyone using an AI coding assistant on this repo (Claude Code, Cursor,
Copilot, etc.). Keep this file accurate — if a fact here goes stale, fix it in a PR.

University software-engineering final project (AI-ENG-110), **Topic 4: Async Research Assistant**.
A question goes in → Wikipedia + arXiv + web search are fetched **concurrently** → an LLM
synthesises one answer with inline `[N]` citations.

---

## What is provided vs. what we build

The `ai/` package **ships finished** and is already committed:

- `ai/sources.py` — `fetch_wikipedia()`, `fetch_arxiv()`, `fetch_web()`, all async, all returning
  `list[Source]`, each accepting an optional `client: httpx.AsyncClient`. Also a
  `WebSearchProvider` ABC with working Tavily / Serper / DuckDuckGo adapters, chosen via the
  `WEB_SEARCH_PROVIDER` env var.
- `ai/synthesizer.py` — `synthesize(question, sources)` → `AnswerWithCitations`. Out-of-range and
  hallucinated citation indices are already dropped.
- `ai/providers/` — Anthropic / OpenAI / Gemini behind a factory, chosen via `LLM_PROVIDER`.

We build the **software-engineering layer** around it: config, concurrency orchestration, caching,
retries, validation, logging, CLI, tests, Docker, README, report.

## The contract — breaking these costs marks

From `TOPIC.md`:

- **Never edit anything under `ai/`.**
- **Never weaken or delete `tests/test_ai_smoke.py`** — it is run during grading and must pass.
- **Never call provider SDKs or source APIs directly from business logic.** Always go through
  `ai.fetch_wikipedia`, `ai.fetch_arxiv`, `ai.fetch_web`, `ai.synthesize`. No `tavily-python` or
  `duckduckgo-search` calls anywhere in `src/`.

Plus:

- **Tests run fully offline.** Mock async HTTP with `respx` or `pytest-httpx`. Network in tests is
  an automatic deduction.
- **No hard-coded API keys.** Env vars only. `.env` is gitignored; `.env.example` is committed.
  Each teammate keeps their own local `.env` — keys are never shared.
- **No leftover TODOs** at submission.
- **≥60% test coverage.**

## Resolved scope decision (settled — do not revisit)

An earlier distribution doc assigned someone to write `src/sources/base.py`,
`src/sources/tavily.py`, and `src/sources/duckduckgo.py`. That duplicated `ai/sources.py` and
broke the contract rule above, and it dropped Wikipedia and arXiv from the plan entirely, which
would have removed the concurrency story the project is graded on.

**The team has agreed this was wrong.** `src/sources/` is dropped. All fetching goes through
`ai.fetch_*`. If `src/sources/` still appears in the tree it is leftover — remove it, don't
extend it.

## File ownership

Contribution is graded per person. Do not write files you don't own.

**Member 1 — Nigar (core & concurrency)**
`src/config.py` (Pydantic settings), `src/models.py` (our own schemas, e.g. `ResearchSession`),
`src/concurrency/orchestrator.py` (`asyncio.gather` + `Semaphore` + per-source timeouts),
`src/core/researcher.py` (business logic tying cache → fetch → synthesis together).

**Member 2 — HTTP layer & delivery**
The shared `httpx.AsyncClient`, per-source `asyncio.timeout()` wrappers, retry/backoff policy on
the fetchers, the `respx` offline test suite, the Dockerfile.

**Member 3 — Farid (services, storage, interface)**
`src/services/ai_service.py` (retries + logging around `ai.synthesize` — a thin service layer, not
an LLM implementation), `src/storage/cache_store.py` (TTL cache), `src/cli.py`.

## Build order

1. `src/config.py`
2. `src/models.py` — the shared schema everything downstream depends on
3. `src/storage/cache_store.py` — no dependency on 1–2, can start immediately
4. `src/services/ai_service.py` — needs the schema agreed; buildable against fake source objects
5. `src/concurrency/orchestrator.py`
6. `src/core/researcher.py`
7. `src/cli.py` — last, it's the integration layer

## CLI spec — match this exactly

```bash
python -m researcher ask "your question"
python -m researcher ask "your question" --sources wiki,arxiv
python -m researcher ask "your question" --no-cache
```

An earlier team doc suggested `python -m src.cli "query"`. That does not match the spec.

## Known environment issues (already diagnosed, don't re-debug)

The shared `httpx.AsyncClient` must be constructed as:

```python
httpx.AsyncClient(
    timeout=10.0,
    follow_redirects=True,   # arXiv hardcodes http:// and 301-redirects to https://
    headers={"User-Agent": "ResearchAssistant/1.0 (AI-ENG-110 student project)"},  # Wikipedia 403s generic UAs
)
```

Without both settings, arXiv raises on the 301 and Wikipedia returns 403. Confirmed live.

`WEB_SEARCH_PROVIDER` is a config choice, not code: `tavily` (needs `TAVILY_API_KEY`, free tier)
or `duckduckgo` (no key). Both already implemented in `ai/`.

`demo_ai.py` fails on Wikipedia/arXiv unless run with `--offline` — it builds a bare client.
Expected; it isn't part of the deliverable.

## What the grade rewards

Code 60 (Correctness 22, Architecture/OOP 13, Concurrency 10, Robustness 8, Testing 7),
Report 25, Presentation 15.

The headline result is a **sequential vs. concurrent benchmark** — wall-clock time for
sum-of-three-sources vs. max-of-three — with bounded parallelism via `asyncio.Semaphore`, plus
naming the new bottleneck. Concurrency must show per-source `asyncio.timeout()`,
`gather(return_exceptions=True)`, and graceful degradation: if arXiv dies, the answer is still
produced from the other two.

Cache keys are `(source, canonicalised query)`. `"WHAT IS PHOTOSYNTHESIS?"` and
`"what is photosynthesis"` must hit the same key.

## Git workflow (enforced by branch protection)

Never commit to `main`. Branch as `<name>/<short-description>`, push, open a PR, one teammate
approves, squash-and-merge, delete the branch.

Commit messages: `<verb>: <one line under 50 chars>` — `add: TTL cache with configurable expiry`,
`fix: 429 handling with jitter`, `test: cover empty-source edge case`. Never `wip` or `fix stuff`.

Commit distribution is graded; a member under 10% of commits costs the team 5 points.

## Environment

`uv` for packages (`uv venv`, `uv pip install ...`), venv at `.venv`.
`requirements-ai.txt` belongs to the provided `ai/` package; our own dependencies go in a separate
`requirements.txt`.
