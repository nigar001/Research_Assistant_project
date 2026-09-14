"""Tests for the command line. No network: a fake assistant and a temp folder."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from src.cli import (
    build_cache,
    build_parser,
    main,
    parse_sources,
    render,
    run,
)
from src.models import CitationModel, ResearchResponse
from src.storage.cache_store import JsonFileCacheStore, NullCacheStore

QUESTION = "What is photosynthesis?"


class FakeAssistant:
    """Returns a canned response, or raises whatever it was given."""

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list = []

    async def ask(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def make_response(*, citations=True, degraded=()) -> ResearchResponse:
    return ResearchResponse(
        question=QUESTION,
        answer="Plants convert light into chemical energy [1]. Oxygen results [2].",
        citations=[
            CitationModel(
                index=1,
                title="Photosynthesis",
                url="https://en.wikipedia.org/wiki/Photosynthesis",
                origin="wikipedia",
            ),
            CitationModel(
                index=2,
                title="Light-Dependent Reactions",
                url="https://arxiv.org/abs/1706.03762",
                origin="arxiv",
            ),
        ]
        if citations
        else [],
        wall_clock_time_seconds=3.4159,
        degraded_sources=list(degraded),
    )


def args_for(question=QUESTION, sources=None, no_cache=False) -> argparse.Namespace:
    return argparse.Namespace(
        question=question,
        sources=sources if sources is not None else ["wikipedia", "arxiv", "web"],
        no_cache=no_cache,
    )


# --- the three commands in the spec -------------------------------------------


def test_plain_ask_parses():
    args = build_parser().parse_args(["ask", QUESTION])
    assert args.question == QUESTION
    assert args.sources == ["wikipedia", "arxiv", "web"]
    assert args.no_cache is False


def test_sources_flag_parses():
    args = build_parser().parse_args(["ask", QUESTION, "--sources", "wiki,arxiv"])
    assert args.sources == ["wikipedia", "arxiv"]


def test_no_cache_flag_parses():
    args = build_parser().parse_args(["ask", QUESTION, "--no-cache"])
    assert args.no_cache is True


def test_missing_subcommand_exits():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([])
    assert exc.value.code != 0


# --- source names --------------------------------------------------------------


def test_shorthand_is_translated():
    # If "wiki" reached a cache key it would become a second entry for the
    # same data, and nothing would report an error.
    assert parse_sources("wiki") == ["wikipedia"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("wiki,arxiv", ["wikipedia", "arxiv"]),
        ("WIKI, ArXiv", ["wikipedia", "arxiv"]),
        ("web", ["web"]),
        ("wiki,wikipedia,arxiv", ["wikipedia", "arxiv"]),
        ("arxiv,wiki", ["arxiv", "wikipedia"]),
    ],
)
def test_source_lists_are_normalised(raw, expected):
    assert parse_sources(raw) == expected


def test_unknown_source_is_rejected_with_the_valid_names():
    with pytest.raises(argparse.ArgumentTypeError, match="valid names"):
        parse_sources("reddi")


def test_empty_source_list_is_rejected():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_sources(" , ")


def test_unknown_source_exits_non_zero_through_the_parser():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["ask", QUESTION, "--sources", "reddi"])
    assert exc.value.code != 0


# --- which cache gets used -----------------------------------------------------


def test_default_uses_the_json_cache(tmp_path):
    settings = SimpleNamespace(cache_dir=tmp_path, cache_ttl_seconds=3600)
    assert isinstance(build_cache(settings, use_cache=True), JsonFileCacheStore)


def test_no_cache_uses_the_null_cache(tmp_path):
    settings = SimpleNamespace(cache_dir=tmp_path, cache_ttl_seconds=3600)
    assert isinstance(build_cache(settings, use_cache=False), NullCacheStore)


# --- rendering -----------------------------------------------------------------


def test_render_matches_the_expected_layout():
    assert render(make_response()) == (
        "Q: What is photosynthesis?\n"
        "\n"
        "A: Plants convert light into chemical energy [1]. Oxygen results [2].\n"
        "\n"
        "References:\n"
        "  [1] (wikipedia) Photosynthesis\n"
        "      https://en.wikipedia.org/wiki/Photosynthesis\n"
        "  [2] (arxiv) Light-Dependent Reactions\n"
        "      https://arxiv.org/abs/1706.03762\n"
        "\n"
        "Completed in 3.42s."
    )


def test_render_names_the_source_that_failed():
    out = render(make_response(degraded=["arxiv"]))
    assert "Note: arxiv unavailable" in out
    assert "answered from the remaining sources" in out


def test_render_without_citations_omits_the_reference_block():
    out = render(make_response(citations=False))
    assert "References:" not in out
    assert out.endswith("Completed in 3.42s.")


# --- running -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_prints_the_answer_and_exits_zero(capsys):
    assistant = FakeAssistant(response=make_response())
    assert await run(args_for(), assistant) == 0
    assert "References:" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_the_request_carries_the_chosen_sources():
    assistant = FakeAssistant(response=make_response())
    await run(args_for(sources=["wikipedia"], no_cache=True), assistant)
    request = assistant.requests[0]
    assert request.sources_filter == ["wikipedia"]
    assert request.bypass_cache is True


@pytest.mark.asyncio
async def test_no_sources_available_exits_non_zero(capsys):
    assistant = FakeAssistant(error=ValueError("no sources could be retrieved"))
    assert await run(args_for(), assistant) == 1
    assert "no sources could be retrieved" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_synthesis_failure_exits_non_zero_with_one_sentence(capsys):
    assistant = FakeAssistant(error=RuntimeError("provider unavailable"))
    assert await run(args_for(), assistant) == 1
    err = capsys.readouterr().err
    assert "provider unavailable" in err
    assert "Traceback" not in err


@pytest.mark.asyncio
async def test_question_too_short_is_rejected_before_any_work(capsys):
    assistant = FakeAssistant(response=make_response())
    assert await run(args_for(question="hi"), assistant) == 1
    assert assistant.requests == []
    assert "between 3 and 500" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_question_too_long_is_rejected_before_any_work(capsys):
    assistant = FakeAssistant(response=make_response())
    assert await run(args_for(question="x" * 501), assistant) == 1
    assert assistant.requests == []


# --- the entry point -----------------------------------------------------------

def test_missing_orchestrator_reports_a_sentence_not_a_traceback(monkeypatch, capsys):
    import sys

    # Simulate the orchestrator module being missing to test graceful degradation
    monkeypatch.setitem(sys.modules, "src.concurrency.orchestrator", None)

    assert main(["ask", QUESTION]) == 1
    assert "concurrent fetching layer is not available" in capsys.readouterr().err


def test_researcher_package_is_importable():
    # `python -m researcher` needs this package to exist.
    import researcher

    assert researcher.__doc__
