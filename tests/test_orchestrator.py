import pytest
import httpx
from src.concurrency.orchestrator import ConcurrentOrchestrator, SOURCE_FETCHER_MAP
from ai.schemas import Source
from ai.providers.base import ProviderError


@pytest.mark.asyncio
async def test_fetch_all_success(monkeypatch):
    # Changed to 'async def' to test the fetch_with_retry path
    async def mock_wiki(question, client=None):
        return [
            Source(
                title="Test",
                url="https://example.com",
                snippet="Data",
                origin="wikipedia",
            )
        ]

    monkeypatch.setitem(SOURCE_FETCHER_MAP, "wikipedia", mock_wiki)

    async with httpx.AsyncClient() as client:
        orchestrator = ConcurrentOrchestrator(client=client, timeout=2.0)
        res = await orchestrator.fetch_all("What is AI?", ["wikipedia"])

    assert "wikipedia" in res
    assert isinstance(res["wikipedia"], list)
    assert len(res["wikipedia"]) == 1
    assert res["wikipedia"][0].title == "Test"


@pytest.mark.asyncio
async def test_fetch_all_captures_exception_without_raising(monkeypatch):
    # Changed to 'async def'
    async def mock_arxiv_fail(question, client=None):
        raise RuntimeError("Network crashed")

    monkeypatch.setitem(SOURCE_FETCHER_MAP, "arxiv", mock_arxiv_fail)

    async with httpx.AsyncClient() as client:
        orchestrator = ConcurrentOrchestrator(client=client, timeout=2.0)
        res = await orchestrator.fetch_all("Quantum physics", ["arxiv"])

    assert "arxiv" in res
    assert isinstance(res["arxiv"], Exception)
    assert str(res["arxiv"]) == "Network crashed"


@pytest.mark.asyncio
async def test_fetch_all_recovers_on_retry(monkeypatch):
    """Verify that a source failing once with a transient 429 recovers on retry."""
    call_count = 0

    async def mock_wiki_transient(question, client=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate a transient HTTP 429 rate limit failure on attempt 1
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(429, request=request)
            cause = httpx.HTTPStatusError("Rate limited", request=request, response=response)
            
            err = ProviderError("Wikipedia rate limit")
            err.__cause__ = cause
            raise err

        # Succeed on attempt 2
        return [
            Source(
                title="Recovered Data",
                url="https://example.com",
                snippet="Data",
                origin="wikipedia",
            )
        ]

    monkeypatch.setitem(SOURCE_FETCHER_MAP, "wikipedia", mock_wiki_transient)

    async with httpx.AsyncClient() as client:
        orchestrator = ConcurrentOrchestrator(client=client, timeout=5.0)
        res = await orchestrator.fetch_all("What is AI?", ["wikipedia"])

    assert "wikipedia" in res
    assert isinstance(res["wikipedia"], list)
    assert res["wikipedia"][0].title == "Recovered Data"
    assert call_count == 2