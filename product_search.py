"""
Facet-based product search backed by Pinecone.

Products are indexed in Pinecone with a flat metadata schema that looks like:
    - name              (str)          — product name
    - description       (str)
    - slug              (str)          — product URL slug
    - primaryImageUrl   (str)
    - currency          (str)
    - status            (str)          — e.g. "PUBLISHED"
    - isFeatured        (bool)
    - stockQuantity     (number)
    - subcategoryId     (str)          — sub-class / leaf category id
    - micrositeId       (str)
    - createdAt / updatedAt (str)
    - facet_<key>       (str|number|bool) — one metadata key per facet,
                         where <key> is the facet slug (e.g. "payload",
                         "engine-tier-powertrain", "retarder-brake-system")

Optional higher-level category fields (super_category_id, category_id,
class_id) are respected when present in metadata.

Free-text queries use one Pinecone integrated text search call against the
products index host, with synonym/meaning expansion folded into the query text
and facet filters passed to Pinecone. Semantic product queries can also use
Pinecone rerank over ordered fields, so product names can outrank descriptions
without losing the first-pass vector/semantic recall. When no free-text query
is supplied we fall back to a zero vector so the endpoint also works as a pure
facet filter.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import semantic_search
from config import settings
from models import (
    ProductCategoryRef,
    ProductFacetFilter,
    ProductFacetSearchRequest,
    ProductFacetValue,
    ProductSearchHit,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pc_client = None
_pc_index = None
_catalog_repo = None


# ---------------------------------------------------------------------------
# Client initialisation (lazy, cached)
# ---------------------------------------------------------------------------

def _get_pinecone_index():
    """Return a cached Pinecone index handle, creating the client on demand."""
    global _pc_client, _pc_index
    if _pc_index is not None:
        return _pc_index

    with _lock:
        if _pc_index is not None:
            return _pc_index
        try:
            from pinecone import Pinecone  # pinecone>=3.x
        except ImportError as e:  # pragma: no cover - surfaced at runtime
            raise RuntimeError(
                "The 'pinecone' package is required for product facet search. "
                "Install it with: pip install pinecone"
            ) from e

        api_key = (settings.PINECONE_API_KEY or "").strip()
        if not api_key:
            raise RuntimeError("PINECONE_API_KEY is not configured")

        _pc_client = Pinecone(api_key=api_key)
        if settings.PINECONE_INDEX_PRODUCTS_HOST:
            _pc_index = _pc_client.Index(host=settings.PINECONE_INDEX_PRODUCTS_HOST)
            logger.info("Initialised Pinecone products index host '%s'", settings.PINECONE_INDEX_PRODUCTS_HOST)
        else:
            _pc_index = _pc_client.Index(settings.PINECONE_INDEX_PRODUCTS)
            logger.info("Initialised Pinecone index '%s'", settings.PINECONE_INDEX_PRODUCTS)
        return _pc_index


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_query(text: str) -> List[float]:
    """Embed a free-text query using the configured OpenAI embedding model."""
    return semantic_search._embed_query(text)


# ---------------------------------------------------------------------------
# Filter construction
# ---------------------------------------------------------------------------

def _facet_metadata_keys(f: ProductFacetFilter) -> List[str]:
    """Possible Pinecone metadata keys for a facet filter (tries both id & key)."""
    keys: List[str] = []
    if f.facet_id:
        keys.append(f"facet_{f.facet_id}")
    if f.key:
        keys.append(f"facet_{f.key}")
    # De-duplicate while preserving order.
    seen: set[str] = set()
    uniq: List[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def _facet_filter_clause(f: ProductFacetFilter) -> Optional[Dict[str, Any]]:
    """Convert one ProductFacetFilter into a Pinecone filter fragment."""
    keys = _facet_metadata_keys(f)
    if not keys:
        return None

    # Build the per-key clause based on the provided constraint.
    def _clause_for(value_op: Dict[str, Any]) -> Dict[str, Any]:
        if len(keys) == 1:
            return {keys[0]: value_op}
        # Try both id-based and key-based fields ($or across them).
        return {"$or": [{k: value_op} for k in keys]}

    if f.values is not None and len(f.values) > 0:
        return _clause_for({"$in": list(f.values)})

    if f.value is not None:
        return _clause_for({"$eq": f.value})

    if f.min is not None or f.max is not None:
        op: Dict[str, Any] = {}
        if f.min is not None:
            op["$gte"] = f.min
        if f.max is not None:
            op["$lte"] = f.max
        return _clause_for(op)

    return None


def build_pinecone_filter(req: ProductFacetSearchRequest) -> Optional[Dict[str, Any]]:
    """Build a Pinecone metadata filter from a facet search request.

    The sub-class / leaf category is stored under ``subcategoryId`` in the
    actual Pinecone index. Higher-level category fields are still honoured
    in case they are indexed.
    """
    clauses: List[Dict[str, Any]] = []

    if req.super_category_id:
        clauses.append({"super_category_id": {"$eq": req.super_category_id}})
    if req.category_id:
        clauses.append({"category_id": {"$eq": req.category_id}})
    if req.class_id:
        clauses.append({"class_id": {"$eq": req.class_id}})
    if req.sub_class_id:
        # Real metadata key is `subcategoryId`; accept either for flexibility.
        clauses.append(
            {"$or": [
                {"subcategoryId": {"$eq": req.sub_class_id}},
                {"sub_class_id": {"$eq": req.sub_class_id}},
            ]}
        )

    for facet in req.facets or []:
        clause = _facet_filter_clause(facet)
        if clause:
            clauses.append(clause)

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


# ---------------------------------------------------------------------------
# Result hydration
# ---------------------------------------------------------------------------

def _first_meta(meta: Dict[str, Any], *keys: str) -> Any:
    """Return the first non-empty metadata value for a set of possible keys."""
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return value
    return None


def _camel(prefix: str, suffix: str) -> str:
    parts = prefix.split("_")
    base = parts[0] + "".join(part.title() for part in parts[1:])
    return f"{base}{suffix}"


def _category_ref(meta: Dict[str, Any], prefix: str) -> Optional[ProductCategoryRef]:
    """Pull a ProductCategoryRef from common snake_case/camelCase metadata keys."""
    id_keys = [f"{prefix}_id", _camel(prefix, "Id")]
    name_keys = [f"{prefix}_name", _camel(prefix, "Name")]
    slug_keys = [f"{prefix}_slug", _camel(prefix, "Slug")]

    if prefix == "super_category":
        id_keys.extend(["cluster_id", "clusterId", "superCategoryId"])
        name_keys.extend(["cluster_name", "clusterName", "superCategoryName"])
        slug_keys.extend(["cluster_slug", "clusterSlug", "superCategorySlug"])
    elif prefix == "class":
        id_keys.append("classId")
        name_keys.append("className")
        slug_keys.append("classSlug")

    cid = _first_meta(meta, *id_keys)
    name = _first_meta(meta, *name_keys)
    slug = _first_meta(meta, *slug_keys)
    if not cid and not name:
        return None
    return ProductCategoryRef(
        id=str(cid) if cid is not None else "",
        name=str(name) if name is not None else "",
        slug=slug,
    )


def _extract_facets(meta: Dict[str, Any]) -> List[ProductFacetValue]:
    """Reconstruct ProductFacetValue objects from flat `facet_*` metadata keys."""
    out: List[ProductFacetValue] = []
    raw_facets = meta.get("facets")
    if isinstance(raw_facets, list):
        for item in raw_facets:
            if not isinstance(item, dict):
                continue
            facet_id = item.get("facet_id") or item.get("facetId") or item.get("id")
            key = item.get("key") or item.get("slug") or facet_id
            value = item.get("value")
            if facet_id or key or value is not None:
                out.append(
                    ProductFacetValue(
                        facet_id=str(facet_id or key or ""),
                        key=str(key) if key is not None else None,
                        label=item.get("label") or item.get("name"),
                        value=value,
                        value_type=item.get("value_type") or item.get("valueType"),
                        sort_order=item.get("sort_order") or item.get("sortOrder"),
                    )
                )

    for k, v in meta.items():
        if not isinstance(k, str) or not k.startswith("facet_"):
            continue
        identifier = k[len("facet_"):]
        if not identifier:
            continue
        if identifier.endswith(("_label", "_type", "_sort_order", "_sortOrder")):
            continue
        # Pinecone metadata values are str / number / bool / list[str].
        value: Any = v
        if isinstance(v, list):
            # Facet value stored as list — join for display; callers can also
            # inspect the raw metadata dict.
            value = ", ".join(str(x) for x in v)
        elif isinstance(v, bool):
            value = str(v)
        out.append(
            ProductFacetValue(
                facet_id=identifier,
                key=identifier,
                label=meta.get(f"facet_{identifier}_label"),
                value=value,
                value_type=meta.get(f"facet_{identifier}_type"),
            )
        )
    return out


def _sub_class_ref(meta: Dict[str, Any]) -> Optional[ProductCategoryRef]:
    """Build a sub-class ProductCategoryRef from `subcategoryId` / `sub_class_*`."""
    cid = _first_meta(meta, "sub_class_id", "subClassId", "subcategoryId", "sub_category_id")
    name = _first_meta(
        meta,
        "sub_class_name",
        "subClassName",
        "subcategoryName",
        "sub_category_name",
    )
    slug = _first_meta(meta, "sub_class_slug", "subClassSlug", "subcategorySlug", "sub_category_slug")
    if not cid and not name:
        return None
    return ProductCategoryRef(
        id=str(cid) if cid is not None else "",
        name=str(name) if name is not None else "",
        slug=slug,
    )


def _row_to_category_ref(row: Any) -> Optional[ProductCategoryRef]:
    if not row:
        return None
    return ProductCategoryRef(id=row.id, name=row.name, slug=row.slug)


def _get_catalog_repo() -> Optional[Any]:
    """Return catalog repo if DB config is available; search still works without it."""
    global _catalog_repo
    if _catalog_repo is not None:
        return _catalog_repo
    try:
        from product_catalog import ProductCatalogRepository

        _catalog_repo = ProductCatalogRepository()
    except (ImportError, RuntimeError) as e:
        logger.info("Product catalog unavailable for category hydration: %s", e)
        return None
    return _catalog_repo


def _category_refs_from_catalog(meta: Dict[str, Any]) -> Dict[str, Optional[ProductCategoryRef]]:
    leaf_id = _first_meta(meta, "sub_class_id", "subClassId", "subcategoryId", "sub_category_id")
    if not leaf_id:
        return {}
    repo = _get_catalog_repo()
    if not repo:
        return {}
    try:
        layers = repo.four_layers_from_leaf(str(leaf_id))
    except Exception as e:
        logger.info("Failed to hydrate product category path for %s: %s", leaf_id, e)
        return {}
    return {
        "cluster": _row_to_category_ref(layers.get("super_category")),
        "category": _row_to_category_ref(layers.get("category")),
        "class": _row_to_category_ref(layers.get("class_name")),
        "sub_class": _row_to_category_ref(layers.get("sub_class_name")),
    }


def _image_urls(meta: Dict[str, Any]) -> Optional[List[str]]:
    """Normalise image URLs — index uses `primaryImageUrl` (singular string)."""
    raw_list = meta.get("image_urls")
    if isinstance(raw_list, list) and raw_list:
        return [str(x) for x in raw_list]
    primary = meta.get("primaryImageUrl")
    if isinstance(primary, str) and primary:
        return [primary]
    return None


def _to_hit(match: Any) -> ProductSearchHit:
    """Convert a Pinecone match object / dict into a ProductSearchHit."""
    if hasattr(match, "to_dict"):
        m = match.to_dict()
    elif isinstance(match, dict):
        m = match
    else:  # pragma: no cover - defensive
        m = {"id": getattr(match, "id", ""), "score": getattr(match, "score", 0.0)}

    meta: Dict[str, Any] = m.get("metadata") or {}
    catalog_refs = _category_refs_from_catalog(meta)
    sub_class_ref = _sub_class_ref(meta)
    if (
        catalog_refs.get("sub_class")
        and (sub_class_ref is None or not sub_class_ref.name)
    ):
        sub_class_ref = catalog_refs.get("sub_class")
    return ProductSearchHit(
        id=str(m.get("id", "")),
        score=float(m.get("score", 0.0) or 0.0),
        title=meta.get("title") or meta.get("name"),
        description=meta.get("description"),
        product_url=meta.get("product_url") or meta.get("url") or meta.get("slug"),
        image_urls=_image_urls(meta),
        cluster=_category_ref(meta, "super_category") or catalog_refs.get("cluster"),
        category=_category_ref(meta, "category") or catalog_refs.get("category"),
        class_=_category_ref(meta, "class") or catalog_refs.get("class"),
        sub_class=sub_class_ref,
        facets=_extract_facets(meta) or None,
        metadata=meta,
    )


def _semantic_hit_to_product_hit(hit: Any) -> ProductSearchHit:
    """Convert a semantic-search hit into the product search response shape."""
    return _to_hit(
        {
            "id": hit.id,
            "score": hit.score,
            "metadata": hit.metadata or {},
        }
    )


def _query_product_index(
    vector: List[float],
    top_k: int,
    pc_filter: Optional[Dict[str, Any]],
    namespace: Optional[str],
    include_metadata: bool,
) -> List[Any]:
    """Query the products index and return hits in semantic-search shape."""
    index = _get_pinecone_index()
    query_kwargs: Dict[str, Any] = {
        "vector": vector,
        "top_k": top_k,
        "include_metadata": include_metadata,
        "include_values": False,
    }
    if pc_filter:
        query_kwargs["filter"] = pc_filter
    if namespace:
        query_kwargs["namespace"] = namespace

    response = index.query(**query_kwargs)

    hits: List[Any] = []
    for raw_match in semantic_search._matches_from_response(response):
        match = semantic_search._match_to_dict(raw_match)
        hits.append(
            semantic_search.SemanticSearchHit(
                id=str(match.get("id", "")),
                score=float(match.get("score", 0.0) or 0.0),
                index="products",
                metadata=match.get("metadata"),
            )
        )
    return hits


def _search_hit_to_dict(hit: Any) -> Dict[str, Any]:
    if hasattr(hit, "to_dict"):
        return hit.to_dict()
    if isinstance(hit, dict):
        return hit
    fields = getattr(hit, "fields", None)
    return {
        "_id": getattr(hit, "_id", getattr(hit, "id", "")),
        "_score": getattr(hit, "_score", getattr(hit, "score", 0.0)),
        "fields": fields,
    }


def _search_hits_from_response(response: Any) -> List[Any]:
    if hasattr(response, "to_dict"):
        response = response.to_dict()
    if isinstance(response, dict):
        result = response.get("result") or {}
        return list(result.get("hits") or response.get("hits") or [])
    result = getattr(response, "result", None)
    if isinstance(result, dict):
        return list(result.get("hits") or [])
    return list(getattr(result, "hits", []) or getattr(response, "hits", []) or [])


def _pinecone_rerank_error(exc: Exception) -> bool:
    """Return true for errors raised by Pinecone's optional rerank stage."""
    message = str(exc).lower()
    return "rerank" in message


def _semantic_hits_from_search_response(response: Any) -> List[Any]:
    """Convert Pinecone integrated-search response hits into semantic hit shape."""
    hits: List[Any] = []
    for raw_hit in _search_hits_from_response(response):
        hit = _search_hit_to_dict(raw_hit)
        metadata = hit.get("fields") or hit.get("metadata") or {}
        hits.append(
            semantic_search.SemanticSearchHit(
                id=str(hit.get("_id") or hit.get("id") or ""),
                score=float(hit.get("_score", hit.get("score", 0.0)) or 0.0),
                index="products",
                metadata=metadata,
            )
        )
    return hits


def _search_product_index(
    text: str,
    top_k: int,
    pc_filter: Optional[Dict[str, Any]],
    namespace: Optional[str],
    *,
    rerank: bool = False,
    rank_fields: Optional[List[str]] = None,
    rerank_top_n: Optional[int] = None,
) -> Tuple[List[Any], bool]:
    """Search the integrated-embedding products index with query text."""
    index = _get_pinecone_index()
    query: Dict[str, Any] = {
        "inputs": {"text": text},
        "top_k": top_k,
    }
    if pc_filter:
        query["filter"] = pc_filter

    search_kwargs: Dict[str, Any] = {
        "namespace": namespace or settings.PINECONE_PRODUCTS_NAMESPACE,
        "query": query,
    }
    effective_rank_fields = [field for field in (rank_fields or []) if field]
    if settings.PRODUCT_SEARCH_FIELDS:
        fields = list(settings.PRODUCT_SEARCH_FIELDS)
        for field in effective_rank_fields:
            if field not in fields:
                fields.append(field)
        search_kwargs["fields"] = fields
    if rerank and effective_rank_fields:
        search_kwargs["rerank"] = {
            "model": settings.PRODUCT_SEARCH_FIELD_RERANK_MODEL,
            "rank_fields": effective_rank_fields,
            "top_n": rerank_top_n or top_k,
        }

    try:
        response = index.search(**search_kwargs)
        return _semantic_hits_from_search_response(response), bool(rerank and effective_rank_fields)
    except Exception as e:
        if not (rerank and _pinecone_rerank_error(e)):
            raise
        fallback_kwargs = dict(search_kwargs)
        fallback_kwargs.pop("rerank", None)
        logger.warning(
            "Pinecone product rerank failed for rank_fields=%s; "
            "retrying semantic search without rerank. Error: %s",
            effective_rank_fields,
            e,
        )
        response = index.search(**fallback_kwargs)
        return _semantic_hits_from_search_response(response), False


def _product_query_variants(query: str) -> List[str]:
    """Build product query variants using the shared semantic expansion logic."""
    variants = semantic_search._deterministic_query_variants(query)
    llm_variants: List[str] = []
    if settings.PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED:
        llm_variants = semantic_search._llm_query_variants(query)

    if _is_skid_steer_query(query):
        variants = [
            query,
            "skid steer loader",
            "skid steer",
            "compact loader",
            *llm_variants,
            "John Deere 330G skid steer",
            "John Deere 332G skid steer",
            "330G 332G skid steer loader",
            "products in Skid Steer Loaders category",
            *variants,
        ]
    elif _is_adt_query(query):
        variants = [
            query,
            "ADT",
            "articulated dump truck",
            "articulated haul truck",
            "John Deere ADT",
            "John Deere 460E articulated dump truck",
            "products in Articulated Dump Trucks category",
            *llm_variants,
            *variants,
        ]
    else:
        variants.extend(llm_variants)

    max_variants = max(1, settings.PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS)
    return semantic_search._dedupe_texts(variants)[:max_variants]


def _product_query_text(query: str) -> Tuple[str, List[str]]:
    """Build one integrated-search text input and expose phrases for debug."""
    variants = _product_query_variants(query)
    return " | ".join(variants), variants


def _is_skid_steer_query(query: str) -> bool:
    """Return true for skid steer or Bobcat/common-name/typo queries."""
    return bool(
        semantic_search.DOMAIN_SYNONYM_RULES[0][0].search(query or "")
        or re.search(r"\bskid\s*steers?\b", query or "", re.IGNORECASE)
    )


def _is_adt_query(query: str) -> bool:
    """Return true for articulated dump truck / ADT queries."""
    return bool(
        re.search(r"\badt\b", query or "", re.IGNORECASE)
        or re.search(r"\barticulated\s+(?:dump|haul)\s+trucks?\b", query or "", re.IGNORECASE)
    )


def _product_rerank_hits(query: str, hits: List[Any]) -> List[Any]:
    """Apply the shared semantic reranker over a bounded product candidate set."""
    if not settings.PRODUCT_SEMANTIC_RERANK_ENABLED:
        return list(hits)

    max_candidates = max(1, settings.PRODUCT_SEMANTIC_RERANK_MAX_CANDIDATES)
    candidates = list(hits)[:max_candidates]
    tail = list(hits)[max_candidates:]
    reranked = semantic_search._rerank_hits(query, candidates)

    # Keep non-reranked tail candidates behind judged candidates while
    # preserving their fused vector order. This matches the shared rerank
    # behavior for extra candidates without sending a larger LLM payload.
    for offset, hit in enumerate(tail, start=len(reranked) + 1):
        hit.score = min(float(hit.score), 0.2) - (offset * 0.0001)

    combined = [*reranked, *tail]
    combined.sort(key=lambda h: h.score, reverse=True)
    return combined


def _product_field_rerank_enabled(req: ProductFacetSearchRequest) -> bool:
    """Return whether Pinecone field rerank should run for this request."""
    if req.field_rerank is not None:
        return bool(req.field_rerank)
    return bool(settings.PRODUCT_SEARCH_FIELD_RERANK_ENABLED)


def _product_rank_fields(req: ProductFacetSearchRequest) -> List[str]:
    """Return ordered rerank fields, with duplicates and blanks removed."""
    configured = req.rank_fields
    if configured is None:
        configured = settings.PRODUCT_SEARCH_FIELD_RERANK_FIELDS
    fields: List[str] = []
    seen: set[str] = set()
    for field in configured or []:
        value = str(field).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        fields.append(value)
    return fields


def _product_candidate_top_k(req: ProductFacetSearchRequest, *, field_rerank: bool) -> int:
    """Fetch enough semantic candidates for rerank without overloading latency."""
    if not field_rerank:
        return req.top_k
    multiplier = max(1, settings.PRODUCT_SEARCH_FIELD_RERANK_CANDIDATE_MULTIPLIER)
    return min(200, max(req.top_k, req.top_k * multiplier))


def _score_threshold(req: ProductFacetSearchRequest, *, semantic: bool) -> Optional[float]:
    """Return the score threshold for this request."""
    if req.min_score is not None:
        return req.min_score
    if semantic:
        return settings.PRODUCT_SEARCH_MIN_SCORE
    return None


def _apply_min_score(hits: List[ProductSearchHit], threshold: Optional[float]) -> List[ProductSearchHit]:
    """Keep only hits whose score is strictly greater than the threshold."""
    if threshold is None:
        return hits
    return [h for h in hits if h.score > threshold]


def _score_text(hit: ProductSearchHit) -> str:
    """Return searchable product text used only for score calibration evidence."""
    parts = [
        hit.title,
        hit.description,
        hit.cluster.name if hit.cluster else None,
        hit.category.name if hit.category else None,
        hit.class_.name if hit.class_ else None,
        hit.sub_class.name if hit.sub_class else None,
    ]
    if hit.facets:
        for facet in hit.facets:
            parts.extend([facet.label, facet.key, str(facet.value) if facet.value is not None else None])
    return " ".join(str(part) for part in parts if part)


def _normalized_text(text: Optional[str]) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalized_text(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {text} "


def _contains_token(text: str, token: str) -> bool:
    normalized_token = _normalized_text(token)
    return bool(normalized_token and re.search(rf"\b{re.escape(normalized_token)}\b", text))


def _query_tokens(query: str) -> List[str]:
    cleaned = semantic_search._clean_query_intent(query)
    stopwords = {"a", "an", "and", "for", "i", "me", "of", "the", "to", "with"}
    return [
        token
        for token in re.findall(r"[a-z0-9]+", cleaned.lower())
        if token not in stopwords and len(token) >= 2
    ]


def _calibrated_product_score(
    query: str,
    hit: ProductSearchHit,
    *,
    raw_score: float,
) -> float:
    """Raise low Pinecone/rerank scores when product-domain evidence is clear."""
    title_text = _normalized_text(hit.title)
    desc_text = _normalized_text(hit.description)
    category_text = _normalized_text(
        " ".join(
            part
            for part in [
                hit.cluster.name if hit.cluster else "",
                hit.category.name if hit.category else "",
                hit.class_.name if hit.class_ else "",
                hit.sub_class.name if hit.sub_class else "",
            ]
            if part
        )
    )
    all_text = _normalized_text(_score_text(hit))
    cleaned_query = semantic_search._clean_query_intent(query)
    cleaned_query_norm = _normalized_text(cleaned_query)
    score = max(0.0, min(0.99, float(raw_score or 0.0)))

    if _is_skid_steer_query(query):
        if _contains_phrase(title_text, "skid steer"):
            score = max(score, 0.94)
        elif _contains_phrase(category_text, "skid steer"):
            score = max(score, 0.90)
        elif _contains_phrase(desc_text, "skid steer") or _contains_phrase(all_text, "compact loader"):
            score = max(score, 0.86)

    if _is_adt_query(query):
        if _contains_token(title_text, "adt"):
            score = max(score, 0.95)
        elif _contains_phrase(title_text, "articulated dump truck") or _contains_phrase(title_text, "articulated haul truck"):
            score = max(score, 0.92)
        elif _contains_phrase(category_text, "articulated dump truck") or _contains_phrase(desc_text, "articulated dump truck"):
            score = max(score, 0.88)

    if cleaned_query_norm:
        if _contains_phrase(title_text, cleaned_query_norm):
            score = max(score, 0.93)
        elif _contains_phrase(category_text, cleaned_query_norm):
            score = max(score, 0.86)
        elif _contains_phrase(desc_text, cleaned_query_norm):
            score = max(score, 0.80)

    tokens = _query_tokens(query)
    if tokens:
        title_matches = sum(1 for token in tokens if _contains_token(title_text, token))
        all_matches = sum(1 for token in tokens if _contains_token(all_text, token))
        if title_matches == len(tokens):
            score = max(score, 0.90 if len(tokens) > 1 else 0.84)
        elif all_matches == len(tokens):
            score = max(score, 0.78)

    return round(min(score, 0.99), 4)


def _calibrate_product_scores(query: str, hits: List[ProductSearchHit]) -> List[ProductSearchHit]:
    """Apply product-specific score calibration while preserving metadata."""
    for hit in hits:
        raw_score = float(hit.score or 0.0)
        hit.score = _calibrated_product_score(query, hit, raw_score=raw_score)
    return sorted(hits, key=lambda h: h.score, reverse=True)


def _semantic_product_search(
    req: ProductFacetSearchRequest,
    pc_filter: Optional[Dict[str, Any]],
    namespace: Optional[str],
) -> Tuple[List[ProductSearchHit], Dict[str, Any]]:
    """Run expanded semantic search over products while preserving facet filters."""
    query = (req.query or "").strip()
    query_text, query_variants = _product_query_text(query)
    rank_fields = _product_rank_fields(req)
    pinecone_field_rerank = _product_field_rerank_enabled(req) and bool(rank_fields)
    candidate_top_k = _product_candidate_top_k(req, field_rerank=pinecone_field_rerank)
    raw_hits, pinecone_field_rerank_applied = _search_product_index(
        text=query_text,
        top_k=candidate_top_k,
        pc_filter=pc_filter,
        namespace=namespace,
        rerank=pinecone_field_rerank,
        rank_fields=rank_fields,
        rerank_top_n=candidate_top_k,
    )
    reranked_hits = _product_rerank_hits(query, raw_hits)
    hits = [_semantic_hit_to_product_hit(hit) for hit in reranked_hits]
    hits = _calibrate_product_scores(query, hits)
    score_threshold = _score_threshold(req, semantic=True)
    hits = _apply_min_score(hits, score_threshold)
    hits = hits[:req.top_k]

    debug = {
        "filter": pc_filter,
        "namespace": namespace,
        "returned": len(hits),
        "query_text": query_text,
        "query_variants": query_variants,
        "candidate_top_k": candidate_top_k,
        "rerank_top_n": candidate_top_k if pinecone_field_rerank else None,
        "pinecone_field_rerank": pinecone_field_rerank_applied,
        "pinecone_field_rerank_requested": pinecone_field_rerank,
        "rank_fields": rank_fields if pinecone_field_rerank_applied else [],
        "pinecone_field_rerank_model": (
            settings.PRODUCT_SEARCH_FIELD_RERANK_MODEL if pinecone_field_rerank else None
        ),
        "llm_expansion": settings.PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED,
        "llm_rerank": settings.PRODUCT_SEMANTIC_RERANK_ENABLED,
        "pinecone_workers": 1,
        "min_score": score_threshold,
        "semantic": True,
    }
    return hits, debug


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def search_products(req: ProductFacetSearchRequest) -> Tuple[List[ProductSearchHit], Dict[str, Any]]:
    """Run a facet + (optional) semantic search against the Pinecone index.

    Returns (hits, debug_info).
    """
    pc_filter = build_pinecone_filter(req)
    namespace = req.namespace if req.namespace is not None else settings.PINECONE_PRODUCTS_NAMESPACE

    if req.query and req.query.strip():
        return _semantic_product_search(req, pc_filter, namespace)
    else:
        # Pure metadata search: Pinecone still requires a vector, so use a
        # zero vector. Scores will be uninformative, and we rely on the
        # metadata filter to return the correct products.
        vector = [0.0] * settings.EMBEDDING_DIM

    index = _get_pinecone_index()
    query_kwargs: Dict[str, Any] = {
        "vector": vector,
        "top_k": req.top_k,
        "include_metadata": req.include_metadata,
        "include_values": False,
    }
    if pc_filter:
        query_kwargs["filter"] = pc_filter
    if namespace:
        query_kwargs["namespace"] = namespace

    logger.info(
        "Pinecone query: top_k=%s, has_query=%s, filter=%s, namespace=%r",
        req.top_k,
        bool(req.query),
        pc_filter,
        namespace,
    )

    response = index.query(**query_kwargs)

    matches = getattr(response, "matches", None)
    if matches is None and isinstance(response, dict):
        matches = response.get("matches", [])
    matches = matches or []

    hits = [_to_hit(m) for m in matches]

    score_threshold = _score_threshold(req, semantic=False)
    hits = _apply_min_score(hits, score_threshold)

    debug = {
        "filter": pc_filter,
        "namespace": namespace,
        "returned": len(hits),
        "min_score": score_threshold,
        "semantic": False,
    }
    return hits, debug
