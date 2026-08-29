"""Backward-compatible, lazy entry point for the vector search service."""

from .infra.vector_search_service import (
    LlamaIndexService,
    get_llama_index_service,
    schedule_index_delete,
    schedule_index_upsert,
)

__all__ = [
    "LlamaIndexService",
    "get_llama_index_service",
    "schedule_index_delete",
    "schedule_index_upsert",
]
