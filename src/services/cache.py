import asyncio
import logging
from typing import Dict, List

from ai.schemas import Source
from src.storage.cache_store import CacheStore

logger = logging.getLogger(__name__)


class CacheService:
    """
    High-level service for concurrent cache operations.
    Wraps the underlying CacheStore backend (JSON/Null) so the core 
    researcher logic doesn't have to manage asyncio orchestration for caching.
    """
    def __init__(self, store: CacheStore) -> None:
        self._store = store

    async def get_cached_sources(self, question: str, sources: List[str]) -> Dict[str, List[Source]]:
        """Look up multiple sources concurrently, returning only the cache hits."""
        if not sources:
            return {}

        hits = await asyncio.gather(
            *(self._store.get(source, question) for source in sources)
        )
        
        return {source: hit for source, hit in zip(sources, hits) if hit}

    async def save_fetched_sources(self, question: str, fetched: Dict[str, List[Source]]) -> None:
        """Save successfully fetched sources concurrently to the underlying store."""
        if not fetched:
            return

        await asyncio.gather(
            *(
                self._store.set(source, question, results)
                for source, results in fetched.items()
            )
        )