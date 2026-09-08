"""Retry, validation and logging around the provided `ai.synthesize`.

`ai.synthesize` is synchronous and blocks on the network. Calling it directly
from a coroutine would freeze the event loop and stall the concurrent source
fetches this project is built around, so every call is moved to a worker
thread.

This module is a thin service layer. It does not implement an LLM, and it
never touches a provider SDK.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

from ai.providers.base import LLMProvider, ProviderError
from ai.schemas import AnswerWithCitations, Source
from ai.synthesizer import synthesize

logger = logging.getLogger(__name__)

# Matches max_length on ResearchRequest in src/models.py — the same rule,
# enforced before an API call is paid for.
MAX_QUESTION_LENGTH = 500

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0


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


def _backoff_seconds(attempt: int, base_delay: float) -> float:
    """Equal jitter — half the exponential delay, plus a random half.

    The exponential part stops us hammering a provider that is already
    struggling. The random part stops several failed calls from retrying in
    the same instant and colliding again.
    """
    nominal = base_delay * 2 ** (attempt - 1)
    return nominal / 2 + random.uniform(0, nominal / 2)


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
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    _validate(question, sources)

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            answer = await asyncio.to_thread(synthesize, question, sources, llm=llm)
        except ProviderError as exc:
            elapsed = time.perf_counter() - started
            if attempt == max_attempts:
                logger.error(
                    "Synthesis failed after %d attempt(s), %.2fs on the last: %s",
                    attempt,
                    elapsed,
                    exc,
                )
                raise
            wait = _backoff_seconds(attempt, base_delay)
            logger.warning(
                "Synthesis attempt %d/%d failed after %.2fs (%s); retrying in %.2fs.",
                attempt,
                max_attempts,
                elapsed,
                exc,
                wait,
            )
            await sleep(wait)
        else:
            logger.info(
                "Synthesis succeeded on attempt %d/%d in %.2fs, %d citation(s).",
                attempt,
                max_attempts,
                time.perf_counter() - started,
                len(answer.citations),
            )
            return answer

    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover
