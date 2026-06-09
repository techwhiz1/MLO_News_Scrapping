"""
Semantic search across multiple Pinecone indexes (jobs, news, products).

Given a natural-language query, this module embeds the query using OpenAI and
queries the specified Pinecone indexes to find the most relevant microsite
companies.
"""
from __future__ import annotations

import logging
import json
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import settings
from models import SemanticSearchHit, SemanticSearchRequest

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_pc_client = None
_pc_indexes: Dict[str, Any] = {}
_openai_client = None

VALID_INDEXES = {"jobs", "news", "products"}
RRF_K = 60
MAX_METADATA_CHARS = 900
FILLER_QUERY_RE = re.compile(
    r"\b(?:please|pls|give me|show me|find me|looking for|i need|i want|can you|could you|search for)\b",
    re.IGNORECASE,
)

DOMAIN_SYNONYM_RULES: Sequence[Tuple[re.Pattern[str], Sequence[str]]] = (
    (
        re.compile(r"\b(?:bobcats?|botcats?|bobcasts?|bpbcats?)\b", re.IGNORECASE),
        (
            "skid steer loader",
            "skid steer",
            "compact track loader",
            "compact loader",
            "Bobcat style compact construction equipment",
        ),
    ),
    (
        re.compile(r"\b(?:telehandlers?|zoom\s*booms?)\b", re.IGNORECASE),
        ("telescopic handler", "rough terrain forklift", "material handler"),
    ),
    (
        re.compile(r"\b(?:haul\s*trucks?|rock\s*trucks?)\b", re.IGNORECASE),
        ("mining dump truck", "off highway truck", "rigid frame truck"),
    ),
    (
        re.compile(r"\b(?:excavators?|diggers?)\b", re.IGNORECASE),
        ("hydraulic excavator", "crawler excavator", "earthmoving equipment"),
    ),
    (
        re.compile(r"\b(?:loaders?|wheel\s*loaders?)\b", re.IGNORECASE),
        ("front end loader", "wheel loader", "material loading equipment"),
    ),
    (
        re.compile(r"\b(?:dozers?|bulldozers?)\b", re.IGNORECASE),
        ("crawler dozer", "track dozer", "earthmoving grading equipment"),
    ),
    (
        re.compile(r"\b(?:man\s*lifts?|boom\s*lifts?|cherry\s*pickers?)\b", re.IGNORECASE),
        ("aerial work platform", "articulating boom lift", "telescopic boom lift"),
    ),
)

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


def _embedding_kwargs(input_text: str | Sequence[str]) -> Dict[str, Any]:
    """Build OpenAI embedding kwargs for the configured model."""
    model = settings.OPENAI_EMBEDDING_MODEL
    kwargs: Dict[str, Any] = {"model": model, "input": input_text}
    if model.startswith("text-embedding-3"):
        kwargs["dimensions"] = settings.EMBEDDING_DIM
    return kwargs


def _embed_queries(texts: Sequence[str]) -> List[List[float]]:
    """Embed one or more query variants using OpenAI embeddings."""
    client = _get_openai_client()
    resp = client.embeddings.create(**_embedding_kwargs(list(texts)))
    ordered = sorted(resp.data, key=lambda item: getattr(item, "index", 0))
    return [list(item.embedding) for item in ordered]


def _embed_query(text: str) -> List[float]:
    """Embed the search query using OpenAI embeddings."""
    return _embed_queries([text])[0]


def _dedupe_texts(texts: Iterable[str]) -> List[str]:
    """Keep unique non-empty strings, preserving order."""
    seen: set[str] = set()
    out: List[str] = []
    for text in texts:
        normalized = " ".join((text or "").split())
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _clean_query_intent(query: str) -> str:
    """Remove command filler that can dilute embedding meaning."""
    cleaned = FILLER_QUERY_RE.sub(" ", query or "")
    cleaned = re.sub(r"[^\w\s+./-]", " ", cleaned)
    return " ".join(cleaned.split())


def _deterministic_query_variants(query: str) -> List[str]:
    """Add stable domain expansions for known brand/common-name aliases."""
    cleaned = _clean_query_intent(query)
    variants = [query, cleaned]
    expansion_terms: List[str] = []
    rewritten = cleaned or query

    for pattern, synonyms in DOMAIN_SYNONYM_RULES:
        if pattern.search(query):
            expansion_terms.extend(synonyms)
            rewritten = pattern.sub(" ".join(synonyms[:2]), rewritten)

    if expansion_terms:
        variants.append(rewritten)
        variants.append(f"{query} means {'; '.join(expansion_terms)}")
        variants.append(f"Find mining or construction products for {'; '.join(expansion_terms)}")
        variants.append(" ".join(expansion_terms))
    elif cleaned:
        variants.append(f"Find mining, construction, jobs, news, or products related to {cleaned}")

    return _dedupe_texts(variants)


def _extract_json_array(text: str) -> List[str]:
    """Parse a JSON array of strings from an LLM response."""
    raw = (text or "").strip()
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def _llm_query_variants(query: str) -> List[str]:
    """Use an LLM to rewrite a user query into semantic retrieval variants."""
    if not settings.SEMANTIC_QUERY_EXPANSION_ENABLED:
        return []

    client = _get_openai_client()
    model = settings.SEMANTIC_QUERY_EXPANSION_MODEL
    prompt = (
        "Rewrite this user search query for vector semantic retrieval across "
        "mining, construction, jobs, news, and product indexes.\n\n"
        "Return only a JSON array of 2 to 4 short search queries. Preserve the "
        "user's intent, fix obvious misspellings, and expand brands or common "
        "names into generic industry terms. Example: Bobcat or bobcats should "
        "include skid steer loader, compact track loader, and compact loader. "
        "Avoid generic instruction words like please, show, find, and give me.\n\n"
        f"User query: {query}"
    )

    try:
        if hasattr(client, "responses"):
            response = client.responses.create(
                model=model,
                input=prompt,
            )
            text = getattr(response, "output_text", "") or ""
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Return only a JSON array of semantic search query strings.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            text = response.choices[0].message.content or ""
    except Exception as e:
        logger.info("Semantic query LLM expansion failed; using deterministic variants: %s", e)
        return []

    return _extract_json_array(text)


def _query_variants(query: str) -> List[str]:
    """Build the final list of query strings to embed and search."""
    variants = _deterministic_query_variants(query)
    variants.extend(_llm_query_variants(query))
    max_variants = max(1, settings.SEMANTIC_MAX_QUERY_VARIANTS)
    return _dedupe_texts(variants)[:max_variants]


def _candidate_top_k(request_top_k: int) -> int:
    """Fetch extra candidates before fusion/reranking."""
    multiplier = max(1, settings.SEMANTIC_CANDIDATE_MULTIPLIER)
    return max(request_top_k, min(100, request_top_k * multiplier))


def _matches_from_response(response: Any) -> List[Any]:
    matches = getattr(response, "matches", None)
    if matches is None and isinstance(response, dict):
        matches = response.get("matches", [])
    return list(matches or [])


def _match_to_dict(match: Any) -> Dict[str, Any]:
    if hasattr(match, "to_dict"):
        return match.to_dict()
    if isinstance(match, dict):
        return match
    return {
        "id": getattr(match, "id", ""),
        "score": getattr(match, "score", 0.0),
        "metadata": getattr(match, "metadata", None),
    }


def _query_index(index_key: str, vector: List[float], top_k: int) -> List[SemanticSearchHit]:
    """Query a single Pinecone index and return hits."""
    idx = _get_pinecone_index(index_key)
    query_kwargs: Dict[str, Any] = {
        "vector": vector,
        "top_k": top_k,
        "include_metadata": True,
    }
    if settings.PINECONE_NAMESPACE:
        query_kwargs["namespace"] = settings.PINECONE_NAMESPACE

    results = idx.query(**query_kwargs)

    hits: List[SemanticSearchHit] = []
    for raw_match in _matches_from_response(results):
        match = _match_to_dict(raw_match)
        hits.append(
            SemanticSearchHit(
                id=str(match.get("id", "")),
                score=float(match.get("score", 0.0) or 0.0),
                index=index_key,
                metadata=match.get("metadata"),
            )
        )
    return hits


def _metadata_digest(metadata: Optional[Dict[str, Any]]) -> str:
    """Compact metadata into reranker-readable text."""
    if not metadata:
        return ""

    preferred_keys = (
        "title",
        "name",
        "jobTitle",
        "employerName",
        "companyName",
        "description",
        "short_description",
        "content",
        "category",
        "sub_class_name",
        "subcategoryName",
        "class_name",
        "url",
        "product_url",
    )
    parts: List[str] = []
    for key in preferred_keys:
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=True)
        text = " ".join(str(value).split())
        if text:
            parts.append(f"{key}: {text}")

    if not parts:
        for key, value in list(metadata.items())[:12]:
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=True)
            parts.append(f"{key}: {' '.join(str(value).split())}")

    digest = " | ".join(parts)
    return digest[:MAX_METADATA_CHARS]


def _extract_rerank_scores(text: str) -> Dict[Tuple[str, str], float]:
    """Parse reranker JSON into {(index, id): score}."""
    raw = (text or "").strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    rows = parsed.get("scores") if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return {}

    scores: Dict[Tuple[str, str], float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        hit_id = row.get("id")
        index = row.get("index")
        score = row.get("score")
        if hit_id is None or index is None or score is None:
            continue
        try:
            numeric = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            continue
        scores[(str(index), str(hit_id))] = numeric
    return scores


def _rerank_hits(query: str, hits: Sequence[SemanticSearchHit]) -> List[SemanticSearchHit]:
    """Use an LLM as a semantic reranker over vector candidates."""
    if not settings.SEMANTIC_RERANK_ENABLED or not hits:
        return list(hits)

    max_candidates = max(1, settings.SEMANTIC_RERANK_MAX_CANDIDATES)
    candidates = list(hits)[:max_candidates]
    payload = [
        {
            "index": hit.index,
            "id": hit.id,
            "vector_score": round(float(hit.score), 6),
            "text": _metadata_digest(hit.metadata),
        }
        for hit in candidates
    ]
    prompt = (
        "Rerank search candidates by semantic meaning, not literal word overlap.\n"
        "Score how well each candidate satisfies the user query from 0.0 to 1.0. "
        "Understand brand/common-name aliases and misspellings, for example "
        "Bobcat/bobcats/bobcasts/bpbcats can mean skid steer loader or compact loader.\n\n"
        "Return only JSON with this shape: "
        '{"scores":[{"index":"products","id":"candidate-id","score":0.92}]}.\n\n'
        f"User query: {query}\n\n"
        f"Candidates: {json.dumps(payload, ensure_ascii=True)}"
    )

    try:
        client = _get_openai_client()
        model = settings.SEMANTIC_RERANK_MODEL
        if hasattr(client, "responses"):
            response = client.responses.create(model=model, input=prompt)
            text = getattr(response, "output_text", "") or ""
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Return only valid JSON reranking scores.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            text = response.choices[0].message.content or ""
    except Exception as e:
        logger.info("Semantic rerank failed; using fused vector scores: %s", e)
        return list(hits)

    llm_scores = _extract_rerank_scores(text)
    if not llm_scores:
        return list(hits)

    weight = max(0.0, min(1.0, settings.SEMANTIC_RERANK_WEIGHT))
    reranked: List[SemanticSearchHit] = []
    for rank, hit in enumerate(hits, start=1):
        semantic_score = llm_scores.get((hit.index, hit.id))
        if semantic_score is None:
            # Keep extra non-reranked candidates behind judged candidates while
            # preserving their vector order.
            hit.score = min(float(hit.score), 0.2) - (rank * 0.0001)
        else:
            vector_score = max(0.0, min(1.0, float(hit.score)))
            hit.score = (weight * semantic_score) + ((1.0 - weight) * vector_score)
        reranked.append(hit)

    reranked.sort(key=lambda h: h.score, reverse=True)
    return reranked


def _fuse_hits(ranked_hit_lists: Sequence[Sequence[SemanticSearchHit]]) -> List[SemanticSearchHit]:
    """Deduplicate and combine rankings from multiple semantic query variants."""
    by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for hit_list in ranked_hit_lists:
        for rank, hit in enumerate(hit_list, start=1):
            key = (hit.index, hit.id)
            rrf_boost = 1.0 / (RRF_K + rank)
            existing = by_key.get(key)

            if existing is None:
                by_key[key] = {
                    "hit": hit,
                    "best_score": hit.score,
                    "rrf": rrf_boost,
                    "matches": 1,
                }
                continue

            existing["rrf"] += rrf_boost
            existing["matches"] += 1
            if hit.score > existing["best_score"]:
                existing["best_score"] = hit.score
                existing["hit"] = hit

    fused: List[SemanticSearchHit] = []
    for item in by_key.values():
        hit = item["hit"]
        # Preserve the Pinecone similarity score scale while giving a small,
        # bounded boost to results that rank well across expanded meanings.
        hit.score = float(item["best_score"]) + min(float(item["rrf"]), 0.1)
        fused.append(hit)

    fused.sort(key=lambda h: h.score, reverse=True)
    return fused


def _limit_per_index(hits: Sequence[SemanticSearchHit], top_k: int) -> List[SemanticSearchHit]:
    """Keep at most top_k results for each logical index, then globally rank."""
    counts: Dict[str, int] = {}
    limited: List[SemanticSearchHit] = []

    for hit in hits:
        count = counts.get(hit.index, 0)
        if count >= top_k:
            continue
        counts[hit.index] = count + 1
        limited.append(hit)

    limited.sort(key=lambda h: h.score, reverse=True)
    return limited


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

    query_variants = _query_variants(request.query)
    vectors = _embed_queries(query_variants)
    candidate_top_k = _candidate_top_k(request.top_k)

    # Query each index and collect results
    ranked_hit_lists: List[List[SemanticSearchHit]] = []
    for index_key in indexes_to_search:
        for variant, vector in zip(query_variants, vectors):
            try:
                hits = _query_index(index_key, vector, candidate_top_k)
                ranked_hit_lists.append(hits)
                logger.debug(
                    "Semantic variant query index=%s variant=%r returned=%s",
                    index_key,
                    variant,
                    len(hits),
                )
            except Exception as e:
                logger.warning("Failed to query index '%s': %s", index_key, e)

    fused_hits = _fuse_hits(ranked_hit_lists)
    reranked_hits = _rerank_hits(request.query, fused_hits)
    return _limit_per_index(reranked_hits, request.top_k)
