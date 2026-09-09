import pytest
import httpx
from src.concurrency.orchestrator import ConcurrentOrchestrator, SOURCE_FETCHER_MAP
from ai.schemas import Source


@pytest.mark.asyncio
async def test_fetch_all_success(monkeypatch):
    def mock_wiki(question, client=None):
        return [
            Source(
                title="Test",
                url="https://example.com",
                snippet="Data",
                origin="wikipedia",
            )
        ]

    # Patch the dictionary lookup directly
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
    def mock_arxiv_fail(question, client=None):
        raise RuntimeError("Network crashed")

    # Patch the dictionary lookup directly
    monkeypatch.setitem(SOURCE_FETCHER_MAP, "arxiv", mock_arxiv_fail)

    async with httpx.AsyncClient() as client:
        orchestrator = ConcurrentOrchestrator(client=client, timeout=2.0)
        res = await orchestrator.fetch_all("Quantum physics", ["arxiv"])

    assert "arxiv" in res
    assert isinstance(res["arxiv"], Exception)
    assert str(res["arxiv"]) == "Network crashed"