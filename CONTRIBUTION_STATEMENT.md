# Contribution Statement

**Team:** _Researchers_  
**Topic:** _[Topic 4 — Research Assistant]_  
**Repository:** _[https://github.com/nigar001/Research_Assistant_project](https://github.com/nigar001/Research_Assistant_project)_  
**Final tag:** `v1.0-final`  
**Submission date:** 2026-09-19

> **Note on git authorship — Member B.** `git shortlog -sn` lists Member B under two names,
> `faridqul <faridlvrl@gmail.com>` and `yaponski <faridqul222@gmail.com>`. These are the same
> person: commits made through GitHub carry the first identity, commits made from the local
> machine carried the second before it was corrected. The two together are Member B's 14
> commits. The repository's `.mailmap` maps them to one author, so `git shortlog -sn` reports
> the combined total.

---

## Member A — Nigar Alimammadova (`@nigar001`)

**Owned (sole author of these files / PRs):**

- `src/config.py`
- `src/models.py`
- `src/concurrency/orchestrator.py`
- `Dockerfile`
- `.dockerignore`
- `docker-compose.yml`
- `benchmark.py`
- `tests/test_orchestrator.py`
- PRs: none opened; this work landed as direct commits to `main` (see §9 of the report)

**Co-owned (paired or substantially edited):**

- `src/cli.py`
- `.gitignore`

**Reviewed (PRs reviewed and merged):**

- PRs: #6, #7, #8, #9, #10, #11, #12

**Approximate share of commits:** 38%

---

## Member B — Farid Dilaverli (`@faridqul`)

**Owned:**

- `src/storage/cache_store.py` — TTL cache: `CacheStore` abstract base class,
  `JsonFileCacheStore` (canonicalised `(source, query)` keys, SHA-256 filenames, atomic
  writes, lazy expiry), and `NullCacheStore`, which implements `--no-cache`
- `src/services/ai_service.py` — retries with exponential backoff and jitter for both
  `ai.synthesize` and every `ai.fetch_*` call, through one shared retry loop; input
  validation and logging. Runs synthesis on a worker thread so the event loop is never
  blocked
- `src/core/researcher.py` — `AsyncResearchAssistant.ask()`: cache-aside flow, graceful
  degradation when a source fails, timing, response assembly
- `src/cli.py` and `researcher/` — the `ask` command, `--sources`, `--no-cache`, reference
  rendering, exit codes, `load_dotenv()`, logging setup
- `tests/test_cache_store.py` (29), `tests/test_ai_service.py` (41),
  `tests/test_researcher.py` (20), `tests/test_cli.py` (26) — 116 of the repository's 141
  tests, all offline
- `requirements.txt`
- PRs: #1, #2, #3, #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14

**Approximate share of commits:** 45%

---

## Member C — Sahib Aliyev (`@SahibAliyev5`)

**Owned:**

- `src/services/cache.py`
- Shared `httpx.AsyncClient` setup (with `follow_redirects` & custom `User-Agent`)
- `respx` offline test suite
- `README.md`
- `.env.example`
- PRs: none opened; this work landed as direct commits to `main` (see §9 of the report)

**Co-owned:**

- `src/cli.py` (HTTP client integration)

**Reviewed:**

- PRs: none

**Approximate share of commits:** 16%

---

## AI tool disclosure (also in §10 of the report)

| Module / file                | Assistant       | What we did with it                                                                                                   |
| :--------------------------- | :-------------- | :-------------------------------------------------------------------------------------------------------------------- |
| `benchmark.py`               | Gemini          | Generated initial benchmark runner; added 429 rate-limit backoff handling and inter-query delays.                     |
| `src/services/ai_service.py` | Cursor / Claude | Drafted initial exponential backoff logic for synthesis retries; team refined exception handling for provider errors. |
| `tests/`                     | Claude          | Suggested unit test cases and mock structures for offline execution; team reviewed and expanded to 141 passing tests. |

We affirm that we **can defend every line of code** in this repository during the oral defense. "The AI wrote it" is not an answer we will use.

---

## Signatures

By signing below, we affirm that:

- The contributions described above are accurate.
- The commit percentages reflect actual work, not artificially split commits.
- Every line of code in the repository can be defended by at least one team member.
- AI assistant usage has been disclosed as described above.

| Member             | Signature       | Date       |
| :----------------- | :-------------- | :--------- |
| Nigar Alimammadova | `@nigar001`     | 2026-09-18 |
| Farid Dilaverli    | `@faridqul`     | 2026-09-18 |
| Sahib Aliyev       | `@SahibAliyev5` | 2026-09-18 |
