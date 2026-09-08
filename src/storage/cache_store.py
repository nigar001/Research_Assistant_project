"""TTL cache for source results, keyed by (source, canonicalised query).

Fetching Wikipedia, arXiv and the web costs seconds per question. Asking the
same question twice should cost nothing, so results are stored on disk until
they expire.

A cache is an optimisation, never a dependency: every failure in here is
logged and reported as a miss, so the worst case is a re-fetch.
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path

from ai.schemas import Source

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")

# Unicode "unit separator" — cannot occur in a source name or a typed question,
# so ("wiki_a", "b") can never collide with ("wiki", "a_b").
_KEY_SEPARATOR = "\x1f"

_TRAILING_PUNCTUATION = "?!."


def canonicalise_query(query: str) -> str:
    """Reduce a question to the form used as its cache key.

    "WHAT IS PHOTOSYNTHESIS?" and "what is photosynthesis" must share one entry.
    """
    normalised = unicodedata.normalize("NFKC", query)
    collapsed = _WHITESPACE.sub(" ", normalised).strip()
    # Trailing only: stripping punctuation everywhere would fold "C++" into "C".
    return collapsed.casefold().rstrip(_TRAILING_PUNCTUATION).strip()


class CacheStore(abc.ABC):
    """The contract every cache backend fulfils.

    Callers depend on this, never on a concrete backend, so a different store
    can be substituted without touching them.

    Async because a future database-backed store would be async-only; a
    synchronous contract could not be fulfilled by one.
    """

    @abc.abstractmethod
    async def get(self, source: str, query: str) -> list[Source] | None:
        """Return the cached sources, or None on a miss or expired entry."""

    @abc.abstractmethod
    async def set(self, source: str, query: str, sources: list[Source]) -> None:
        """Store sources under (source, query). Never raises."""


class JsonFileCacheStore(CacheStore):
    """Filesystem backend storing one JSON file per entry.

    One file per entry rather than one shared index: the three fetchers finish
    at roughly the same moment, and concurrent read-modify-write against a
    shared file would let the last writer erase the others' entries.
    """

    def __init__(
        self,
        cache_dir: str | Path,
        ttl_seconds: float,
        *,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn

    def _entry_path(self, source: str, canonical: str) -> Path:
        # Questions contain "/", "?" and unicode, and filenames cap at ~255
        # bytes; a hash is a fixed-length, filesystem-safe name.
        key = f"{source}{_KEY_SEPARATOR}{canonical}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._cache_dir / source / f"{digest}.json"

    async def get(self, source: str, query: str) -> list[Source] | None:
        canonical = canonicalise_query(query)
        path = self._entry_path(source, canonical)
        try:
            return await asyncio.to_thread(self._read_entry, path, source, canonical)
        except Exception:
            logger.warning(
                "Cache read failed for %s/%r; treating as a miss.",
                source,
                canonical,
                exc_info=True,
            )
            return None

    async def set(self, source: str, query: str, sources: list[Source]) -> None:
        if not sources:
            # An empty result means the fetch failed or found nothing. Storing
            # it would serve that failure for the whole TTL.
            logger.debug("Not caching an empty result for %s/%r.", source, query)
            return

        canonical = canonicalise_query(query)
        now = self._time_fn()
        envelope = {
            "source": source,
            "query": query,
            "canonical_query": canonical,
            "stored_at": now,
            "expires_at": now + self._ttl_seconds,
            "sources": [s.model_dump() for s in sources],
        }
        path = self._entry_path(source, canonical)
        try:
            await asyncio.to_thread(self._write_entry, path, envelope)
        except Exception:
            logger.warning(
                "Cache write failed for %s/%r; continuing uncached.",
                source,
                canonical,
                exc_info=True,
            )

    def _read_entry(
        self, path: Path, source: str, canonical: str
    ) -> list[Source] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

        envelope = json.loads(raw)

        # The filename is a hash, so this confirms the entry is really the one
        # asked for rather than trusting the digest alone.
        if (
            envelope.get("source") != source
            or envelope.get("canonical_query") != canonical
        ):
            logger.warning("Cache entry at %s does not match its key; discarding.", path)
            self._discard(path)
            return None

        if self._time_fn() >= envelope["expires_at"]:
            # Expiry is lazy: deleting on read costs nothing extra and keeps
            # the directory from growing without a cleanup job.
            self._discard(path)
            return None

        return [Source.model_validate(item) for item in envelope["sources"]]

    def _write_entry(self, path: Path, envelope: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to a unique temporary name in the same directory, then rename.
        # os.replace is atomic within a filesystem, so a crash mid-write can
        # never leave a half-written file that breaks every later read.
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(envelope), encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stale cache entry %s.", path, exc_info=True)


class NullCacheStore(CacheStore):
    """A store that remembers nothing — the whole of `--no-cache`.

    Substituting this at startup makes every lookup a miss, so no code below
    the CLI needs a "is caching enabled?" branch.
    """

    async def get(self, source: str, query: str) -> list[Source] | None:
        return None

    async def set(self, source: str, query: str, sources: list[Source]) -> None:
        return None
