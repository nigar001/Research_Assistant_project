# Research Assistant — Topic 4

## Overview
The Async Research Assistant is a command-line tool that answers user questions by querying Wikipedia, arXiv, and web-search APIs in parallel. It retrieves relevant excerpts from these sources and uses a Large Language Model (LLM) to synthesize a single, concise answer with inline bracketed citations.

## Key Features
* **Concurrent Orchestration:** Fetches data from all sources simultaneously using `asyncio.gather` with per-source timeouts.
* **Graceful Degradation:** If one source (e.g., arXiv) fails or times out, the system continues and synthesizes an answer from the remaining successful sources.
* **TTL-Aware Caching:** Uses a filesystem-based JSON cache keyed by the canonicalized query and source. This prevents redundant API calls for duplicate questions until the Time-To-Live (TTL) expires.
* **Robust Retries:** Implements exponential backoff with equal jitter for transient HTTP errors (like 429 Rate Limit or 5xx server errors) and LLM provider errors.
* **Pluggable Providers:** Supports multiple LLM providers (Anthropic, OpenAI, Google Gemini) and web search backends (Tavily, Serper, DuckDuckGo).

## Architecture & Design Decisions
This tool is built with a clear separation of concerns, isolating the core AI prompt logic from the software engineering layer (retries, caching, concurrency).
* **Connection Pooling:** A shared `httpx.AsyncClient` is passed down to all source fetchers to amortize connection setup times.
* **Fault-Tolerant Concurrency:** The orchestrator uses `return_exceptions=True` alongside `asyncio.wait_for`. This ensures a slow or failing third-party API never crashes the main process or blocks other successful fetches.
* **Jittered Backoff:** Retries utilize equal jitter backoff (half exponential delay + half random) to prevent thundering herd problems when APIs are rate-limited.
* **Atomic Cache Writes:** The JSON cache writes to a temporary file before using `os.replace` to guarantee atomic updates and prevent corrupt reads during concurrent executions.

## Project Structure
```text
├── ai/                # Provided Core AI module (fetchers, synthesizer, schemas)
├── src/               # Software Engineering Layer
│   ├── concurrency/   # asyncio orchestration and per-source timeouts
│   ├── core/          # Business logic and pipeline assembly
│   ├── services/      # Retry loops, backoff math, and logging wrappers
│   └── storage/       # TTL-aware JSON caching and file operations
├── tests/             # Offline test suite with respx HTTP mocking
├── benchmark.py       # Sequential vs. Concurrent performance benchmarking
└── requirements.txt   # Core project dependencies
```

## Prerequisites
* Python 3.12+
* API keys for your chosen LLM and Web Search providers.

## Installation & Setup

1. **Clone the repository and navigate to the project root.**
2. **Install dependencies:** 
   ```bash
   pip install -r requirements-ai.txt -r requirements.txt
   ```
   *Note: Install your specific LLM SDK as an extra (e.g., `pip install anthropic`, `pip install openai`, or `pip install google-genai`).*

3. **Configure Environment Variables:** Copy `.env.example` to `.env` and fill in your values. 
   ```env
   # --- LLM (for synthesis) ---
   LLM_PROVIDER=gemini         # anthropic | openai | gemini
   LLM_MODEL=gemini-2.5-flash
   GEMINI_API_KEY=your_api_key_here

   # --- Web search provider ---
   WEB_SEARCH_PROVIDER=tavily  # tavily | serper | duckduckgo
   TAVILY_API_KEY=your_api_key_here 

   # --- SE-layer settings ---
   LOG_LEVEL=INFO
   CACHE_DIR=./.cache
   CACHE_TTL_SECONDS=86400
   PER_SOURCE_TIMEOUT_SECONDS=10
   MAX_SOURCES_PER_QUERY=3
   ```

## Usage

The primary interface is the `ask` command via the `researcher` CLI module.

**Basic Query:**
```bash
python -m researcher ask "What is photosynthesis and what are its main stages?"
```

**Restrict Sources:**
You can limit the search to a specific subset of sources using the `--sources` flag. Valid aliases include `wiki`, `wikipedia`, `arxiv`, and `web`.
```bash
python -m researcher ask "How do transformers work?" --sources arxiv,web
```

**Bypass Cache:**
To ignore stored results and force a fresh fetch from all APIs, use the `--no-cache` flag.
```bash
python -m researcher ask "Current state of fusion energy?" --no-cache
```

## Example Output

A real run. Wikipedia currently returns nothing for question-shaped queries (see
*Known limitations*), so the answer is produced from the remaining sources and the
missing one is named rather than silently dropped:

```text
$ python -m researcher ask "What is photosynthesis and what are its main stages?"

Q: What is photosynthesis and what are its main stages?

A: Photosynthesis is a vital process that plants, algae, and some bacteria use to
   convert light energy into chemical energy. It consists of two main stages: the
   light-dependent reactions and the light-independent reactions (Calvin cycle). In
   the light-dependent reactions, which occur in the thylakoid membranes, sunlight
   energy is used to produce ATP and NADPH while releasing oxygen as a byproduct.
   The light-independent reactions then utilize ATP, NADPH, and carbon dioxide to
   synthesize sugars [4,5,6].

References:
  [4] (web) Breaking down photosynthesis stages (video)
      https://www.khanacademy.org/science/ap-biology/...
  [5] (web) Photosynthesis
      https://education.nationalgeographic.org/resource/photosynthesis
  [6] (web) Photosynthesis
      https://en.wikipedia.org/wiki/Photosynthesis

Note: wikipedia unavailable — answered from the remaining sources.
Completed in 4.95s.
```

Citation numbers follow each excerpt's position in the collected list, and the
synthesizer drops the indices it did not use rather than renumbering — which is why
the first reference here is `[4]`.

## Testing & Benchmarking

* **Automated Tests:** The project includes a comprehensive test suite (unit and smoke tests) with over 60% coverage. It uses `respx` to mock asynchronous HTTP traffic, allowing the suite to run completely offline. 
  ```bash
  pytest
  ```
* **Benchmarking:** Compare the wall-clock time of sequential fetching versus concurrent fetching using the provided benchmark script.
  ```bash
  python benchmark.py --repeats 3
  python benchmark.py --with-llm  # Includes end-to-end synthesis
  ```

### Results: sequential vs concurrent fetching

Measured 18 September 2026 on Linux / Python 3.12.3, over all five questions in
`data/research_questions.json`, three timed runs each. Both paths use the same shared
client, the same per-source timeout and the same retry wrapper, so only the scheduling
differs; the cache is not involved, because a cache hit would mean no fetch happened.

| Question | Sequential (s) | Concurrent (s) | Speed-up |
| :--- | ---: | ---: | ---: |
| What is photosynthesis and what are its main stages? | 3.11 | 2.15 | 1.45x |
| How do transformer-based language models handle long context windows? | 3.11 | 1.16 | 2.68x |
| What were the main causes of the 2008 financial crisis? | 2.39 | 1.47 | 1.62x |
| What is the current state of fusion energy research? | 2.12 | 1.64 | 1.29x |
| How does CRISPR-Cas9 gene editing work at a molecular level? | 2.87 | 2.43 | 1.18x |
| **Mean** | **2.72** | **1.77** | **1.54x** |

End to end, including synthesis, the mean is **6.25 s** — of which concurrent fetching is
about 1.9 s. The remaining ~4.3 s is the single LLM call, which is one request for one
question and cannot be parallelised. Concurrency converts the *sum* of the three fetches
into their *maximum*, so the gain depends on how uneven the sources are: 2.68x where one
source dominated, 1.18x where all three were already similar.

Full method and interpretation: [`REPORT.md`](REPORT.md) §5.

## Docker Support

The image runs the application end to end. The test suite runs by overriding the
entrypoint.

```bash
# build and run a real query (needs .env with your keys)
docker compose up --build

# run the offline test suite instead
docker compose run --rm --entrypoint pytest research-pipeline
```

`.dockerignore` excludes `.env`, which matters: the Dockerfile uses `COPY . .`, so without
it real API keys would be baked into an image layer. `docker-compose.yml` mounts `./.cache`
so cached results persist between runs.

## Known limitations

- **Wikipedia returns nothing for question-shaped queries.** `ai/sources.py` uses the
  `action=opensearch` endpoint, which matches the *start of an article title*:
  `"Photosynthesis"` returns results, `"What is photosynthesis?"` returns none. The API
  answers HTTP 200 with an empty list, so this is not rate limiting. The fix is Wikipedia's
  full-text `list=search` endpoint, which lives inside `ai/` and cannot be edited under the
  project contract. Graceful degradation reports the source as unavailable and the answer is
  produced from arXiv and web search.
- **No timeout on synthesis.** Each fetch has a per-source deadline; the LLM call does not.
- Further limitations are catalogued in [`REPORT.md`](REPORT.md) §8.
