"""Sequential-vs-concurrent benchmark for the research pipeline.

`TOPIC.md` asks for the headline comparison: wall-clock time for fetching the
three sources one after another (the sum of three) against fetching them
together (the maximum of three).

Both paths use the same client, the same per-source timeout and the same retry
wrapper, so the only difference being measured is the scheduling. Neither path
touches the cache — a cache hit would mean no fetch happened at all, which is
not what this measures.

The LLM is left out of the default run. Synthesis is one sequential call in both
paths, so it cannot change the comparison, and excluding it makes repeats free.
Pass --with-llm for the end-to-end figures the report also needs.

    python benchmark.py                      # fetch comparison, 3 repeats
    python benchmark.py --repeats 5
    python benchmark.py --with-llm           # adds paid end-to-end runs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import statistics
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src.concurrency.orchestrator import SOURCE_FETCHER_MAP, ConcurrentOrchestrator
from src.config import settings
from src.core.researcher import AsyncResearchAssistant
from src.models import ResearchRequest
from src.services.ai_service import fetch_with_retry
from src.services.http_client import create_shared_client
from src.storage.cache_store import NullCacheStore

ALL_SOURCES = ["wikipedia", "arxiv", "web"]
QUESTIONS_FILE = Path(__file__).parent / "data" / "research_questions.json"
POLITENESS_DELAY_SECONDS = 3.0

logging.basicConfig(level=logging.ERROR)


@dataclass
class FetchTiming:
    """One question, one repeat: how long each way of fetching took."""

    sequential: float = 0.0
    concurrent: float = 0.0
    per_source: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)

    @property
    def speedup(self) -> float:
        return self.sequential / self.concurrent if self.concurrent else 0.0


def load_questions(limit: int | None) -> list[str]:
    """Read the supplied research questions. Fails loudly if they are missing."""
    data = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    questions = [q["text"] for q in data["questions"] if "text" in q]
    if not questions:
        raise SystemExit(f"No questions found in {QUESTIONS_FILE}")
    return questions[:limit] if limit else questions


async def _fetch_one(client, source: str, question: str) -> tuple[float, int | None]:
    """One source, through the same retry wrapper and deadline the app uses.

    Returns the elapsed time and how many excerpts came back, or None if the
    fetch raised. Zero is not the same as None: a source that answers with an
    empty list has not failed, it simply matched nothing.
    """
    started = time.perf_counter()
    try:
        results = await asyncio.wait_for(
            fetch_with_retry(SOURCE_FETCHER_MAP[source], question, client=client),
            timeout=settings.per_source_timeout_seconds,
        )
        return time.perf_counter() - started, len(results)
    except Exception:
        return time.perf_counter() - started, None


async def time_one_question(client, question: str) -> FetchTiming:
    timing = FetchTiming()

    # --- sequential: one source after another ---------------------------------
    started = time.perf_counter()
    for source in ALL_SOURCES:
        elapsed, count = await _fetch_one(client, source, question)
        timing.per_source[source] = elapsed
        if count is None:
            timing.failed.append(source)
        else:
            timing.counts[source] = count
    timing.sequential = time.perf_counter() - started

    await asyncio.sleep(POLITENESS_DELAY_SECONDS)

    # --- concurrent: all three at once, through the real orchestrator ---------
    orchestrator = ConcurrentOrchestrator(
        client=client, timeout=settings.per_source_timeout_seconds
    )
    started = time.perf_counter()
    await orchestrator.fetch_all(question, ALL_SOURCES)
    timing.concurrent = time.perf_counter() - started

    return timing


async def run_fetch_benchmark(questions: list[str], repeats: int) -> dict[str, list[FetchTiming]]:
    results: dict[str, list[FetchTiming]] = {}
    async with create_shared_client(settings.per_source_timeout_seconds) as client:
        for q_index, question in enumerate(questions, 1):
            runs: list[FetchTiming] = []
            for repeat in range(1, repeats + 1):
                print(f"  [{q_index}/{len(questions)}] repeat {repeat}/{repeats}: {question[:48]}…")
                runs.append(await time_one_question(client, question))
                await asyncio.sleep(POLITENESS_DELAY_SECONDS)
            results[question] = runs
    return results


async def run_end_to_end(questions: list[str]) -> list[tuple[str, float, int, list[str]]]:
    """One full run per question, including synthesis. Costs real LLM calls."""
    rows = []
    async with create_shared_client(settings.per_source_timeout_seconds) as client:
        orchestrator = ConcurrentOrchestrator(
            client=client, timeout=settings.per_source_timeout_seconds
        )
        # NullCacheStore, so every run genuinely fetches and synthesises.
        assistant = AsyncResearchAssistant(orchestrator=orchestrator, cache=NullCacheStore())
        for question in questions:
            print(f"  end-to-end: {question[:48]}…")
            started = time.perf_counter()
            try:
                response = await assistant.ask(
                    ResearchRequest(question=question, sources_filter=ALL_SOURCES)
                )
                rows.append(
                    (question, time.perf_counter() - started,
                     len(response.citations), response.degraded_sources)
                )
            except Exception as exc:
                rows.append((question, time.perf_counter() - started, 0, [f"FAILED: {exc}"]))
            await asyncio.sleep(POLITENESS_DELAY_SECONDS)
    return rows


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def print_report(results, repeats: int, e2e_rows) -> None:
    print("\n" + "#" * 74)
    print("  COPY-PASTE INTO THE REPORT (§5)")
    print("#" * 74 + "\n")

    print("**Method.** Each question was fetched twice — once with the three sources")
    print("one after another, once through `ConcurrentOrchestrator` — using the same")
    print("shared HTTP client, the same per-source timeout and the same retry wrapper.")
    print("The cache is not involved: a cache hit would mean no fetch happened.")
    print(f"Repeats per question: {repeats}; figures below are means.\n")

    print(f"- Date: {date.today().isoformat()}")
    print(f"- Machine: {platform.system()} {platform.release()}, Python {platform.python_version()}")
    print(f"- Per-source timeout: {settings.per_source_timeout_seconds}s")
    print(f"- Web search provider: {settings.web_search_provider}")
    print(f"- LLM: {settings.llm_provider} / {settings.llm_model}\n")

    print("### Sequential vs concurrent fetching\n")
    print("| Question | Sequential (s) | Concurrent (s) | Speed-up | Slowest source (s) | Excerpts returned |")
    print("| :--- | ---: | ---: | ---: | ---: | :--- |")

    all_seq, all_con = [], []
    for question, runs in results.items():
        seq, con = mean([r.sequential for r in runs]), mean([r.concurrent for r in runs])
        all_seq.append(seq)
        all_con.append(con)
        slowest = max(
            (mean([r.per_source.get(s, 0.0) for r in runs]), s) for s in ALL_SOURCES
        )
        label = question if len(question) <= 42 else question[:39] + "…"
        returned = []
        for source in ALL_SOURCES:
            if any(source in r.failed for r in runs):
                returned.append(f"{source} failed")
            else:
                returned.append(f"{source} {round(mean([r.counts.get(source, 0) for r in runs]))}")
        print(
            f"| {label} | {seq:.2f} | {con:.2f} | {seq / con if con else 0:.2f}× "
            f"| {slowest[1]} {slowest[0]:.2f} | {', '.join(returned)} |"
        )

    seq_mean, con_mean = mean(all_seq), mean(all_con)
    print(
        f"| **Mean** | **{seq_mean:.2f}** | **{con_mean:.2f}** "
        f"| **{seq_mean / con_mean if con_mean else 0:.2f}×** | | |\n"
    )

    print("_Sequential is the sum of the three fetches; concurrent approaches the")
    print("slowest single source, which is why the speed-up rises with how uneven")
    print("the three sources are and can never exceed the number of sources._\n")

    if e2e_rows:
        print("### End to end, including synthesis\n")
        print("| Question | Total (s) | Citations | Degraded sources |")
        print("| :--- | ---: | ---: | :--- |")
        for question, total, citations, degraded in e2e_rows:
            label = question if len(question) <= 42 else question[:39] + "…"
            print(f"| {label} | {total:.2f} | {citations} | {', '.join(degraded) or 'none'} |")
        e2e_mean = mean([r[1] for r in e2e_rows])
        print(f"| **Mean** | **{e2e_mean:.2f}** | | |\n")
        print(f"_Fetching concurrently accounts for about {con_mean:.2f}s of that mean.")
        print("The remainder is the single LLM synthesis call, which is sequential by")
        print("nature and is the bottleneck the concurrency work exposes._\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3, help="timed runs per question")
    parser.add_argument("--limit", type=int, default=None, help="use only the first N questions")
    parser.add_argument("--with-llm", action="store_true", help="also run end to end (costs API calls)")
    args = parser.parse_args()

    questions = load_questions(args.limit)
    print(f"Benchmarking {len(questions)} question(s), {args.repeats} repeat(s) each.\n")

    results = await run_fetch_benchmark(questions, args.repeats)

    e2e_rows = []
    if args.with_llm:
        print("\nRunning end-to-end (this makes real LLM calls)…")
        e2e_rows = await run_end_to_end(questions)

    print_report(results, args.repeats, e2e_rows)


if __name__ == "__main__":
    asyncio.run(main())
