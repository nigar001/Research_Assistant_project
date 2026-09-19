# Topic 4 — Async Research Assistant

## Project report

**Team:** Researches · **Repository:** https://github.com/nigar001/Research_Assistant_project
**Members:** Nigar Alimammadova (`@nigar001`) · Farid Dilaverli (`@faridqul`) · Sahib Aliyev

## §1 · Introduction and problem statement

A researcher asking a factual question usually consults several sources: an encyclopaedia for
background, a paper archive for current work, and the open web for everything else. Doing this
by hand is slow, and doing it in software naïvely is barely faster, because each source is a
network call that spends almost all of its time waiting.

This project builds a command-line research assistant that queries Wikipedia, arXiv and a
web-search API **at the same time**, then asks a large language model to write a single answer
whose claims carry numbered citations back to the retrieved excerpts.

The AI layer was supplied to us as a finished package, `ai/`, which the project contract
forbids us to modify: three asynchronous fetchers, a pluggable web-search abstraction, and a
synthesiser that returns an answer with its citations already validated. **Everything around it
is ours**: the concurrency layer that overlaps the three fetches, a cache so a repeated question
costs nothing, retries with exponential backoff, configuration, validation, logging, the command
people type, the container it ships in, and a test suite that never touches the network.

The engineering problem, stated once: _three slow, independently unreliable network calls must
behave like one fast, reliable one._ Sections §3–§5 are about how, §7 about how we know it
works, and §8 about where it still does not.

---

## §2 · Requirements and how we satisfied them

Every row of the "What you build" table in `TOPIC.md`, and where it is satisfied.

| Requirement                                                         | Where it lives                                              | §            | Done by      |
| ------------------------------------------------------------------- | ----------------------------------------------------------- | ------------ | ------------ |
| `config.py` — typed settings from the environment                   | `src/config.py`                                             | §4.1         | Nigar        |
| Concurrent orchestration, per-source timeouts, graceful degradation | `src/concurrency/orchestrator.py`                           | §4.2, §5     | Nigar        |
| Caching keyed by `(source, query)` with TTL; `--no-cache` bypass    | `src/storage/cache_store.py`, `src/services/cache.py`       | §4.4.1, §4.3 | Farid, Sahib |
| CLI `python -m researcher ask "…"`, `--sources`                     | `src/cli.py`, `researcher/`                                 | §4.4.4       | Farid        |
| Citation rendering                                                  | `src/cli.py`                                                | §4.4.4       | Farid        |
| Retries with exponential backoff on `ai.*` calls and HTTP fetches   | `src/services/ai_service.py`                                | §4.4.2       | Farid        |
| Validation — reject empty and oversized questions                   | `src/models.py`, `src/cli.py`, `src/services/ai_service.py` | §4.1, §4.4   | Nigar, Farid |
| Logging with an env-driven level                                    | `src/cli.py`, module loggers throughout                     | §4.4.4       | Farid        |
| Tests: ≥60% coverage, fully offline                                 | `tests/`                                                    | §7           | all three    |
| Dockerfile that builds and runs                                     | `Dockerfile`, `docker-compose.yml`, `.dockerignore`         | §6           | Nigar        |
| README: setup, env, run, test, timings                              | `README.md`                                                 | §6           | Sahib        |

**Achieved:** 141 tests passing offline in about 2.7 seconds, **96% statement coverage** of
`src/`, and the 16 supplied smoke tests still passing unmodified.

---

## §3 · Architecture

### Layering

The system is five layers, and each one knows only about the layer beneath it.

```
            cli.py                composition root — builds everything, once
               │
         core/researcher.py       the order of operations
               │
    ┌──────────┼──────────┐
 storage/   concurrency/  services/
 cache_store orchestrator ai_service
    └──────────┼──────────┘
               │
              ai/                 supplied, never edited
```

Two consequences matter more than the diagram:

**Objects are built in exactly one place.** `cli.py` is the only module that reads settings,
opens the shared HTTP client, chooses a cache implementation and assembles the workflow.
Everything below receives what it needs as a constructor argument. This pattern — a
_composition root_ — is why the other modules can be tested with fakes: none of them constructs
its own dependencies, so a test can substitute any of them.

**The workflow does not import the orchestrator.** `researcher.py` declares a `typing.Protocol`
naming the single method it needs, and the orchestrator satisfies it structurally, without
inheriting anything. The two modules therefore have no import relationship in either direction,
which let two people build them in parallel against an agreed signature. The honest caveat is
in §8: a Protocol is checked by a type checker, not at runtime.

### Request flow

1. `cli.py` loads `.env`, builds settings, logging, the HTTP client and the cache.
2. Input is validated and source aliases are expanded (`wiki` → `wikipedia`).
3. `researcher.ask()` asks the cache for each requested source.
4. Sources that missed are fetched — all at once, each under its own deadline.
5. Fresh results are written back to the cache; failures are recorded, not raised.
6. The collected excerpts go to the LLM, which returns an answer with citations.
7. `cli.py` prints the answer, the references, and any source that failed.

---

## §4 · Component walkthroughs

### §4.1 · `src/config.py`, `src/models.py` — Nigar

_config 66 lines · models 19 lines_

`Settings` is a `pydantic_settings.BaseSettings` subclass: each setting is declared once with a
type, a default and a description, and pydantic reads it from the environment or `.env`,
converting and validating as it goes. A missing variable falls back to its default; a malformed
one fails at start-up with a message naming the field, rather than failing later as a confusing
`TypeError`.

Settings cover provider selection (`llm_provider`, `llm_model`, `web_search_provider`), the five
optional API keys, `per_source_timeout_seconds` (8.0), `max_retries` (3), `cache_dir` and
`cache_ttl_seconds` (86 400 — one day), and `log_level`. `extra="ignore"` means an unrelated
variable in the environment cannot crash the application, and `case_sensitive=False` allows the
conventional upper-case names in `.env`.

A module-level `settings = Settings()` gives every module one shared instance, so configuration
is read once rather than rebuilt per call. The consequence is worth stating plainly, because it
constrains another file: the object is constructed **at import time**, so anything that must be
in the environment first has to happen before `src.config` is imported. That is exactly why
`cli.py` calls `load_dotenv()` as its first statement and imports `settings` afterwards
(§4.4.4). An import-time singleton is convenient and it is also a hidden ordering requirement;
we accepted it, and documented the ordering rather than leaving it to be rediscovered.

`models.py` holds the three pydantic models that cross layer boundaries: `ResearchRequest`
(with `min_length=3, max_length=500` on the question — validation expressed as a type rather
than as a check somewhere), `CitationModel`, and `ResearchResponse`, which carries the answer,
its citations, `wall_clock_time_seconds` and `degraded_sources`. Keeping these in one module
means the CLI, the workflow and the tests all agree on one vocabulary.

### §4.2 · `src/concurrency/orchestrator.py` — Nigar

_107 lines · 3 tests_

`ConcurrentOrchestrator.fetch_all()` is where the project's headline claim is implemented. It
takes a question and a list of source names and returns a dictionary mapping each source to
**either** its results **or** the exception that stopped it.

- **`asyncio.gather(..., return_exceptions=True)`.** Without that flag, the first failing
  fetch would cancel the gather and lose the results of the sources that succeeded. With it,
  each task's outcome — value or exception — is returned as data, which is precisely what
  graceful degradation needs: the workflow can then answer from what survived.
- **A per-source deadline, not one deadline for the batch.** Each fetch is wrapped in
  `asyncio.wait_for(..., timeout=8.0)`. A single timeout around the gather would let one slow
  source consume the entire window and starve the others; per-source, a hanging Wikipedia
  cannot shorten arXiv's budget.
- **`asyncio.Semaphore(5)` bounds concurrency.** With three sources it never binds today; it
  exists so that adding sources later cannot open an unbounded number of sockets at once.
- **The method is documented never to raise.** Unknown source names, timeouts and provider
  errors are all converted into entries in the returned dictionary.
- **Fetches are retried through `fetch_with_retry`** (§4.4.2), so the retry policy is shared
  with synthesis rather than reimplemented here.

One structural note worth making in the defence: because retries happen _inside_
`wait_for`, all attempts share the same 8-second budget. That is deliberate — the deadline is a
promise to the user about total latency, not about a single attempt.

### §4.3 · `src/services/cache.py`, `src/services/http_client.py` — Sahib

_cache 40 lines · http_client 10 lines · 6 tests_

`CacheService` sits between the workflow and the cache backend and does one thing the backend
deliberately does not: it handles **many keys at once**. `get_cached_sources` launches one
`store.get` per requested source under `asyncio.gather` and returns only the hits;
`save_fetched_sources` writes the fresh results back the same way. Because misses are simply
absent from the returned dictionary, the caller computes what still needs fetching with a plain
set difference, and no `None` checks leak into the workflow.

This split is worth defending: `cache_store.py` knows how to store _one_ entry safely, and
`CacheService` knows how to do _several_ concurrently. Neither knows about the other's concern.

`http_client.py` builds the one `httpx.AsyncClient` that all three fetchers share in a request.
Sharing it reuses TCP connections instead of repeating a handshake per source. Two settings are
not optional and were both established by observation, not by reading documentation:
`follow_redirects=True`, because arXiv answers `http` with a `301` to `https`, and an explicit
`User-Agent`, because Wikipedia rejects generic client identifiers.

One deliberate asymmetry with the orchestrator: `CacheService` calls `asyncio.gather` **without**
`return_exceptions=True`, where the orchestrator uses it. That is safe today because the
contract of `CacheStore` is that it never raises — every failure inside it is logged and turned
into a miss (§4.4.1) — so there is nothing for `gather` to propagate. It is, however, a coupling
worth naming: the safety of this layer depends on a promise made by the layer beneath it. If the
store ever began raising, one failed write would cancel the others. Adding `return_exceptions=True`
here would cost nothing and remove the dependency.

### §4.4 · `cache_store.py`, `ai_service.py`, `researcher.py`, `cli.py` — Farid

#### §4.4.1 `src/storage/cache_store.py` — the TTL cache

_198 lines · 29 tests · 94% covered_

Stores the excerpts returned for a `(source, query)` pair so the same question costs no network
calls the second time. `CacheStore` is an abstract base class declaring `async get` and
`async set`; `JsonFileCacheStore` is the working backend and `NullCacheStore` is a cache that
stores nothing.

- **One JSON file per entry, not one shared index.** The three fetchers finish at roughly the
  same moment. Against a shared file they would each read, modify and write it, and the last
  writer would erase the other two entries — a lost update. Separate files make that race
  impossible rather than merely unlikely, which is why the cache needs no lock.
- **The filename is `sha256(source + "\x1f" + canonical_query)`.** Questions contain `/`, `?`,
  spaces and accented characters, which break filenames or escape the directory, and filenames
  cap at about 255 bytes. `\x1f` is a control character that cannot appear in either half, so
  `("wiki_a", "b")` cannot collide with `("wiki", "a_b")`.
- **Queries are canonicalised: NFKC → collapse whitespace → `casefold()` → strip trailing
  `?!.`** `casefold()` rather than `lower()` because it is the Unicode fold built for
  comparison — German `ß` folds to `ss`, which `lower()` leaves alone. Punctuation is stripped
  only at the **end**: stripping it everywhere turns `"What is C++?"` into `"what is c"`, the
  same entry as `"What is C?"`, and the cache would then serve C answers to C++ questions
  without any error.
- **Writes are atomic.** The entry is written to a uniquely named temporary file in the same
  directory and then moved into place with `os.replace()`, which the operating system
  guarantees happens completely or not at all. Without it, a crash mid-write leaves half a JSON
  file that breaks every later run. The temporary file must share a filesystem with its target,
  or `os.replace` raises `OSError: Invalid cross-device link`.
- **Expiry is lazy.** An entry stores an absolute `expires_at`; a read past that time deletes
  the file and reports a miss. Checking on read costs nothing extra and keeps `.cache/` from
  growing without a cleanup job.
- **It never raises.** Corrupt JSON, a missing field, an unwritable directory — each is logged
  and treated as a miss. A cache must never be the reason the application fails; the worst case
  is a re-fetch, which is what a run with no cache does anyway.
- **`NullCacheStore` is how `--no-cache` works.** Injecting a cache that stores nothing means
  there is no `if use_cache:` branch anywhere below the CLI, so the flag changes no other
  module's logic. (Null Object pattern.)

#### §4.4.2 `src/services/ai_service.py` — retries around every `ai.*` call

_234 lines · 41 tests · 100% covered_

`TOPIC.md` requires exponential backoff on every `ai.*` call **and** every HTTP fetch. Both go
through one retry loop, `_with_retry`, so there is a single retry policy rather than two that
drift apart. `synthesize_with_retry` wraps the LLM call; `fetch_with_retry` wraps one
`ai.fetch_*` call and is what the orchestrator uses.

- **`ai.synthesize` is synchronous, so it runs on a worker thread** via `asyncio.to_thread`.
  Called directly it would block the event loop for the whole LLM call, stopping every other
  task — the most costly possible mistake in a project graded on concurrency.
- **The retry loop takes a factory, not a coroutine.** A coroutine can only be awaited once;
  awaiting the same object on attempt two raises `RuntimeError: cannot reuse already awaited
coroutine`. Each attempt therefore calls `call()` to build a fresh one.
- **Only `Exception` is caught, never `BaseException`.** `asyncio.CancelledError` inherits from
  `BaseException`, so when the orchestrator's deadline fires, the cancellation passes straight
  through the retry loop, including out of a pending backoff sleep. Catching too broadly would
  let a source outlive its timeout.
- **Backoff uses equal jitter:** `nominal/2 + random.uniform(0, nominal/2)`. The exponential
  half stops us hammering a provider that is already struggling; the random half stops several
  failed calls retrying in the same instant and colliding again.
- **Fetches and synthesis are retried on different criteria.** Synthesis retries any
  `ProviderError`. Fetches inspect `exc.__cause__`: an `httpx.TransportError`, a 429 or a 5xx is
  retried; a 404, a missing API key or malformed XML fails on the first attempt, because no
  number of retries fixes them. `ValueError` — raised for an empty question or empty sources —
  is never retried for the same reason.
- **Fetches back off from 0.5s rather than 1.0s**, so three attempts fit inside the
  orchestrator's per-source deadline instead of being cancelled mid-wait.

#### §4.4.3 `src/core/researcher.py` — the workflow

_155 lines · 20 tests · 100% covered_

`AsyncResearchAssistant.ask()` is the order of operations: read the cache, fetch only the
sources that missed, store what came back, synthesise, and assemble the response with its timing
and the list of sources that failed.

- **It depends on a `typing.Protocol`, not on the orchestrator module** — the workflow declares
  the shape it needs and any object with that shape satisfies it, so the tests drive it with a
  small fake and the two modules never import each other.
- **Everything it needs is injected** — orchestrator, cache, LLM — which is what lets all 20
  tests run with no network, no disk and no API key.
- **Graceful degradation is a returned value, not an exception.** A failed source is recorded in
  `degraded_sources` and the answer is produced from the rest. Only total failure — no sources
  at all — raises.
- _Note for §9: `services/cache.py` was later extracted from this file's cache handling by
  Sahib, so the current version calls `CacheService` rather than the store directly._

#### §4.4.4 `src/cli.py` and `researcher/` — the entry point

_196 + 11 lines · 26 tests · 95% covered_

Implements the specified command: `python -m researcher ask "…"`, with `--sources` and
`--no-cache`. `researcher/` is a two-file shim that makes `python -m researcher` work without
renaming `src/` and breaking every import.

- **This file is the composition root** — the only place that reads settings, configures
  logging, opens the shared client, chooses `JsonFileCacheStore` or `NullCacheStore`, and builds
  the workflow.
- **`load_dotenv()` runs first, before `src.config` is imported.** pydantic-settings reads
  `.env` into a `Settings` object but does **not** export to `os.environ`, and
  `ai/providers/factory.py` reads `os.getenv`. Without this line the application silently falls
  back to a different provider and fails. `src.config` is imported afterwards because its
  settings object is built at import time.
- **Source aliases are translated here**: `wiki` → `wikipedia`. Untranslated, the two spellings
  would become two cache entries holding identical data, and nothing would error.
- **Failures print a sentence, not a traceback**, and exit codes are meaningful: `0` success,
  `1` failure, `130` interrupted — the convention shells and Docker rely on.

---

## §5 · Concurrency and the benchmark

### What is being compared

`TOPIC.md` asks for one specific comparison: **wall-clock time for fetching the three sources
one after another (the sum of three) against fetching them together (the maximum of three).**

```
sequential:  |--wikipedia--||------arxiv------||---web---|   → sum
concurrent:  |--wikipedia--|                                 → max
             |------arxiv------|
             |---web---|
```

### Method

- **Questions:** all 5 in `data/research_questions.json`
- **Repeats per question:** 3; every figure below is a mean of 3 timed runs
- **Cache:** not involved. Both paths call the fetchers directly, so no run can be
  served from disk. (`--with-llm`, used for the second table, runs against
  `NullCacheStore` for the same reason.)
- **Fairness:** both paths use the same shared `httpx.AsyncClient`, the same per-source
  timeout and the same `fetch_with_retry` wrapper. Only the scheduling differs.
- **Machine:** Linux 7.0.0-31-generic, Python 3.12.3 · **Date:** 18 September 2026
- **Configuration:** per-source timeout 10.0s · web search Tavily · LLM `openai` / `gpt-4o-mini`

Reproduce with `python benchmark.py --repeats 3`.

### Results — sequential vs concurrent fetching

| Question                                 | Sequential (s) | Concurrent (s) |  Speed-up | Slowest source (s) | Excerpts returned           |
| :--------------------------------------- | -------------: | -------------: | --------: | -----------------: | :-------------------------- |
| What is photosynthesis and what are its… |           3.11 |           2.15 |     1.45× |           web 2.39 | wikipedia 0, arxiv 3, web 3 |
| How do transformer-based language model… |           3.11 |           1.16 |     2.68× |           web 2.49 | wikipedia 0, arxiv 3, web 3 |
| What were the main causes of the 2008 f… |           2.39 |           1.47 |     1.62× |           web 1.62 | wikipedia 0, arxiv 3, web 3 |
| What is the current state of fusion ene… |           2.12 |           1.64 |     1.29× |           web 1.23 | wikipedia 0, arxiv 3, web 3 |
| How does CRISPR-Cas9 gene editing work … |           2.87 |           2.43 |     1.18× |           web 1.34 | wikipedia 0, arxiv 3, web 3 |
| **Mean**                                 |       **2.72** |       **1.77** | **1.54×** |                    |                             |

**Concurrent fetching is 1.54× faster on average**, saving about 0.95s of every request.

### Results — end to end, including synthesis

| Question                                 | Total (s) | Citations | Degraded sources |
| :--------------------------------------- | --------: | --------: | :--------------- |
| What is photosynthesis and what are its… |      7.64 |         3 | wikipedia        |
| How do transformer-based language model… |      7.59 |         4 | wikipedia        |
| What were the main causes of the 2008 f… |      6.97 |         3 | wikipedia        |
| What is the current state of fusion ene… |      4.68 |         3 | wikipedia        |
| How does CRISPR-Cas9 gene editing work … |      4.37 |         2 | wikipedia        |
| **Mean**                                 |  **6.25** |           |                  |

### Interpretation

**The speed-up is real but smaller than the naïve prediction, and the reason is the point.**
Three sources fetched together might be expected to take a third of the time. They do not,
because concurrent time is bounded below by the _slowest_ source, not by the average: the
fetch phase can never finish before `web` does. The mean concurrent time (1.77s) sits close
to the mean slowest-source time, which is exactly the behaviour the design predicts.

**The speed-up varies with how uneven the sources are, not with how many there are.** The
transformer question gained 2.68× because one source dominated its sequential total; the
CRISPR question gained only 1.18× because its three sources were already similar in speed.
This is the most useful sentence in the section: parallelism converts _sum_ into _maximum_,
so the benefit is whatever the non-slowest sources were costing.

**The concurrency work exposes a new bottleneck, and it is the LLM.** Of the 6.25s mean
end-to-end time, concurrent fetching accounts for about 1.9s. The remaining ~4.3s is a single
`ai.synthesize` call, which is one request for one question and cannot be split. No further
concurrency work touches it. Concretely: the fetch phase is now a smaller share of the request
than the synthesis it feeds, so future optimisation belongs in prompt size, model choice or
streaming — not in more parallelism.

**Honest caveat about the table.** "Concurrent" and "slowest source" come from two separately
timed runs a few seconds apart, so network variance occasionally makes a concurrent mean look
faster than the slowest individual source measured in the sequential pass (rows 2 and 5). The
comparison between the sequential and concurrent columns is sound — both are wall-clock totals
of the same work — but the per-source column should be read as indicative, not as a component
of the concurrent figure.

### Graceful degradation, demonstrated

A real run. Wikipedia returns nothing (§8, item 1) and the answer is still produced, with the
missing source named to the user rather than silently dropped:

```
$ python -m researcher ask "What is photosynthesis and what are its main stages?"
WARNING  src.core.researcher: Source wikipedia returned nothing.
WARNING  src.core.researcher: Answering '...' without wikipedia.

Q: What is photosynthesis and what are its main stages?

A: Photosynthesis is a vital process that plants, algae, and some bacteria use to
   convert light energy into chemical energy. It consists of two main stages: the
   light-dependent reactions and the light-independent reactions (Calvin cycle) ... [4,5,6]

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

Two details worth noticing. The citation numbers start at `[4]` because numbering follows the
position of each excerpt in the collected list, and the three arXiv excerpts occupied 1–3; the
synthesiser drops the indices it did not use rather than renumbering. And the exit was a
success, not an error — a degraded answer is still an answer.

### How to reproduce

```bash
python benchmark.py --repeats 3          # the table above
python benchmark.py --with-llm           # adds the end-to-end table (paid)
```

`benchmark.py` times both paths through the same shared client, the same per-source timeout
and the same retry wrapper, so the only difference measured is the scheduling. The cache is
deliberately not involved: a cache hit would mean no fetch happened at all.

---

## §6 · Running the project

### Configuration

| Variable                                 | Purpose                       | Our value                                              |
| ---------------------------------------- | ----------------------------- | ------------------------------------------------------ |
| `LLM_PROVIDER` / `LLM_MODEL`             | which model writes the answer | `openai` / `gpt-4o-mini`                               |
| `OPENAI_API_KEY`                         | that provider's key           | _(never committed)_                                    |
| `WEB_SEARCH_PROVIDER` / `TAVILY_API_KEY` | web search                    | `tavily`                                               |
| `PER_SOURCE_TIMEOUT_SECONDS`             | per-source deadline           | `10` _(the code's default is 8; our `.env` raises it)_ |
| `CACHE_DIR` / `CACHE_TTL_SECONDS`        | cache location and lifetime   | `.cache` / `86400`                                     |
| `LOG_LEVEL`                              | logging verbosity             | `INFO`                                                 |

`.env` is listed in `.gitignore` and in `.dockerignore`, and has never been committed on any
branch. A trailing `# comment` after a value in `.env` is captured as part of the value — this
cost us an afternoon to a 401 from Tavily, and is the reason comments sit on their own lines.

### Locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-ai.txt -r requirements.txt
cp .env.example .env          # then fill in the keys
python -m researcher ask "What is photosynthesis?"
python -m researcher ask "..." --sources wiki,arxiv
python -m researcher ask "..." --no-cache
pytest -q                     # offline
```

### In Docker

```bash
docker compose build
docker compose run --rm research-pipeline          # runs the test suite
```

`.dockerignore` exists and excludes `.env`, and it must: with `COPY . .` in the Dockerfile,
without it the real API keys would be baked into an image layer and would survive any later
deletion of the file.

Three defects were found in the container setup while this section was being written, and all
three are fixed:

1. **The image could not start.** It installed only `requirements.txt`, so `numpy`,
   `pytest-asyncio`, `httpx` and `pydantic` — declared in `requirements-ai.txt` — were missing,
   and every entry point died with `ModuleNotFoundError: No module named 'numpy'`. Pinning the
   full dependency set resolved it; the image now runs all 141 tests.
2. **The image ran the tests, not the application**, where `TOPIC.md` asks for a container that
   works end to end. It now has `ENTRYPOINT ["python", "-m", "researcher"]` with a real query as
   its default command, and the suite runs by overriding the entrypoint.
3. **The cache mount pointed at the wrong directory.** Compose mounted `./cache` while the
   application writes to `CACHE_DIR`, which defaults to `.cache`; nothing persisted between
   runs. Both paths now agree.

Verified after the fixes: `docker run --rm --entrypoint pytest <image>` gives 141 passed, and
`docker run --rm --env-file .env <image>` answers a real question with citations, correctly
reporting Wikipedia as unavailable.

---

## §7 · Testing

**141 tests, all offline, about 2.7 seconds, 96% statement coverage of `src/`.**

```bash
pytest -q
pytest --cov=src --cov-report=term-missing
```

| Test file              | Tests | Covers                                                         |
| ---------------------- | ----- | -------------------------------------------------------------- |
| `test_ai_service.py`   | 41    | retries, backoff, classification of transient errors           |
| `test_cache_store.py`  | 29    | canonicalisation, TTL, atomicity, corruption, `NullCacheStore` |
| `test_cli.py`          | 26    | argument parsing, aliases, rendering, exit codes               |
| `test_researcher.py`   | 20    | cache-aside flow, degradation, response assembly               |
| `test_ai_smoke.py`     | 16    | the supplied tests, unmodified                                 |
| `test_fetchers.py`     | 5     | the fetchers, mocked with `respx`                              |
| `test_orchestrator.py` | 3     | concurrency, timeouts, `return_exceptions`                     |
| `test_http_client.py`  | 1     | client construction                                            |

### How the suite stays offline

Four substitutions, each replacing a slow or paid dependency with something deterministic:

| Real thing     | Replaced by                       | What it buys                                 |
| -------------- | --------------------------------- | -------------------------------------------- |
| the clock      | a `FakeClock` the test advances   | TTL expiry tested in microseconds, not hours |
| the filesystem | pytest's `tmp_path`               | every test starts with an empty cache        |
| HTTP           | `respx` and `httpx.MockTransport` | scripted responses, including 429 and 500    |
| the LLM        | `FakeLLM` from `conftest.py`      | no API key, no cost, deterministic answers   |

`asyncio.sleep` is also injected into the retry loop, so a test can assert that the backoff
grows without waiting for it.

### Two tests that earned their place

Coverage counts lines; these caught defects.

**A test that spent money.** `test_missing_orchestrator_reports_a_sentence_not_a_traceback`
assumed the orchestrator did not exist yet. Once it did, the test ran the entire live
application on every `pytest` invocation: real Wikipedia, arXiv and Tavily calls, a paid LLM
call, and arXiv rate-limiting us in return. It now makes the import fail deterministically with
`monkeypatch.setitem(sys.modules, "src.concurrency.orchestrator", None)`. The lesson recorded
here honestly: a passing test suite is not by itself evidence that the suite is offline. We
verified it separately by patching `socket.socket.connect` to raise.

**A test that only passed on one operating system.** The cache's "unwritable directory" test
made a real read-only folder with `chmod(0o500)`. Windows ignores directory permission bits and
`root` bypasses them, so the write succeeded and the assertion failed for a teammate — and would
also have failed inside Docker, which runs as root by default. It now simulates the failure with
`monkeypatch`, and we confirmed the fix by breaking the cache deliberately and checking the test
still failed.

---

## §8 · Limitations and known issues

Written to be volunteered rather than discovered.

**1. Wikipedia returns nothing for question-shaped queries.** `ai/sources.py` uses
`action=opensearch`, which matches the _beginning of an article title_. `"Photosynthesis"`
returns three articles; `"What is photosynthesis?"` returns none. Verified against the live API:
the response is HTTP **200** with an empty list, so this is not rate limiting and not a rejected
client — adding a contact address to the `User-Agent` does not change it. The fix is Wikipedia's
full-text `list=search` endpoint, which lives inside `ai/` and is therefore outside our contract;
per `TOPIC.md` the route is to report it rather than patch it. Filed as issue #15 on the team
repository with the reproduction above; the instructor still needs to be notified directly.
Graceful degradation reports Wikipedia as unavailable and
the answer is produced from the other two sources — which is, at least, an honest demonstration
of the degradation path.

**2. Wikipedia returns zero excerpts on every benchmark question** — visible in the §5 table's
last column. This is item 1 above, measured rather than asserted.

**3. Fixed during review: `ResearchRequest.bypass_cache` was never read.** `cli.py` set the
field but `researcher.ask()` ignored it, so anything relying on it silently received a cached
answer — including the first version of `benchmark.py`, whose "cold" runs were not cold after
the first question. The workflow now honours the flag (skipping the read, still refreshing the
write) and two tests cover it. Recorded here because the class of bug is worth naming: a field
that is written but never read fails silently, and no test will notice until something depends
on it.

**4. No timeout on synthesis.** Fetches have an 8-second per-source deadline; the LLM call has
none. It runs on a worker thread, and Python threads cannot be cancelled, so a timeout would
free the caller while the work continued in the background. A provider that hangs hangs the run.

**5. Two identical questions asked at the same moment both fetch.** Nothing coordinates
concurrent misses. Each write is atomic, so the cache never corrupts — the cost is one wasted
fetch, not a wrong answer.

**6. Changing the TTL does not shorten entries already saved.** `expires_at` is absolute and
written at save time. The escape hatches are `--no-cache` and deleting `.cache/`.

**7. Refusing to cache empty results is our decision, not a requirement.** An empty list usually
means the fetch failed, and storing it would serve that failure for the whole TTL. The cost is
that a genuinely empty result is re-fetched every time.

**8. The Protocol between workflow and orchestrator is not enforced at runtime.** An object of
the wrong shape fails when it is called, not when it is passed in.

**9. Docker runs the tests, not the application** — see the three items flagged in §6.

**10. `.env.example` does not match what we run.** It defaults to `LLM_PROVIDER=gemini` and
`PER_SOURCE_TIMEOUT_SECONDS=10`, while the team runs OpenAI with an 8-second timeout, and it
lists `MAX_SOURCES_PER_QUERY`, which no code reads. Anyone following it gets a configuration we
have never tested.

---

## §9 · Process and teamwork

Work was divided by file, with each member owning their modules end to end, including their
tests. Interfaces between members were agreed as signatures before implementation, which is what
allowed `researcher.py` and `orchestrator.py` to be written in parallel by different people and
still fit together on the first run.

Everything went through a branch and a pull request — branch names prefixed with the author's
name, one concern per PR, kept small enough to read. Fourteen pull requests and one issue were
opened over twelve days; `main` was never committed to directly after the first week.

What worked, judged by what it caught rather than by how it felt: reviewing across members found
defects their author could not have seen alone. A permissions test that passed on Linux failed on
a teammate's Windows machine, and would also have failed in Docker, which runs as root. A
benchmark written against a request field discovered that field had never been read by anything.
A dependency freeze made on one machine silently removed the LLM provider every measurement in
this report depends on. None of these break a test; all three were found by someone other than
the author reading the change.

What we would change, stated honestly:

- **Several early pull requests were merged by their own author without review.** The habit only
  changed partway through the project. A visible review history is evidence for a grader, and we
  gave up some of it.
- **Two files were built twice** because ownership was assumed from a document rather than
  confirmed with the person who would otherwise own it. The duplicate work was avoidable.
- **Timings were added late.** `TOPIC.md` advises logging per-source timings from the start; the
  benchmark arrived near the end, which is why §5 was the last section that could be written.

---

## §10 · AI tool disclosure

| Module / file                                                                                 | Assistant                           | What we did with the output                                                                                                                                                                                                                                 |
| --------------------------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/storage/cache_store.py`                                                                  | Claude (Claude Code)                | Design agreed in discussion first — abstract base class, one file per entry, canonicalisation rules — then drafted from that design and reviewed decision by decision. The trailing-only punctuation rule and the `\x1f` separator came out of that review. |
| `src/services/ai_service.py`                                                                  | Claude (Claude Code)                | Retry loop drafted from an agreed design. The factory-instead-of-coroutine fix and the `Exception` vs `BaseException` distinction were worked through explicitly before the code was accepted.                                                              |
| `src/core/researcher.py`, `src/cli.py`                                                        | Claude (Claude Code)                | Same approach: design agreed, draft generated, reviewed line by line. The `load_dotenv()` ordering was established by testing it both ways rather than by assumption.                                                                                       |
| `tests/test_cache_store.py`, `test_ai_service.py`, `test_researcher.py`, `test_cli.py`        | Claude (Claude Code)                | Test cases proposed and reviewed. Two defects were found this way: a test that reached the live network and made a paid LLM call on every run, and a permissions test that passed only on Linux as a non-root user.                                         |
| `benchmark.py`                                                                                | Gemini                              | Generated the initial benchmark runner; rate-limit backoff and the inter-query delays were added afterwards. _(As declared in `CONTRIBUTION_STATEMENT.md`.)_                                                                                                |
| `src/config.py`, `src/models.py`, `src/concurrency/orchestrator.py`, `Dockerfile`             | ChatGPT (GPT-4o), GitHub Copilot    | Consulted on Pydantic `BaseSettings` syntax, designed the `asyncio.gather(..., return_exceptions=True)` pattern with per-source `asyncio.wait_for` deadlines, and resolved Docker dependency layer issues.                                                  |
| `src/services/cache.py`, `src/services/http_client.py`, `tests/test_fetchers.py`, `README.md` | ChatGPT (GPT-4o), Claude 3.5 Sonnet | Used for structuring multi-key batch logic in `CacheService`, configuring `httpx.AsyncClient` redirect and `User-Agent` settings, drafting `respx` mock tests, and polishing setup documentation.                                                           |

_This table and the one in `CONTRIBUTION_STATEMENT.md` are the same declaration and must agree
before submission. The statement is the signed copy._

We affirm that we can defend every line of code in this repository during the oral defence.
"The AI wrote it" is not an answer we will use.

---

## Appendix · Reproducing the figures in this report

| Figure           | Command                                        |
| ---------------- | ---------------------------------------------- |
| 141 tests, ~2.7s | `pytest -q`                                    |
| 96% coverage     | `pytest --cov=src --cov-report=term`           |
| 16 smoke tests   | `pytest tests/test_ai_smoke.py -q`             |
| Commit shares    | `git shortlog -sn main`                        |
| Benchmark table  | `python benchmark.py` _(see §5 and §8 item 2)_ |
