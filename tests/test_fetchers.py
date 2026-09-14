import pytest
import respx
import httpx

from ai.sources import (
    fetch_wikipedia,
    fetch_arxiv,
    fetch_web,
    TavilyProvider
)
from src.concurrency.orchestrator import ConcurrentOrchestrator
from src.services.http_client import create_shared_client

@pytest.fixture
def mock_wiki(respx_mock):
    # Mock Opensearch endpoint
    respx_mock.get("https://en.wikipedia.org/w/api.php").respond(
        200, json=["test", ["Test_Title"], [""], ["https://en.wikipedia.org/wiki/Test_Title"]]
    )
    # Mock Summary endpoint
    respx_mock.get("https://en.wikipedia.org/api/rest_v1/page/summary/Test_Title").respond(
        200, json={
            "title": "Test Title",
            "extract": "This is a summary.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Test_Title"}}
        }
    )

@pytest.fixture
def mock_arxiv(respx_mock):
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1234.5678</id>
        <title>Quantum Mocking</title>
        <summary>A paper about mocking.</summary>
      </entry>
    </feed>"""
    respx_mock.get("http://export.arxiv.org/api/query").respond(200, text=xml_response)

@pytest.fixture
def mock_tavily(respx_mock):
    respx_mock.post("https://api.tavily.com/search").respond(
        200, json={
            "results": [
                {"title": "Tavily Result", "url": "https://example.com/tavily", "content": "Tavily snippet."}
            ]
        }
    )

@pytest.mark.asyncio
async def test_fetch_wikipedia_offline(mock_wiki):
    async with create_shared_client(timeout=5.0) as client:
        sources = await fetch_wikipedia("test", client=client)
        assert len(sources) == 1
        assert sources[0].origin == "wikipedia"
        assert sources[0].title == "Test Title"

@pytest.mark.asyncio
async def test_fetch_arxiv_offline(mock_arxiv):
    async with create_shared_client(timeout=5.0) as client:
        sources = await fetch_arxiv("quantum", client=client)
        assert len(sources) == 1
        assert sources[0].origin == "arxiv"
        assert sources[0].title == "Quantum Mocking"

@pytest.mark.asyncio
async def test_fetch_tavily_offline(mock_tavily):
    async with create_shared_client(timeout=5.0) as client:
        provider = TavilyProvider(api_key="fake-key")
        sources = await fetch_web("test", provider=provider, client=client)
        assert len(sources) == 1
        assert sources[0].origin == "web"
        assert "Tavily Result" in sources[0].title

@pytest.mark.asyncio
async def test_orchestrator_integration_offline(mock_wiki, mock_arxiv, mock_tavily, monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    
    async with create_shared_client(timeout=5.0) as client:
        orchestrator = ConcurrentOrchestrator(client=client, timeout=2.0)
        results = await orchestrator.fetch_all("test query", ["wikipedia", "arxiv", "web"])
        
        assert "wikipedia" in results
        assert len(results["wikipedia"]) == 1
        assert "arxiv" in results
        assert len(results["arxiv"]) == 1
        assert "web" in results
        assert len(results["web"]) == 1

@pytest.mark.asyncio
async def test_orchestrator_handles_http_errors(mock_wiki, mock_tavily, monkeypatch, respx_mock):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")
    
    # Intentionally fail arXiv to verify the orchestrator degrades gracefully without crashing
    respx_mock.get("http://export.arxiv.org/api/query").respond(500)
    
    async with create_shared_client(timeout=5.0) as client:
        orchestrator = ConcurrentOrchestrator(client=client, timeout=2.0)
        results = await orchestrator.fetch_all("test query", ["wikipedia", "arxiv", "web"])
        
        assert isinstance(results["wikipedia"], list)
        assert isinstance(results["arxiv"], Exception) 
        assert isinstance(results["web"], list)