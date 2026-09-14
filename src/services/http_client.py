import httpx

# Wikipedia rejects generic agents, so identify the project honestly.
USER_AGENT = "async-research-assistant/1.0 (university project; httpx)"

def create_shared_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )