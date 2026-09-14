"""The `ask` command, and the one place the application is assembled.

Everything below this file receives what it needs rather than building it, so
this is the only module that knows about settings, the HTTP client, or which
cache backend is in use. That is what keeps the rest testable.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from src.models import ResearchRequest, ResearchResponse
from src.storage.cache_store import CacheStore, JsonFileCacheStore, NullCacheStore

logger = logging.getLogger(__name__)

# CLI shorthand on the left, the name `ai/` actually uses on the right. The
# translation happens here and nowhere else: if "wiki" reached a cache key,
# "wiki" and "wikipedia" would become two entries holding identical data.
SOURCE_ALIASES = {
    "wiki": "wikipedia",
    "wikipedia": "wikipedia",
    "arxiv": "arxiv",
    "web": "web",
}
DEFAULT_SOURCES = ["wikipedia", "arxiv", "web"]


EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INTERRUPTED = 130


def parse_sources(value: str) -> list[str]:
    """Turn `--sources wiki,arxiv` into canonical names, rejecting typos."""
    names = [part.strip().casefold() for part in value.split(",") if part.strip()]
    if not names:
        raise argparse.ArgumentTypeError("--sources needs at least one name")

    unknown = [name for name in names if name not in SOURCE_ALIASES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown source(s): {', '.join(unknown)}. "
            f"valid names: {', '.join(sorted(SOURCE_ALIASES))}"
        )
    # dict.fromkeys de-duplicates while keeping the order the user asked for.
    return list(dict.fromkeys(SOURCE_ALIASES[name] for name in names))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="researcher",
        description="Research a question across Wikipedia, arXiv and the web.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    ask = subcommands.add_parser("ask", help="research one question")
    ask.add_argument("question", help="the question to research")
    ask.add_argument(
        "--sources",
        type=parse_sources,
        default=list(DEFAULT_SOURCES),
        metavar="wiki,arxiv,web",
        help="restrict the search to a subset of sources",
    )
    ask.add_argument(
        "--no-cache",
        action="store_true",
        help="ignore stored results and fetch everything fresh",
    )
    return parser


def build_cache(settings: Any, *, use_cache: bool) -> CacheStore:
    """Pick a cache backend. This is the whole of `--no-cache`."""
    if not use_cache:
        return NullCacheStore()
    return JsonFileCacheStore(settings.cache_dir, settings.cache_ttl_seconds)


def render(response: ResearchResponse) -> str:
    """Format an answer the way `demo_ai.py` does, plus what it can't know."""
    lines = [f"Q: {response.question}", "", f"A: {response.answer}", ""]

    if response.citations:
        lines.append("References:")
        for citation in response.citations:
            lines.append(f"  [{citation.index}] ({citation.origin}) {citation.title}")
            lines.append(f"      {citation.url}")
        lines.append("")

    if response.degraded_sources:
        lines.append(
            f"Note: {', '.join(response.degraded_sources)} unavailable — "
            "answered from the remaining sources."
        )

    lines.append(f"Completed in {response.wall_clock_time_seconds:.2f}s.")
    return "\n".join(lines)


async def run(args: argparse.Namespace, assistant: Any) -> int:
    """Do the work and print the result. Returns the process exit code."""
    try:
        request = ResearchRequest(
            question=args.question,
            sources_filter=args.sources,
            bypass_cache=args.no_cache,
        )
    except ValidationError:
        print(
            "error: the question must be between 3 and 500 characters.",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    try:
        response = await assistant.ask(request)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except Exception as exc:
        # A provider that failed every retry, or anything else unexpected: the
        # user gets one sentence, the log keeps the traceback.
        logger.exception("Research failed.")
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    print(render(response))
    return EXIT_OK


def _build_orchestrator(client: Any, settings: Any) -> Any:
    """Import the concurrency layer late, so this module works without it."""
    try:
        from src.concurrency.orchestrator import ConcurrentOrchestrator
    except ImportError as exc:
        raise RuntimeError(
            "the concurrent fetching layer is not available yet "
            "(src/concurrency/orchestrator.py)"
        ) from exc
    return ConcurrentOrchestrator(
        client, timeout=settings.per_source_timeout_seconds
    )


async def _ask(args: argparse.Namespace, settings: Any) -> int:
    from src.core.researcher import AsyncResearchAssistant
    from src.services.http_client import create_shared_client

    async with create_shared_client(timeout=settings.per_source_timeout_seconds) as client:
        assistant = AsyncResearchAssistant(
            _build_orchestrator(client, settings),
            build_cache(settings, use_cache=not args.no_cache),
            max_attempts=settings.max_retries,
        )
        return await run(args, assistant)


def main(argv: list[str] | None = None) -> int:
    # First line, before anything reads configuration: pydantic-settings fills
    # our Settings object but never os.environ, and ai/providers/factory.py
    # reads os.getenv. Without this the app falls back to the wrong provider.
    load_dotenv()

    args = build_parser().parse_args(argv)

    # Imported after load_dotenv, because src/config.py builds its settings
    # object at import time.
    from src.config import settings

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        return asyncio.run(_ask(args, settings))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
