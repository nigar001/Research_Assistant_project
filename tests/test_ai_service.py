"""Tests for the synthesis service. No network: fake LLMs and an injected sleep."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from ai.providers.base import ProviderError
from ai.schemas import AnswerWithCitations
from ai.sources import fetch_arxiv
from src.services.ai_service import (
    DEFAULT_FETCH_BASE_DELAY_SECONDS,
    MAX_QUESTION_LENGTH,
    _backoff_seconds,
    _is_transient_fetch_error,
    fetch_with_retry,
    synthesize_with_retry,
)
from tests.conftest import FakeLLM

QUESTION = "What is photosynthesis?"


class FlakyLLM(FakeLLM):
    """Raises for the first `failures` calls, then behaves like FakeLLM."""

    def __init__(self, failures: int, exc: Exception | None = None) -> None:
        super().__init__()
        self.failures = failures
        self.exc = exc or ProviderError("provider unavailable")

    def complete(self, prompt, *, json_schema=None, max_tokens=1024):
        self.calls.append(prompt)
        if len(self.calls) <= self.failures:
            raise self.exc
        return self.response


class RecordingSleep:
    """Stands in for asyncio.sleep so backoff can be asserted, not waited for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def sleep() -> RecordingSleep:
    return RecordingSleep()


# --- the happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_call_returns_the_answer(fake_llm, sample_sources, sleep):
    answer = await synthesize_with_retry(
        QUESTION, sample_sources, llm=fake_llm, sleep=sleep
    )
    assert isinstance(answer, AnswerWithCitations)
    assert answer.question == QUESTION
    assert answer.answer == fake_llm.response
    assert len(fake_llm.calls) == 1
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_sources_reach_the_prompt(fake_llm, sample_sources, sleep):
    await synthesize_with_retry(QUESTION, sample_sources, llm=fake_llm, sleep=sleep)
    prompt = fake_llm.calls[0]
    assert QUESTION in prompt
    for source in sample_sources:
        assert source.title in prompt


@pytest.mark.asyncio
async def test_citations_are_resolved_from_the_answer(fake_llm, sample_sources, sleep):
    answer = await synthesize_with_retry(
        QUESTION, sample_sources, llm=fake_llm, sleep=sleep
    )
    # The default fake response cites [1] and [2].
    assert [c.index for c in answer.citations] == [1, 2]
    assert answer.citations[0].source == sample_sources[0]


# --- retry behaviour ----------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_failure_is_retried_then_succeeds(sample_sources, sleep):
    llm = FlakyLLM(failures=1)
    answer = await synthesize_with_retry(QUESTION, sample_sources, llm=llm, sleep=sleep)
    assert answer.answer == llm.response
    assert len(llm.calls) == 2
    assert len(sleep.delays) == 1


@pytest.mark.asyncio
async def test_persistent_failure_raises_after_max_attempts(sample_sources, sleep):
    llm = FlakyLLM(failures=99)
    with pytest.raises(ProviderError):
        await synthesize_with_retry(
            QUESTION, sample_sources, llm=llm, max_attempts=3, sleep=sleep
        )
    assert len(llm.calls) == 3
    # Three attempts means two waits — never a pointless sleep after the last.
    assert len(sleep.delays) == 2


@pytest.mark.asyncio
async def test_single_attempt_never_sleeps(sample_sources, sleep):
    llm = FlakyLLM(failures=99)
    with pytest.raises(ProviderError):
        await synthesize_with_retry(
            QUESTION, sample_sources, llm=llm, max_attempts=1, sleep=sleep
        )
    assert len(llm.calls) == 1
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_value_error_from_the_provider_is_not_retried(sample_sources, sleep):
    # Retrying cannot fix bad input, so it must fail on the first attempt.
    llm = FlakyLLM(failures=99, exc=ValueError("bad input"))
    with pytest.raises(ValueError):
        await synthesize_with_retry(QUESTION, sample_sources, llm=llm, sleep=sleep)
    assert len(llm.calls) == 1
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_unexpected_errors_are_not_retried(sample_sources, sleep):
    llm = FlakyLLM(failures=99, exc=TypeError("bug in our code"))
    with pytest.raises(TypeError):
        await synthesize_with_retry(QUESTION, sample_sources, llm=llm, sleep=sleep)
    assert len(llm.calls) == 1


# --- backoff ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_waits_grow_between_attempts(sample_sources, sleep):
    llm = FlakyLLM(failures=99)
    with pytest.raises(ProviderError):
        await synthesize_with_retry(
            QUESTION, sample_sources, llm=llm, max_attempts=4, base_delay=1.0, sleep=sleep
        )
    assert len(sleep.delays) == 3
    assert sleep.delays[0] <= sleep.delays[1] <= sleep.delays[2]


def test_backoff_stays_within_its_jitter_band():
    # Equal jitter: between half and all of the nominal exponential delay.
    for attempt in (1, 2, 3, 4):
        nominal = 1.0 * 2 ** (attempt - 1)
        for _ in range(50):
            wait = _backoff_seconds(attempt, base_delay=1.0)
            assert nominal / 2 <= wait <= nominal


def test_backoff_is_not_always_identical():
    # Without jitter, simultaneous failures would all retry in the same instant.
    waits = {_backoff_seconds(2, base_delay=1.0) for _ in range(50)}
    assert len(waits) > 1


# --- validation, before any API call is paid for -------------------------------


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
@pytest.mark.asyncio
async def test_empty_question_rejected_without_calling_the_llm(
    bad, fake_llm, sample_sources, sleep
):
    with pytest.raises(ValueError, match="non-empty"):
        await synthesize_with_retry(bad, sample_sources, llm=fake_llm, sleep=sleep)
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_oversized_question_rejected_without_calling_the_llm(
    fake_llm, sample_sources, sleep
):
    with pytest.raises(ValueError, match="at most"):
        await synthesize_with_retry(
            "x" * (MAX_QUESTION_LENGTH + 1), sample_sources, llm=fake_llm, sleep=sleep
        )
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_question_at_the_limit_is_accepted(fake_llm, sample_sources, sleep):
    await synthesize_with_retry(
        "x" * MAX_QUESTION_LENGTH, sample_sources, llm=fake_llm, sleep=sleep
    )
    assert len(fake_llm.calls) == 1


@pytest.mark.asyncio
async def test_empty_sources_rejected_without_calling_the_llm(fake_llm, sleep):
    with pytest.raises(ValueError, match="sources"):
        await synthesize_with_retry(QUESTION, [], llm=fake_llm, sleep=sleep)
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_max_attempts_below_one_is_rejected(fake_llm, sample_sources, sleep):
    with pytest.raises(ValueError, match="max_attempts"):
        await synthesize_with_retry(
            QUESTION, sample_sources, llm=fake_llm, max_attempts=0, sleep=sleep
        )
    assert fake_llm.calls == []


# --- the point of the whole module --------------------------------------------


@pytest.mark.asyncio
async def test_blocking_llm_does_not_freeze_the_event_loop(sample_sources, sleep):
    """`ai.synthesize` blocks. Without asyncio.to_thread this would stall everything."""

    class SlowLLM(FakeLLM):
        def complete(self, prompt, *, json_schema=None, max_tokens=1024):
            self.calls.append(prompt)
            time.sleep(0.20)
            return self.response

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    await synthesize_with_retry(QUESTION, sample_sources, llm=SlowLLM(), sleep=sleep)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # A frozen loop would leave this at 0.
    assert ticks >= 5


# ==============================================================================
# fetch_with_retry — exercised against the real ai.fetch_arxiv, with HTTP
# responses scripted by httpx.MockTransport. No request leaves the machine.
# ==============================================================================

ARXIV_OK = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762</id>
    <title>Light-Dependent Reactions of Photosynthesis</title>
    <summary>How chloroplasts capture light energy.</summary>
  </entry>
</feed>"""


def scripted_client(*steps):
    """A client whose Nth request gets the Nth step: a status code or an error.

    Returns the client and the list of requests it received, so a test can
    count how many times the fetcher really went to the network.
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        step = steps[min(len(requests), len(steps) - 1)]
        requests.append(request)
        if isinstance(step, type) and issubclass(step, Exception):
            raise step("simulated failure", request=request)
        if step == 200:
            return httpx.Response(200, text=ARXIV_OK, request=request)
        return httpx.Response(step, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requests


# --- recovering from transient failures ---------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_then_success_recovers(sleep):
    client, requests = scripted_client(429, 200)
    async with client:
        sources = await fetch_with_retry(fetch_arxiv, "photosynthesis", client=client, sleep=sleep)
    assert [s.origin for s in sources] == ["arxiv"]
    assert len(requests) == 2
    assert len(sleep.delays) == 1


@pytest.mark.asyncio
async def test_server_errors_then_success_recovers(sleep):
    client, requests = scripted_client(503, 502, 200)
    async with client:
        sources = await fetch_with_retry(fetch_arxiv, "photosynthesis", client=client, sleep=sleep)
    assert sources
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_dropped_connection_is_retried(sleep):
    client, requests = scripted_client(httpx.ConnectError, 200)
    async with client:
        sources = await fetch_with_retry(fetch_arxiv, "photosynthesis", client=client, sleep=sleep)
    assert sources
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_persistent_rate_limit_gives_up_after_max_attempts(sleep):
    client, requests = scripted_client(429)
    async with client:
        with pytest.raises(ProviderError):
            await fetch_with_retry(
                fetch_arxiv, "photosynthesis", client=client, max_attempts=3, sleep=sleep
            )
    assert len(requests) == 3
    assert len(sleep.delays) == 2


@pytest.mark.asyncio
async def test_each_attempt_calls_the_fetcher_afresh(sleep):
    # A coroutine can only be awaited once, so retrying must start a new call.
    calls = 0

    async def flaky_fetcher(question, *, client=None):
        nonlocal calls
        calls += 1
        if calls < 3:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(503, request=request)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ProviderError("temporarily unavailable") from exc
        return []

    await fetch_with_retry(flaky_fetcher, "q", sleep=sleep)
    assert calls == 3


# --- failures a retry cannot fix ----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_client_errors_are_not_retried(status, sleep):
    client, requests = scripted_client(status)
    async with client:
        with pytest.raises(ProviderError):
            await fetch_with_retry(fetch_arxiv, "photosynthesis", client=client, sleep=sleep)
    assert len(requests) == 1
    assert sleep.delays == []


@pytest.mark.asyncio
async def test_provider_error_without_a_network_cause_is_not_retried(sleep):
    # e.g. "TAVILY_API_KEY is not set." — a setup mistake, identical every time.
    calls = 0

    async def misconfigured(question, *, client=None):
        nonlocal calls
        calls += 1
        raise ProviderError("TAVILY_API_KEY is not set.")

    with pytest.raises(ProviderError, match="TAVILY_API_KEY"):
        await fetch_with_retry(misconfigured, "q", sleep=sleep)
    assert calls == 1


@pytest.mark.asyncio
async def test_fetch_max_attempts_below_one_is_rejected(sleep):
    with pytest.raises(ValueError, match="max_attempts"):
        await fetch_with_retry(fetch_arxiv, "q", max_attempts=0, sleep=sleep)


# --- the classification rule, directly ----------------------------------------


def _provider_error_for(status: int) -> ProviderError:
    request = httpx.Request("GET", "https://example.com")
    try:
        httpx.Response(status, request=request).raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            raise ProviderError("wrapped") from exc
        except ProviderError as wrapped:
            return wrapped
    raise AssertionError("expected an HTTP error")


@pytest.mark.parametrize(
    "status,transient",
    [(429, True), (500, True), (502, True), (503, True),
     (400, False), (401, False), (403, False), (404, False)],
)
def test_status_codes_are_classified(status, transient):
    assert _is_transient_fetch_error(_provider_error_for(status)) is transient


def test_errors_that_are_not_provider_errors_are_never_transient():
    assert _is_transient_fetch_error(ValueError("unknown source")) is False
    assert _is_transient_fetch_error(TypeError("bug")) is False


# --- fitting inside the orchestrator's deadline --------------------------------


@pytest.mark.asyncio
async def test_total_waiting_stays_well_inside_the_deadline(sleep):
    client, _ = scripted_client(429)
    async with client:
        with pytest.raises(ProviderError):
            await fetch_with_retry(fetch_arxiv, "photosynthesis", client=client, sleep=sleep)
    # Equal jitter from a 0.5s base: at most 0.5s then 1.0s.
    assert sum(sleep.delays) <= DEFAULT_FETCH_BASE_DELAY_SECONDS * 3
    assert sum(sleep.delays) < 8.0


@pytest.mark.asyncio
async def test_an_outer_deadline_cancels_a_pending_retry():
    """Retries go inside the deadline; the deadline must still win.

    Uses the real asyncio.sleep with a long backoff. If cancellation were
    swallowed, this would sit in the backoff for seconds instead of stopping.
    """
    client, requests = scripted_client(503)
    started = time.perf_counter()
    async with client:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                fetch_with_retry(fetch_arxiv, "photosynthesis", client=client, base_delay=10.0),
                timeout=0.2,
            )
    assert time.perf_counter() - started < 1.0
    assert len(requests) == 1
