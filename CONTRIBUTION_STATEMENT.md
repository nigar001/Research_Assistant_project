# Contribution Statement

**Team:** _Researches_
**Topic:** _[Topic 4 — Research Assistant]_
**Repository:** _[https://github.com/nigar001/Research_Assistant_project](https://github.com/nigar001/Research_Assistant_project)_
**Final tag:** `v1.0-final`
**Submission date:** _[YYYY-MM-DD]_

> **Note on git authorship — Member B.** `git shortlog -sn` lists Member B under two names,
> `faridqul <faridlvrl@gmail.com>` and `yaponski <faridqul222@gmail.com>`. These are the same
> person: commits made through GitHub carry the first identity, commits made from the local
> machine carried the second before it was corrected. The two together are Member B's 14
> commits. The repository's `.mailmap` maps them to one author, so `git shortlog -sn` reports
> the combined total.

---

## How to fill this in

This is the single piece of evidence we use to assess **individual contribution** within the team. Rules:

1. Every member writes their own three subsections (Owned, Co-owned, Reviewed).
2. **Be specific.** "Worked on the backend" is not acceptable; "implemented `src/services/ai_service.py` and `src/concurrency/pipeline.py`, owned PRs #4, #7, #11" is.
3. The committed-percentages must add to 100% and approximately match `git shortlog -sn` on the `main` branch.
4. All three members must sign at the bottom. Unsigned submissions are returned ungraded.

If one member contributed less than 10% without a documented reason (illness, emergency), the team loses 5 points automatically per the rubric.

---

## Member A — _[Nigar Alimammadov]_ (`@nigar001`)

**Owned (sole author of these files / PRs):**

- `src/config.py`
- `src/models.py`
- `src/concurrency/orchestrator.py`
- `src/core/researcher.py`
- PRs: #_[list]_

**Co-owned (paired or substantially edited):**

- _[list]_

**Reviewed (PRs reviewed and merged):**

- PRs: #_[list]_

**Approximate share of commits:** _[34]_%

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
  `tests/test_researcher.py` (18), `tests/test_cli.py` (26) — 114 of the repository's 139
  tests, all offline
- `requirements.txt`
- PRs: #1, #2, #3, #4, #5, #6, #7, #8, #9, #10

**Approximate share of commits:** 41%

---

## Member C — _[Sahib Aliyev]_ (`@github-handle`)

_Slice: shared `httpx.AsyncClient`, per-source `asyncio.timeout()` wrappers, retry/backoff
on the fetchers, the `respx` offline test suite, the Dockerfile._

**Owned:**

- _[list]_

**Co-owned:**

- _[list]_

**Reviewed:**

- _[list]_

**Approximate share of commits:** _[33]_%

---

## AI tool disclosure (also in §10 of the report)

We used AI coding assistants as follows. Each item lists the module, the assistant, and what the team did with the output.

| Module / file                     | Assistant  | What we did with it                                                                                                            |
| --------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------ |
| _[e.g. `src/services/retry.py`]_  | _[Cursor]_ | _[Drafted initial backoff logic; team rewrote the jitter and retry-on-429 branch after observing rate-limit behavior in dev.]_ |
| _[e.g. `tests/test_pipeline.py`]_ | _[Claude]_ | _[Suggested test cases; team reviewed each, kept 4 of 6, hand-wrote 2 more.]_                                                  |
| _[...]_                           | _[...]_    | _[...]_                                                                                                                        |

We affirm that we **can defend every line of code** in this repository during the oral defense. "The AI wrote it" is not an answer we will use.

---

## Signatures

By signing below, we affirm that:

- The contributions described above are accurate.
- The commit percentages reflect actual work, not artificially split commits.
- Every line of code in the repository can be defended by at least one team member.
- AI assistant usage has been disclosed as described above.

| Member          | Signature                                    | Date             |
| --------------- | -------------------------------------------- | ---------------- |
| _[Full Name A]_ | \***\*\*\*\*\*\*\***\_\_\***\*\*\*\*\*\*\*** | \***\*\_\_\*\*** |
| _[Full Name B]_ | \***\*\*\*\*\*\*\***\_\_\***\*\*\*\*\*\*\*** | \***\*\_\_\*\*** |
| _[Full Name C]_ | \***\*\*\*\*\*\*\***\_\_\***\*\*\*\*\*\*\*** | \***\*\_\_\*\*** |
