# Reasearch_Assistant_project

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
   GOOGLE_API_KEY=your_api_key_here

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
```text
Q: What is photosynthesis and what are its main stages?

A: Photosynthesis is the process by which plants convert light energy into chemical energy [1]. The reaction takes place in the chloroplasts and produces oxygen as a byproduct [2].

References:
  [1] (wikipedia) Photosynthesis
      https://en.wikipedia.org/wiki/Photosynthesis
  [2] (arxiv) Light-Dependent Reactions of Photosynthesis
      https://arxiv.org/abs/1706.03762

Note: web unavailable - answered from the remaining sources.
Completed in 2.45s.
```

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

## Docker Support
The project includes a `Dockerfile` that builds the application and defaults to running the `pytest` test suite. A `docker-compose.yml` is also provided to mount the local cache directory so results persist on your host.
```bash
docker-compose up --build
```
