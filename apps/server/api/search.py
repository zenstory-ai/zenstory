"""File search API endpoints for Agent API.

Provides database keyword retrieval for external AI agents using X-Agent-API-Key authentication.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.agent_dependencies import AgentAuthContext, require_project_access
from core.error_codes import ErrorCode
from core.error_handler import APIException
from middleware.rate_limit import require_agent_rate_limit
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["Agent API - Search"])


# ==================== Request/Response Models ====================


class SearchRequest(BaseModel):
    """Request body for keyword search."""

    query: str = Field(..., description="Search query")
    top_k: int = Field(default=10, ge=1, le=50, description="Maximum results")
    file_types: list[str] | None = Field(default=None, description="Filter by file types")
    include_content: bool = Field(
        default=False,
        description="Whether to include full content in each result (default false for snippet-first payload).",
    )


class SearchResult(BaseModel):
    """Single search result."""

    id: str
    title: str
    file_type: str
    content: str | None
    score: float
    snippet: str | None = None
    line_start: int | None = None
    fused_score: float | None = None
    sources: list[str] | None = None
    metadata: dict | None


class SearchResponse(BaseModel):
    """Response body for keyword search."""

    query: str
    results: list[SearchResult]
    result_count: int


# ==================== Search Endpoint ====================


@router.post("/projects/{project_id}/search", response_model=SearchResponse)
async def search(
    project_id: str,
    body: SearchRequest,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_search", 500, 3600)),
    context: AgentAuthContext = Depends(require_project_access("read")),
):
    """
    Keyword search within a project.

    Requires scope: read
    Requires project access

    Request body:
    - query: Search query
    - top_k: Maximum results (1-50, default 10)
    - file_types: Filter by file types (optional)
    """
    _session, user_id, api_key = context

    try:
        from services.infra.file_search_service import FileSearchService

        def run_search():
            svc = FileSearchService()
            return svc.search(
                project_id=project_id,
                query=body.query,
                top_k=body.top_k,
                entity_types=body.file_types,
                include_content=body.include_content,
            )

        results = await asyncio.to_thread(run_search)
    except Exception as e:
        logger.exception(
            "Agent API: File search failed",
            extra={"user_id": user_id, "project_id": project_id},
        )
        raise APIException(
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            detail="File search service unavailable",
        ) from e

    # Convert results to response format
    search_results = []
    for r in results:
        search_results.append(
            SearchResult(
                id=r.entity_id,
                title=r.title,
                file_type=r.entity_type,
                content=r.content if body.include_content else None,
                score=r.score or 0.0,
                snippet=r.snippet,
                line_start=r.line_start,
                fused_score=r.fused_score,
                sources=r.sources,
                metadata=r.metadata,
            )
        )

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Search completed",
        user_id=user_id,
        api_key_id=api_key.id,
        project_id=project_id,
        query=body.query[:50],
        result_count=len(search_results),
    )

    return SearchResponse(
        query=body.query,
        results=search_results,
        result_count=len(search_results),
    )
