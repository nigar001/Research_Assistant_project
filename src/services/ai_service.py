"""Retry, validation and logging around the provided `ai.*` calls.

Both kinds of call the project makes go through one retry loop:

- `synthesize_with_retry` wraps `ai.synthesize`, which is synchronous and blocks
  on the network, so each attempt runs on a worker thread.
- `fetch_with_retry` wraps one `ai.fetch_*` call, which is a genuine coroutine.

`TOPIC.md` requires exponential backoff on every `ai.*` call and every HTTP
fetch; keeping a single loop means there is one retry policy to reason about
rather than two that drift apart.

This module is a thin service layer. It does not implement an LLM, and it
never touches a provider SDK.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from ai.providers.base import LLMProvider, ProviderError
from ai.schemas import AnswerWithCitations, Source
from ai.synthesizer import synthesize

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Matches max_length on ResearchRequest in src/models.py — the same rule,
# enforced before an API call is paid for.
MAX_QUESTION_LENGTH = 500

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0

# A fetch normally answers in well under a second and has to fit inside the
# orchestrator's per-source deadline, so it backs off from a much shorter base
# than synthesis. Three attempts wait at most 0.5s + 1.0s in total.
DEFAULT_FETCH_BASE_DELAY_SECONDS = 0.5

TOO_MANY_REQUESTS = 429


def _validate(question: str, sources: list[Source]) -> None:
    """Reject bad input before spending an API call."""
    if not question.strip():
        raise ValueError("question must be non-empty")
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(
            f"question must be at most {MAX_QUESTION_LENGTH} characters, "
            f"got {len(question)}"
        )
    if not sources:
        raise ValueError("sources must be non-empty")


def _check_attempts(max_attempts: int) -> None:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")


def _backoff_seconds(attempt: int, base_delay: float) -> float:
    """Equal jitter — half the exponential delay, plus a random half.

    The exponential part stops us hammering a provider that is already
    struggling. The random part stops several failed calls from retrying in
    the same instant and colliding again.
    """
    nominal = base_delay * 2 ** (attempt - 1)
    return nominal / 2 + random.uniform(0, nominal / 2)


def _is_synthesis_retryable(exc: BaseException) -> bool:
    # ai/providers/base.py documents ProviderError as a transient or unknown
    # failure and leaves the retry decision to us.
    return isinstance(exc, ProviderError)


def _is_transient_fetch_error(exc: BaseException) -> bool:
    """True for fetch failures that a second attempt could plausibly fix.

    `ai/sources.py` wraps HTTP failures in ProviderError but chains the
    original error as `__cause__`, so the real status code is still readable.
    A 404 or a missing API key will fail identically every time; a rate limit,
    a server error or a dropped connection often will not.
    """
    if not isinstance(exc, ProviderError):
        return False
    cause = exc.__cause__
    if isinstance(cause, httpx.TransportError):
        return True
    if isinstance(cause, httpx.HTTPStatusError):
        status = cause.response.status_code
        return status == TOO_MANY_REQUESTS or status >= 500
    return False


async def _with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[BaseException], bool],
    max_attempts: int,
    base_delay: float,
    sleep: Callable[[float], Awaitable[None]],
    label: str,
) -> T:
    """The one retry loop.

    `call` is a factory rather than a coroutine because an awaited coroutine
    cannot be awaited again — every attempt has to start a fresh one.

    Only `Exception` is caught. Cancellation is a BaseException, so when an
    outer deadline expires it passes straight through, including out of a
    pending backoff sleep.
    """
    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            result = await call()
        except Exception as exc:
            elapsed = time.perf_counter() - started
            if not is_retryable(exc):
                raise
            if attempt == max_attempts:
                logger.error(
                    "%s failed after %d attempt(s), %.2fs on the last: %s",
                    label,
                    attempt,
                    elapsed,
                    exc,
                )
                raise
            wait = _backoff_seconds(attempt, base_delay)
            logger.warning(
                "%s attempt %d/%d failed after %.2fs (%s); retrying in %.2fs.",
                label,
                attempt,
                max_attempts,
                elapsed,
                exc,
                wait,
            )
            await sleep(wait)
        else:
            logger.info(
                "%s succeeded on attempt %d/%d in %.2fs.",
                label,
                attempt,
                max_attempts,
                time.perf_counter() - started,
            )
            return result

    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


async def synthesize_with_retry(
    question: str,
    sources: list[Source],
    *,
    llm: LLMProvider | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AnswerWithCitations:
    """Synthesise an answer from `sources`, retrying transient failures.

    `sleep` is injected so tests can verify the backoff without waiting for it.

    Raises
    ------
    ValueError
        Invalid input, or `max_attempts` below 1. Never retried — no number of
        attempts fixes bad input.
    ProviderError
        Every attempt failed; re-raised from the last one.
    """
    _check_attempts(max_attempts)
    _validate(question, sources)

    return await _with_retry(
        lambda: asyncio.to_thread(synthesize, question, sources, llm=llm),
        is_retryable=_is_synthesis_retryable,
        max_attempts=max_attempts,
        base_delay=base_delay,
        sleep=sleep,
        label="Synthesis",
    )


async def fetch_with_retry(
    fetcher: Callable[..., Awaitable[list[Source]]],
    question: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_FETCH_BASE_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[Source]:
    """Call one `ai.fetch_*` function, retrying failures that might pass.

    Retries rate limits (429), server errors (5xx) and dropped connections.
    Anything else — a 404, a missing API key, malformed XML — fails on the
    first attempt.

    Meant to run inside a per-source deadline such as the orchestrator's
    `asyncio.wait_for`. Fetchers are real coroutines, so when that deadline
    expires the attempt in flight, or the backoff sleep, is genuinely
    cancelled. Unlike synthesis, a deadline here actually stops the work.

    Raises
    ------
    ValueError
        `max_attempts` below 1.
    ProviderError
        A non-transient failure, or every attempt failed.
    """
    _check_attempts(max_attempts)

    return await _with_retry(
        lambda: fetcher(question, client=client),
        is_retryable=_is_transient_fetch_error,
        max_attempts=max_attempts,
        base_delay=base_delay,
        sleep=sleep,
        label=getattr(fetcher, "__name__", "fetch"),
    )
