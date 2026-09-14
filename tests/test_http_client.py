import pytest
import respx
import httpx
from src.services.http_client import create_shared_client

@pytest.mark.asyncio
@respx.mock
async def test_shared_client_headers_and_redirects():
    respx.get("https://example.com").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    
    async with create_shared_client(timeout=5.0) as client:
        response = await client.get("https://example.com")
        
        assert response.status_code == 200
        assert client.headers["User-Agent"] == "async-research-assistant/1.0 (university project; httpx)"
        assert client.follow_redirects is True