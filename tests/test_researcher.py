"""Tests for the research workflow. Fake orchestrator, fake LLM, temp-dir cache."""

from __future__ import annotations

import pytest

from ai.schemas import Source
from src.core.researcher import AsyncResearchAssistant
from src.models import ResearchRequest
from src.storage.cache_store import JsonFileCacheStore, NullCacheStore

QUESTION = "What is photosynthesis?"
ALL_SOURCES = ["wikipedia", "arxiv", "web"]


def make_source(origin: str, title: str | None = None) -> Source:
    return Source(
        title=title or f"{origin} result",
        url=f"https://example.com/{origin}",
        snippet=f"An excerpt from {origin}.",
        origin=origin,
    )


class FakeOrchestrator:
    """Returns canned results per source. A value may be an Exception."""

    def __init__(self, results: dict[str, object] | None = None) -> None:
        self.results = results if results is not None else {
            origin: [make_source(origin)] for origin in ALL_SOURCES
        }
        self.calls: list[tuple[str, list[str]]] = []

    async def fetch_all(self, question, sources):
        self.calls.append((question, list(sources)))
        return {source: self.results.get(source, []) for source in sources}


@pytest.fixture
def orchestrator() -> FakeOrchestrator:
    return FakeOrchestrator()


@pytest.fixture
def cache(tmp_path) -> JsonFileCacheStore:
    return JsonFileCacheStore(tmp_path / "cache", ttl_seconds=3600)


@pytest.fixture
def assistant(orchestrator, cache, fake_llm) -> AsyncResearchAssistant:
    return AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)


def request_for(question=QUESTION, sources=None, bypass=False) -> ResearchRequest:
    return ResearchRequest(
        question=question,
        sources_filter=sources if sources is not None else list(ALL_SOURCES),
        bypass_cache=bypass,
    )


# --- the happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_an_answer_from_all_three_sources(assistant, orchestrator, fake_llm):
    response = await assistant.ask(request_for())
    assert response.question == QUESTION
    assert response.answer == fake_llm.response
    assert response.degraded_sources == []
    assert orchestrator.calls[0][1] == ALL_SOURCES


@pytest.mark.asyncio
async def test_wall_clock_time_is_recorded(assistant):
    response = await assistant.ask(request_for())
    assert response.wall_clock_time_seconds > 0


@pytest.mark.asyncio
async def test_citations_are_flattened_onto_the_response(assistant):
    response = await assistant.ask(request_for())
    # The default fake answer cites [1] and [2].
    assert [c.index for c in response.citations] == [1, 2]
    first = response.citations[0]
    assert first.origin == "wikipedia"
    assert first.url == "https://example.com/wikipedia"
    assert first.title


@pytest.mark.asyncio
async def test_only_requested_sources_are_fetched(assistant, orchestrator):
    await assistant.ask(request_for(sources=["wikipedia", "arxiv"]))
    assert orchestrator.calls[0][1] == ["wikipedia", "arxiv"]


@pytest.mark.asyncio
async def test_duplicate_source_names_are_collapsed(assistant, orchestrator):
    await assistant.ask(request_for(sources=["arxiv", "arxiv", "web"]))
    assert orchestrator.calls[0][1] == ["arxiv", "web"]


# --- caching ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_results_are_cached_for_the_next_call(assistant, orchestrator):
    await assistant.ask(request_for())
    await assistant.ask(request_for())
    # The second call found everything cached, so no second fetch.
    assert len(orchestrator.calls) == 1


@pytest.mark.asyncio
async def test_only_uncached_sources_are_refetched(assistant, orchestrator, cache):
    await cache.set("wikipedia", QUESTION, [make_source("wikipedia")])
    await assistant.ask(request_for())
    assert orchestrator.calls[0][1] == ["arxiv", "web"]


@pytest.mark.asyncio
async def test_cached_sources_still_reach_the_answer(orchestrator, cache, fake_llm):
    await cache.set("wikipedia", QUESTION, [make_source("wikipedia", "Cached title")])
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    response = await assistant.ask(request_for())
    assert "Cached title" in {c.title for c in response.citations}


@pytest.mark.asyncio
async def test_null_cache_refetches_every_time(orchestrator, fake_llm):
    assistant = AsyncResearchAssistant(orchestrator, NullCacheStore(), llm=fake_llm)
    await assistant.ask(request_for())
    await assistant.ask(request_for())
    assert len(orchestrator.calls) == 2


@pytest.mark.asyncio
async def test_a_differently_spelled_question_hits_the_same_cache(assistant, orchestrator):
    await assistant.ask(request_for(question="What is photosynthesis?"))
    await assistant.ask(request_for(question="WHAT IS PHOTOSYNTHESIS"))
    assert len(orchestrator.calls) == 1


# --- graceful degradation -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_source_is_reported_but_the_answer_still_arrives(cache, fake_llm):
    orchestrator = FakeOrchestrator({
        "wikipedia": [make_source("wikipedia")],
        "arxiv": TimeoutError("arxiv timed out"),
        "web": [make_source("web")],
    })
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    response = await assistant.ask(request_for())
    assert response.answer == fake_llm.response
    assert response.degraded_sources == ["arxiv"]


@pytest.mark.asyncio
async def test_a_source_returning_nothing_counts_as_degraded(cache, fake_llm):
    orchestrator = FakeOrchestrator({
        "wikipedia": [make_source("wikipedia")],
        "arxiv": [],
        "web": [make_source("web")],
    })
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    response = await assistant.ask(request_for())
    assert response.degraded_sources == ["arxiv"]


@pytest.mark.asyncio
async def test_degraded_sources_keep_the_requested_order(cache, fake_llm):
    orchestrator = FakeOrchestrator({
        "wikipedia": RuntimeError("down"),
        "arxiv": [make_source("arxiv")],
        "web": RuntimeError("down"),
    })
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    response = await assistant.ask(request_for())
    assert response.degraded_sources == ["wikipedia", "web"]


@pytest.mark.asyncio
async def test_a_failed_source_is_not_cached(cache, fake_llm):
    orchestrator = FakeOrchestrator({
        "wikipedia": [make_source("wikipedia")],
        "arxiv": RuntimeError("down"),
        "web": [make_source("web")],
    })
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    await assistant.ask(request_for())
    assert await cache.get("arxiv", QUESTION) is None


# --- total failure ------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_source_failing_raises_a_clear_error(cache, fake_llm):
    orchestrator = FakeOrchestrator(dict.fromkeys(ALL_SOURCES, RuntimeError("down")))
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    with pytest.raises(ValueError, match="no sources could be retrieved"):
        await assistant.ask(request_for())


@pytest.mark.asyncio
async def test_no_llm_call_is_made_when_there_is_nothing_to_synthesise(cache, fake_llm):
    orchestrator = FakeOrchestrator(dict.fromkeys(ALL_SOURCES, []))
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    with pytest.raises(ValueError):
        await assistant.ask(request_for())
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_error_message_names_the_sources_that_were_tried(cache, fake_llm):
    orchestrator = FakeOrchestrator({"arxiv": []})
    assistant = AsyncResearchAssistant(orchestrator, cache, llm=fake_llm)
    with pytest.raises(ValueError, match="arxiv"):
        await assistant.ask(request_for(sources=["arxiv"]))


# --- retries are delegated, not reimplemented ---------------------------------


@pytest.mark.asyncio
async def test_max_attempts_is_passed_to_the_ai_service(orchestrator, cache, fake_llm):
    assistant = AsyncResearchAssistant(orchestrator, cache, max_attempts=1, llm=fake_llm)
    response = await assistant.ask(request_for())
    assert response.answer == fake_llm.response
    assert len(fake_llm.calls) == 1
