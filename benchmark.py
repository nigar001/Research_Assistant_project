from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from src.config import settings
from src.concurrency.orchestrator import ConcurrentOrchestrator
from src.core.researcher import AsyncResearchAssistant
from src.models import ResearchRequest
from src.services.http_client import create_shared_client
from src.storage.cache_store import JsonFileCacheStore

load_dotenv()
logging.getLogger().setLevel(logging.ERROR)


@dataclass
class QueryMetric:
    query: str
    cold_latency: float = 0.0
    warm_latency: float = 0.0
    speedup_factor: float = 0.0
    citations_count: int = 0
    degraded_sources: list[str] = field(default_factory=list)


def load_benchmark_questions() -> list[str]:
    json_path = Path("research_questions.json")
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return [q["text"] for q in data.get("questions", []) if "text" in q]
        except Exception:
            pass

    return [
        "What is photosynthesis and what are its main stages?",
        "How do transformer-based language models handle long context windows?",
        "How does CRISPR-Cas9 gene editing work at a molecular level?",
    ]


async def measure_single_query(
    assistant: AsyncResearchAssistant, query: str
) -> QueryMetric:
    metric = QueryMetric(query=query)
    all_sources = ["wikipedia", "arxiv", "web"]

    request_cold = ResearchRequest(
        question=query,
        sources_filter=all_sources,
        bypass_cache=True,
    )
    t0 = time.perf_counter()
    resp_cold = await assistant.ask(request_cold)
    metric.cold_latency = time.perf_counter() - t0
    metric.citations_count = len(resp_cold.citations)
    metric.degraded_sources = resp_cold.degraded_sources

    request_warm = ResearchRequest(
        question=query,
        sources_filter=all_sources,
        bypass_cache=False,
    )
    t1 = time.perf_counter()
    await assistant.ask(request_warm)
    metric.warm_latency = time.perf_counter() - t1

    if metric.warm_latency > 0:
        metric.speedup_factor = metric.cold_latency / metric.warm_latency

    return metric


async def run_benchmark_suite() -> None:
    questions = load_benchmark_questions()

    print("=" * 60)
    print("      RESEARCH PIPELINE BENCHMARK SUITE EXECUTION")
    print("=" * 60)
    print(f"LLM Provider : {settings.llm_provider} ({settings.llm_model})")
    print(f"Search Engine: {settings.web_search_provider}")
    print(f"Timeout Cap  : {settings.per_source_timeout_seconds}s per source")
    print(f"Test Cases   : {len(questions)} queries")
    print("-" * 60)

    cache = JsonFileCacheStore(settings.cache_dir, settings.cache_ttl_seconds)

    async with create_shared_client(settings.per_source_timeout_seconds) as client:
        orchestrator = ConcurrentOrchestrator(
            client=client, timeout=settings.per_source_timeout_seconds
        )
        assistant = AsyncResearchAssistant(
            orchestrator=orchestrator,
            cache=cache,
        )

        results: list[QueryMetric] = []
        for idx, query_text in enumerate(questions, 1):
            print(f"[{idx}/{len(questions)}] Benchmarking: '{query_text}'...")
            metric = await measure_single_query(assistant, query_text)
            results.append(metric)
            await asyncio.sleep(3.0)

    print("\n" + "#" * 60)
    print("      COPY-PASTE REPORT RESULTS (MARKDOWN TABLE)")
    print("#" * 60 + "\n")

    print("### Performance & Latency Benchmark\n")
    print("| Query Target | Cold Latency (s) | Warm Latency (s) | Cache Speedup | Citations | Degraded Sources |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: |")

    avg_cold = sum(m.cold_latency for m in results) / len(results)
    avg_warm = sum(m.warm_latency for m in results) / len(results)

    for m in results:
        degraded_str = ", ".join(m.degraded_sources) if m.degraded_sources else "None"
        query_fmt = f"{m.query[:30]}..." if len(m.query) > 30 else m.query
        print(
            f"| `{query_fmt}` | {m.cold_latency:.2f}s | {m.warm_latency:.4f}s | {m.speedup_factor:.1f}x | {m.citations_count} | {degraded_str} |"
        )

    speedup_avg = (avg_cold / avg_warm) if avg_warm > 0 else 0
    print(f"| **AVERAGE** | **{avg_cold:.2f}s** | **{avg_warm:.4f}s** | **{speedup_avg:.1f}x** | - | - |")
    print()


if __name__ == "__main__":
    asyncio.run(run_benchmark_suite())