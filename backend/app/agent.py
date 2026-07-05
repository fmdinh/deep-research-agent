"""The research agent, built as an explicit LangGraph state machine.

Flow:

    START -> plan -> search -> evaluate -*-> search   (loop while more info needed)
                                          '-> synthesize -> END

Each node is a small, testable function. The graph is compiled once and
reused; callers stream node-by-node updates so the API layer can forward
progress to the frontend over SSE.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, List, Optional, TypedDict

from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, StateGraph

from .documents import DocumentStore
from .schemas import EvaluateOutput, PlanOutput, ReportOutput, SearchResult
from .tools import SearchToolError, web_search

logger = logging.getLogger(__name__)


def _add(existing: list, new: list) -> list:
    """Reducer: append new items instead of overwriting (used for log/result lists)."""
    return existing + new


class AgentState(TypedDict):
    original_query: str
    max_iterations: int
    use_web: bool
    use_documents: bool
    iteration: int
    pending_queries: List[str]
    completed_queries: Annotated[List[str], _add]
    search_results: Annotated[List[SearchResult], _add]
    log: Annotated[List[str], _add]
    report: Optional[dict]
    error: Optional[str]


class ResearchAgent:
    """Wraps a compiled LangGraph graph plus the LLM/tool config it needs."""

    def __init__(
        self,
        anthropic_api_key: str,
        tavily_api_key: str,
        model: str,
        document_store: Optional[DocumentStore] = None,
    ):
        self.tavily_api_key = tavily_api_key
        self.document_store = document_store
        self.llm = ChatAnthropic(
            model=model,
            api_key=anthropic_api_key,
            temperature=0,
            max_tokens=2000,
        )
        self.graph = self._build_graph()

    # ---- nodes -----------------------------------------------------------

    async def _plan_node(self, state: AgentState) -> dict:
        planner = self.llm.with_structured_output(PlanOutput)
        has_docs = bool(self.document_store and self.document_store.has_documents())
        doc_note = (
            "\n\nThe user has also uploaded reference document(s) that will be searched "
            "alongside the web for each query — phrase queries so they'd also match "
            "content likely to appear in those documents, not only news-style web results."
            if (has_docs and state["use_documents"])
            else ""
        )
        prompt = (
            "You are a research planner. Break the user's question into 2-4 "
            "concrete, distinct search queries that together would let "
            f"someone answer it thoroughly. Avoid redundant queries.{doc_note}\n\n"
            f"Question: {state['original_query']}"
        )
        result: PlanOutput = await planner.ainvoke(prompt)
        queries = result.sub_queries[:4] or [state["original_query"]]
        return {
            "pending_queries": queries,
            "log": [f"Planned {len(queries)} search queries."],
        }

    async def _search_node(self, state: AgentState) -> dict:
        if not state["pending_queries"]:
            return {"log": ["No pending queries, skipping search."]}

        query = state["pending_queries"][0]
        remaining = state["pending_queries"][1:]
        results: List[SearchResult] = []
        log_lines: List[str] = []

        if state["use_web"]:
            try:
                web_results = web_search(query, api_key=self.tavily_api_key, max_results=4)
                results.extend(web_results)
                log_lines.append(f"Web search: {query!r} -> {len(web_results)} results.")
            except SearchToolError as exc:
                log_lines.append(f"Web search failed for {query!r}: {exc}")

        if state["use_documents"] and self.document_store is not None:
            local_hits = self.document_store.search(query, top_k=4)
            local_results = [
                SearchResult(
                    query=query,
                    title=hit["title"],
                    url=hit["url"],
                    snippet=hit["snippet"],
                    source="local",
                )
                for hit in local_hits
            ]
            results.extend(local_results)
            if local_results:
                log_lines.append(f"Document search: {query!r} -> {len(local_results)} matches.")

        if not log_lines:
            log_lines.append(f"No sources enabled for query {query!r}.")

        return {
            "pending_queries": remaining,
            "completed_queries": [query],
            "search_results": results,
            "log": log_lines,
        }

    async def _evaluate_node(self, state: AgentState) -> dict:
        # If a plan-time query is still queued, run it before re-evaluating.
        if state["pending_queries"]:
            return {"log": ["Queries still pending, continuing search."]}

        evaluator = self.llm.with_structured_output(EvaluateOutput)
        context = "\n".join(
            f"- ({r.source}) [{r.title}]({r.url}): {r.snippet[:200]}"
            for r in state["search_results"]
        ) or "(no results yet)"
        prompt = (
            "You are deciding whether enough information has been gathered to "
            f"answer this research question: {state['original_query']}\n\n"
            f"Search results so far:\n{context}\n\n"
            "If important angles are missing, propose exactly one new, specific "
            "search query to fill the gap. Otherwise say enough_information=true."
        )
        result: EvaluateOutput = await evaluator.ainvoke(prompt)

        at_cap = state["iteration"] + 1 >= state["max_iterations"]
        enough = result.enough_information or at_cap or not result.next_query

        update: dict = {
            "iteration": state["iteration"] + 1,
            "log": [f"Evaluation: {result.reasoning}"],
        }
        if not enough:
            update["pending_queries"] = [result.next_query]
        return update

    async def _synthesize_node(self, state: AgentState) -> dict:
        writer = self.llm.with_structured_output(ReportOutput)
        sources = "\n".join(
            f"[{i+1}] ({r.source}) {r.title} — {r.url}\n{r.snippet[:400]}"
            for i, r in enumerate(state["search_results"])
        ) or "(no sources gathered)"
        prompt = (
            f"Write a well-organized research report answering: {state['original_query']}\n\n"
            "Base every claim only on the sources below. Sources marked (local) come "
            "from documents the user uploaded — prefer them when they're directly "
            "relevant, and cite them by their filename/url like any other source. "
            "For each section, list the source URLs actually used. Be concise and "
            "factual.\n\n"
            f"Sources:\n{sources}"
        )
        result: ReportOutput = await writer.ainvoke(prompt)
        return {
            "report": result.model_dump(),
            "log": ["Report synthesized."],
        }

    # ---- routing -----------------------------------------------------------

    @staticmethod
    def _route_after_evaluate(state: AgentState) -> str:
        return "search" if state["pending_queries"] else "synthesize"

    # ---- graph assembly -----------------------------------------------------

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("plan", self._plan_node)
        graph.add_node("search", self._search_node)
        graph.add_node("evaluate", self._evaluate_node)
        graph.add_node("synthesize", self._synthesize_node)

        graph.set_entry_point("plan")
        graph.add_edge("plan", "search")
        graph.add_edge("search", "evaluate")
        graph.add_conditional_edges(
            "evaluate",
            self._route_after_evaluate,
            {"search": "search", "synthesize": "synthesize"},
        )
        graph.add_edge("synthesize", END)
        return graph.compile()

    # ---- public API -----------------------------------------------------------

    def initial_state(
        self, query: str, max_iterations: int, use_web: bool = True, use_documents: bool = True
    ) -> AgentState:
        return AgentState(
            original_query=query,
            max_iterations=max_iterations,
            use_web=use_web,
            use_documents=use_documents,
            iteration=0,
            pending_queries=[],
            completed_queries=[],
            search_results=[],
            log=[],
            report=None,
            error=None,
        )

    async def astream_events(
        self, query: str, max_iterations: int, use_web: bool = True, use_documents: bool = True
    ):
        """Yield (node_name, state_after_node) for every step, for SSE streaming."""
        state = self.initial_state(query, max_iterations, use_web, use_documents)
        async for event in self.graph.astream(state, stream_mode="updates"):
            for node_name, node_update in event.items():
                yield node_name, node_update
