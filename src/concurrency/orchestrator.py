"""Concurrent orchestration layer for fetching research sources in parallel.

Uses asyncio.gather and per-source timeouts/semaphores to safely query multiple
data sources without blocking or raising uncaught exceptions.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Callable

import httpx
from ai.providers.base import ProviderError
from ai.schemas import Source
from ai.sources import fetch_arxiv, fetch_web, fetch_wikipedia

logger = logging.getLogger(__name__)

# Map normalized full names to the uneditable ai.sources functions
SOURCE_FETCHER_MAP: dict[str, Callable[..., list[Source]]] = {
    "wikipedia": fetch_wikipedia,
    "arxiv": fetch_arxiv,
    "web": fetch_web,
}


class ConcurrentOrchestrator:
    """Orchestrates asynchronous, parallel fetches from multiple sources."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout: float = 8.0,
        max_concurrent_fetches: int = 5,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrent_fetches)

    async def fetch_all(
        self, question: str, sources: list[str]
    ) -> dict[str, list[Source] | Exception]:
        """Fetch all requested sources concurrently.

        Guaranteed NEVER to raise an exception. Returns a dictionary mapping
        source names to their list[Source] results or Exception objects.
        """
        # Deduplicate while preserving order
        unique_sources = list(dict.fromkeys(sources))

        tasks = [
            self._fetch_single_source(source, question)
            for source in unique_sources
        ]

        # return_exceptions=True prevents gather from raising if one task fails
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, list[Source] | Exception] = {}
        for source, result in zip(unique_sources, results):
            output[source] = result

        return output

    async def _fetch_single_source(
        self, source: str, question: str
    ) -> list[Source]:
        """Fetch a single source with rate limiting, timeouts, and error handling."""
        fetcher = SOURCE_FETCHER_MAP.get(source.lower())
        if not fetcher:
            raise ValueError(f"Unknown source provider: {source!r}")

        async with self._semaphore:
            try:
                # Handle both async coroutines and sync functions cleanly
                if inspect.iscoroutinefunction(fetcher):
                    coro = fetcher(question, client=self._client)
                else:
                    coro = asyncio.to_thread(fetcher, question, client=self._client)

                return await asyncio.wait_for(coro, timeout=self._timeout)

            except asyncio.TimeoutError as exc:
                logger.warning(
                    "Fetch timed out for source %s after %.1fs",
                    source,
                    self._timeout,
                )
                raise TimeoutError(
                    f"Source '{source}' timed out after {self._timeout}s"
                ) from exc
            except ProviderError as exc:
                logger.warning("Provider error for source %s: %s", source, exc)
                raise
            except Exception as exc:
                logger.error(
                    "Unexpected error fetching source %s: %s",
                    source,
                    exc,
                    exc_info=True,
                )
                raise