"""Tests for the TTL cache. No network, no mocking — a temp dir and a fake clock."""

from __future__ import annotations

import json

import pytest

from ai.schemas import Source
from src.storage.cache_store import (
    JsonFileCacheStore,
    NullCacheStore,
    canonicalise_query,
)


class FakeClock:
    """A clock the test controls, so TTL expiry needs no real waiting."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path, clock) -> JsonFileCacheStore:
    return JsonFileCacheStore(tmp_path / "cache", ttl_seconds=3600, time_fn=clock)


@pytest.fixture
def wiki_sources() -> list[Source]:
    return [
        Source(
            title="Photosynthesis",
            url="https://en.wikipedia.org/wiki/Photosynthesis",
            snippet="Plants convert light energy into chemical energy.",
            origin="wikipedia",
        )
    ]


# --- canonicalisation ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "what is photosynthesis",
        "What Is Photosynthesis",
        "WHAT IS PHOTOSYNTHESIS?",
        "  what   is  photosynthesis  ",
        "what is photosynthesis!!",
        "what is photosynthesis.",
    ],
)
def test_equivalent_questions_share_one_key(raw):
    assert canonicalise_query(raw) == "what is photosynthesis"


def test_only_trailing_punctuation_is_stripped():
    # Stripping punctuation everywhere would fold "C++" into "C".
    assert canonicalise_query("What is C++?") != canonicalise_query("What is C?")
    assert canonicalise_query("What is C++?") == "what is c++"


def test_unicode_spellings_normalise_to_one_key():
    # "é" as a single codepoint vs. "e" + combining accent.
    assert canonicalise_query("café") == canonicalise_query("café")


def test_distinct_questions_keep_distinct_keys():
    assert canonicalise_query("what is arxiv") != canonicalise_query("what is wikipedia")


# --- basic get / set ----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_none_when_nothing_stored(store):
    assert await store.get("wikipedia", "what is photosynthesis") is None


@pytest.mark.asyncio
async def test_stored_sources_come_back_unchanged(store, wiki_sources):
    await store.set("wikipedia", "what is photosynthesis", wiki_sources)
    assert await store.get("wikipedia", "what is photosynthesis") == wiki_sources


@pytest.mark.asyncio
async def test_differently_spelled_question_hits_the_same_entry(store, wiki_sources):
    await store.set("wikipedia", "WHAT IS PHOTOSYNTHESIS?", wiki_sources)
    assert await store.get("wikipedia", "what is photosynthesis") == wiki_sources


@pytest.mark.asyncio
async def test_same_question_is_cached_per_source(store, wiki_sources):
    await store.set("wikipedia", "what is photosynthesis", wiki_sources)
    assert await store.get("arxiv", "what is photosynthesis") is None


@pytest.mark.asyncio
async def test_empty_results_are_not_cached(store, tmp_path):
    await store.set("web", "an unanswerable question", [])
    assert await store.get("web", "an unanswerable question") is None
    assert list((tmp_path / "cache").rglob("*.json")) == []


# --- TTL ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_survives_until_the_ttl_elapses(store, clock, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    clock.advance(3599)
    assert await store.get("wikipedia", "q") == wiki_sources


@pytest.mark.asyncio
async def test_entry_expires_once_the_ttl_elapses(store, clock, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    clock.advance(3601)
    assert await store.get("wikipedia", "q") is None


@pytest.mark.asyncio
async def test_expired_entry_is_deleted_on_read(store, clock, tmp_path, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    clock.advance(3601)
    await store.get("wikipedia", "q")
    assert list((tmp_path / "cache").rglob("*.json")) == []


# --- robustness: a cache must never be the reason the app fails ---------------


@pytest.mark.asyncio
async def test_corrupt_file_is_a_miss_not_a_crash(store, tmp_path, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    entry = next((tmp_path / "cache").rglob("*.json"))
    entry.write_text("{not valid json", encoding="utf-8")
    assert await store.get("wikipedia", "q") is None


@pytest.mark.asyncio
async def test_entry_with_missing_fields_is_a_miss(store, tmp_path, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    entry = next((tmp_path / "cache").rglob("*.json"))
    entry.write_text(json.dumps({"source": "wikipedia"}), encoding="utf-8")
    assert await store.get("wikipedia", "q") is None


@pytest.mark.asyncio
async def test_entry_holding_an_invalid_source_is_a_miss(store, tmp_path, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    entry = next((tmp_path / "cache").rglob("*.json"))
    envelope = json.loads(entry.read_text(encoding="utf-8"))
    envelope["sources"][0]["origin"] = "not-a-real-origin"
    entry.write_text(json.dumps(envelope), encoding="utf-8")
    assert await store.get("wikipedia", "q") is None


@pytest.mark.asyncio
async def test_entry_that_does_not_match_its_key_is_discarded(store, tmp_path, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    entry = next((tmp_path / "cache").rglob("*.json"))
    envelope = json.loads(entry.read_text(encoding="utf-8"))
    envelope["canonical_query"] = "a completely different question"
    entry.write_text(json.dumps(envelope), encoding="utf-8")
    assert await store.get("wikipedia", "q") is None
    assert not entry.exists()


# --- storage details ----------------------------------------------------------


@pytest.mark.asyncio
async def test_write_leaves_no_temporary_files(store, tmp_path, wiki_sources):
    await store.set("wikipedia", "q", wiki_sources)
    assert list((tmp_path / "cache").rglob("*.tmp")) == []


@pytest.mark.asyncio
async def test_entry_is_filed_under_its_source(store, tmp_path, wiki_sources):
    await store.set("arxiv", "q", wiki_sources)
    assert list((tmp_path / "cache" / "arxiv").glob("*.json"))


@pytest.mark.asyncio
async def test_envelope_records_the_original_question_for_debugging(store, tmp_path, wiki_sources):
    await store.set("wikipedia", "WHAT IS PHOTOSYNTHESIS?", wiki_sources)
    entry = next((tmp_path / "cache").rglob("*.json"))
    envelope = json.loads(entry.read_text(encoding="utf-8"))
    assert envelope["query"] == "WHAT IS PHOTOSYNTHESIS?"
    assert envelope["canonical_query"] == "what is photosynthesis"


@pytest.mark.asyncio
async def test_awkward_questions_produce_usable_filenames(store, wiki_sources):
    awkward = "What is 100%/50% of ../../etc/passwd? " + "x" * 500
    await store.set("web", awkward, wiki_sources)
    assert await store.get("web", awkward) == wiki_sources


@pytest.mark.asyncio
async def test_rewriting_a_key_replaces_the_previous_entry(store, tmp_path, wiki_sources):
    replacement = [
        Source(title="Newer", url="https://example.com/newer", snippet="s", origin="wikipedia")
    ]
    await store.set("wikipedia", "q", wiki_sources)
    await store.set("wikipedia", "q", replacement)
    assert await store.get("wikipedia", "q") == replacement
    assert len(list((tmp_path / "cache").rglob("*.json"))) == 1


# --- NullCacheStore -----------------------------------------------------------


@pytest.mark.asyncio
async def test_null_store_never_returns_anything(wiki_sources):
    null = NullCacheStore()
    await null.set("wikipedia", "q", wiki_sources)
    assert await null.get("wikipedia", "q") is None


@pytest.mark.asyncio
async def test_null_store_writes_nothing_to_disk(tmp_path, wiki_sources):
    null = NullCacheStore()
    await null.set("wikipedia", "q", wiki_sources)
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_unwritable_cache_dir_degrades_instead_of_raising(tmp_path, clock, wiki_sources):
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        store = JsonFileCacheStore(readonly / "cache", ttl_seconds=3600, time_fn=clock)
        await store.set("wikipedia", "q", wiki_sources)
        assert await store.get("wikipedia", "q") is None
    finally:
        readonly.chmod(0o700)
