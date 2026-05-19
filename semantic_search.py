"""
Semantic search across multiple Pinecone indexes (jobs, news, products).

Given a natural-language query, this module embeds the query using OpenAI and
queries the specified Pinecone indexes to find the most relevant microsite
companies.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from config import settings
from models import SemanticSearchHit, SemanticSearchRequest

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_pc_client = None
_pc_indexes: Dict[str, Any] = {}
_openai_client = None

VALID_INDEXES = {"jobs", "news", "products"}

# Mapping from logical index name to the config setting
INDEX_NAME_MAP = {
    "jobs": settings.PINECONE_INDEX_JOBS,
    "news": settings.PINECONE_INDEX_NEWS,
    "products": settings.PINECONE_INDEX_PRODUCTS,
}


def _get_pinecone_client():
    """Return a cached Pinecone client."""
    global _pc_client
    if _pc_client is not None:
        return _pc_client

    with _lock:
        if _pc_client is not None:
            return _pc_client
        try:
            from pinecone import Pinecone
        except ImportError as e:
            raise RuntimeError(
                "The 'pinecone' package is required. Install with: pip install pinecone"
            ) from e

        api_key = (settings.PINECONE_API_KEY or "").strip()
        if not api_key:
            raise RuntimeError("PINECONE_API_KEY is not configured")

        _pc_client = Pinecone(api_key=api_key)
        return _pc_client


def _get_pinecone_index(index_key: str):
    """Return a cached Pinecone index handle for the given logical index name."""
    global _pc_indexes
    if index_key in _pc_indexes:
        return _pc_indexes[index_key]

    with _lock:
        if index_key in _pc_indexes:
            return _pc_indexes[index_key]

        pc = _get_pinecone_client()
        real_index_name = INDEX_NAME_MAP[index_key]
        idx = pc.Index(real_index_name)
        _pc_indexes[index_key] = idx
        logger.info("Initialised Pinecone index '%s' (key=%s)", real_index_name, index_key)
        return idx


def _get_openai_client():
    """Return a cached OpenAI client for embedding."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    with _lock:
        if _openai_client is not None:
            return _openai_client
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "The 'openai' package is required. Install with: pip install openai"
            ) from e

        api_key = (settings.OPENAI_API_KEY or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        _openai_client = OpenAI(api_key=api_key)
        return _openai_client


def _embed_query(text: str) -> List[float]:
    """Embed the search query using OpenAI embeddings."""
    client = _get_openai_client()
    model = settings.OPENAI_EMBEDDING_MODEL
    kwargs: Dict[str, Any] = {"model": model, "input": text}
    if model.startswith("text-embedding-3"):
        kwargs["dimensions"] = settings.EMBEDDING_DIM

    resp = client.embeddings.create(**kwargs)
    return list(resp.data[0].embedding)


def _query_index(index_key: str, vector: List[float], top_k: int) -> List[SemanticSearchHit]:
    """Query a single Pinecone index and return hits."""
    idx = _get_pinecone_index(index_key)
    results = idx.query(
        vector=vector,
        top_k=top_k,
        include_metadata=True,
        namespace=settings.PINECONE_NAMESPACE or "",
    )

    hits: List[SemanticSearchHit] = []
    for match in results.get("matches", []):
        hits.append(
            SemanticSearchHit(
                id=match["id"],
                score=match["score"],
                index=index_key,
                metadata=match.get("metadata"),
            )
        )
    return hits


def search(request: SemanticSearchRequest) -> List[SemanticSearchHit]:
    """
    Perform semantic search across the requested Pinecone indexes.

    Args:
        request: SemanticSearchRequest with query, top_k, and optional index filter.

    Returns:
        Combined list of SemanticSearchHit sorted by score descending.
    """
    # Determine which indexes to query
    indexes_to_search = VALID_INDEXES
    if request.indexes:
        indexes_to_search = {idx for idx in request.indexes if idx in VALID_INDEXES}
        if not indexes_to_search:
            indexes_to_search = VALID_INDEXES

    # Embed the query
    vector = _embed_query(request.query)

    # Query each index and collect results
    all_hits: List[SemanticSearchHit] = []
    for index_key in indexes_to_search:
        try:
            hits = _query_index(index_key, vector, request.top_k)
            all_hits.extend(hits)
        except Exception as e:
            logger.warning("Failed to query index '%s': %s", index_key, e)

    # Sort by score descending
    all_hits.sort(key=lambda h: h.score, reverse=True)
    return all_hits
