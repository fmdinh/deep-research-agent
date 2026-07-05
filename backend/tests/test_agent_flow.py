"""Structural test of the agent graph, using fakes instead of real API calls.

This does NOT hit Anthropic or Tavily. It verifies that plan -> search ->
evaluate -> (loop|synthesize) wires together correctly and that state
accumulates as expected. Run with: pytest backend/tests -q
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import ResearchAgent  # noqa: E402
from app.schemas import EvaluateOutput, PlanOutput, ReportOutput, ReportSection, SearchResult  # noqa: E402


def make_agent() -> ResearchAgent:
    with patch("app.agent.ChatAnthropic") as mock_llm_cls:
        mock_llm_cls.return_value = AsyncMock()
        agent = ResearchAgent(anthropic_api_key="fake", tavily_api_key="fake", model="fake-model")
    return agent


def test_full_flow_stops_after_one_round():
    agent = make_agent()

    plan_result = PlanOutput(sub_queries=["query one", "query two"])
    evaluate_result = EvaluateOutput(enough_information=True, next_query=None, reasoning="Enough.")
    report_result = ReportOutput(
        title="Test report",
        summary="A short summary.",
        sections=[ReportSection(heading="Findings", content="Some content.", source_urls=["http://x"])],
    )

    fake_results = [
        SearchResult(query="query one", title="A", url="http://a", snippet="..."),
    ]

    with patch("app.agent.web_search", return_value=fake_results):
        # with_structured_output returns a new runnable each call; patch the method itself.
        # Only 3 LLM calls actually happen: plan, the real evaluate call (the
        # first evaluate pass after `plan` is skipped because a query is
        # still queued), then synthesize.
        structured_outputs = iter([plan_result, evaluate_result, report_result])

        async def fake_ainvoke(_prompt):
            return next(structured_outputs)

        fake_runnable = AsyncMock()
        fake_runnable.ainvoke = fake_ainvoke
        agent.llm.with_structured_output = lambda *_args, **_kwargs: fake_runnable

        events = asyncio.run(_collect_events(agent, "test question", max_iterations=3))

    node_order = [name for name, _ in events]
    assert node_order[0] == "plan"
    assert "search" in node_order
    assert "evaluate" in node_order
    assert node_order[-1] == "synthesize"

    synth_update = dict(events)["synthesize"]
    assert synth_update["report"]["title"] == "Test report"


async def _collect_events(agent: ResearchAgent, query: str, max_iterations: int):
    events = []
    async for node_name, update in agent.astream_events(query, max_iterations):
        events.append((node_name, update))
    return events


def test_search_node_merges_web_and_local_results():
    """search_node should tag and combine both sources when both are enabled."""
    from unittest.mock import MagicMock

    agent = make_agent()
    agent.document_store = MagicMock()
    agent.document_store.search.return_value = [
        {"title": "paper.pdf", "url": "local://paper.pdf#chunk-0", "snippet": "local snippet"}
    ]

    fake_web_results = [
        SearchResult(query="q", title="Web result", url="http://web", snippet="web snippet", source="web")
    ]

    with patch("app.agent.web_search", return_value=fake_web_results):
        state = agent.initial_state("q", max_iterations=2, use_web=True, use_documents=True)
        state["pending_queries"] = ["q"]
        update = asyncio.run(agent._search_node(state))

    sources = {r.source for r in update["search_results"]}
    assert sources == {"web", "local"}
    assert len(update["search_results"]) == 2


if __name__ == "__main__":
    test_full_flow_stops_after_one_round()
    test_search_node_merges_web_and_local_results()
    print("OK: agent graph flow behaves as expected.")
