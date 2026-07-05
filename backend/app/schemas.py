"""Pydantic models used across the API and the agent."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    """Incoming request to kick off a research run."""

    query: str = Field(..., min_length=3, max_length=500, description="The research question")
    max_iterations: int = Field(
        default=3, ge=1, le=6, description="How many search rounds the agent may run"
    )
    use_web: bool = Field(default=True, description="Include live web search results")
    use_documents: bool = Field(
        default=True, description="Include results from documents the user has uploaded"
    )


class SearchResult(BaseModel):
    """A single web search hit, normalized across providers."""

    query: str
    title: str
    url: str
    snippet: str
    source: str = "web"  # "web" | "local"


class PlanOutput(BaseModel):
    """Structured output the LLM must return when planning sub-queries."""

    sub_queries: List[str] = Field(
        ..., description="2-4 concrete, distinct search queries that together cover the question"
    )


class EvaluateOutput(BaseModel):
    """Structured output the LLM must return when deciding whether to keep researching."""

    enough_information: bool = Field(
        ..., description="True if the gathered results are sufficient to answer the question"
    )
    next_query: Optional[str] = Field(
        default=None,
        description="If not enough information, one new search query to fill the gap. "
        "Omit or leave null if enough_information is true.",
    )
    reasoning: str = Field(..., description="One short sentence explaining the decision")


class ReportOutput(BaseModel):
    """Final structured report."""

    title: str
    summary: str
    sections: List["ReportSection"]


class ReportSection(BaseModel):
    heading: str
    content: str
    source_urls: List[str] = Field(default_factory=list)


ReportOutput.model_rebuild()


class DocumentInfo(BaseModel):
    filename: str
    chunks: int


class AgentEvent(BaseModel):
    """A single step emitted over the SSE stream so the frontend can show progress."""

    type: str  # "status" | "plan" | "search" | "evaluate" | "report" | "error" | "done"
    message: str
    data: Optional[dict] = None
