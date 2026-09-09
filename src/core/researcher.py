"""Business logic: turn a research question into an answer with citations.

Coordinates the three collaborators without doing any of their jobs itself —
the cache stores, the orchestrator fetches, the AI service synthesises. All
three arrive through the constructor, so this class can be tested with fakes
and never touches the network.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

from ai.providers.base import LLMProvider
from ai.schemas import Source
from src.models import CitationModel, ResearchRequest, ResearchResponse
from src.services.ai_service import DEFAULT_MAX_ATTEMPTS, synthesize_with_retry
from src.storage.cache_store import CacheStore

logger = logging.getLogger(__name__)


class SourceOrchestrator(Protocol):
    """What this module needs from the concurrency layer.

    Declared structurally so nothing here imports the orchestrator module: any
    object with a matching `fetch_all` satisfies it, including a test fake.
    """

    async def fetch_all(
        self, question: str, sources: list[str]
    ) -> dict[str, list[Source] | Exception]:
        ...


class AsyncResearchAssistant:
    """Answers a research question, reusing cached sources where possible."""

    def __init__(
        self,
        orchestrator: SourceOrchestrator,
        cache: CacheStore,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        llm: LLMProvider | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._cache = cache
        self._max_attempts = max_attempts
        self._llm = llm

    async def ask(self, request: ResearchRequest) -> ResearchResponse:
        """Fetch, synthesise and return an answer.

        Raises
        ------
        ValueError
            No source returned anything, so there is nothing to synthesise from.
        ProviderError
            Synthesis failed on every attempt.
        """
        started = time.perf_counter()
        question = request.question
        # dict.fromkeys de-duplicates while preserving the requested order.
        requested = list(dict.fromkeys(request.sources_filter))

        cached = await self._read_cache(requested, question)
        misses = [source for source in requested if source not in cached]

        fetched, degraded = await self._fetch(question, misses)

        collected: list[Source] = []
        for source in requested:
            collected.extend(cached.get(source) or fetched.get(source) or [])

        if not collected:
            raise ValueError(
                "no sources could be retrieved for this question "
                f"(tried: {', '.join(requested) or 'none'})"
            )

        if degraded:
            logger.warning(
                "Answering %r without %s.", question, ", ".join(degraded)
            )

        answer = await synthesize_with_retry(
            question,
            collected,
            llm=self._llm,
            max_attempts=self._max_attempts,
        )

        elapsed = time.perf_counter() - started
        logger.info(
            "Answered %r in %.2fs from %d source(s), %d cached, %d degraded.",
            question,
            elapsed,
            len(collected),
            len(cached),
            len(degraded),
        )

        return ResearchResponse(
            question=question,
            answer=answer.answer,
            citations=[
                CitationModel(
                    index=citation.index,
                    title=citation.source.title,
                    url=citation.source.url,
                    origin=citation.source.origin,
                )
                for citation in answer.citations
            ],
            wall_clock_time_seconds=elapsed,
            degraded_sources=degraded,
        )

    async def _read_cache(
        self, sources: list[str], question: str
    ) -> dict[str, list[Source]]:
        """Look every source up at once; the cache never raises, so no guard."""
        hits = await asyncio.gather(
            *(self._cache.get(source, question) for source in sources)
        )
        return {source: hit for source, hit in zip(sources, hits) if hit}

    async def _fetch(
        self, question: str, sources: list[str]
    ) -> tuple[dict[str, list[Source]], list[str]]:
        """Fetch the cache misses, returning results and the sources that failed."""
        if not sources:
            return {}, []

        results = await self._orchestrator.fetch_all(question, sources)

        fetched: dict[str, list[Source]] = {}
        degraded: list[str] = []
        for source in sources:
            outcome = results.get(source)
            if isinstance(outcome, Exception):
                logger.warning("Source %s failed: %s", source, outcome)
                degraded.append(source)
            elif not outcome:
                # No results is a degraded outcome too, not a silent success.
                logger.warning("Source %s returned nothing.", source)
                degraded.append(source)
            else:
                fetched[source] = outcome

        if fetched:
            await asyncio.gather(
                *(
                    self._cache.set(source, question, results_for)
                    for source, results_for in fetched.items()
                )
            )
        return fetched, degraded
