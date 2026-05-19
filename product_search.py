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

Query embeddings are generated with OpenAI (default: text-embedding-3-small
→ 1536 dims, matching ``EMBEDDING_DIM``). When no free-text query is supplied
we fall back to a zero vector so the endpoint also works as a pure facet
filter.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

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
_openai_client = None


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
        _pc_index = _pc_client.Index(settings.PINECONE_INDEX_NAME)
        logger.info("Initialised Pinecone index '%s'", settings.PINECONE_INDEX_NAME)
        return _pc_index


def _get_openai_client():
    """Return a cached OpenAI client used for embedding queries."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    with _lock:
        if _openai_client is not None:
            return _openai_client
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "The 'openai' package is required for query embedding. "
                "Install it with: pip install openai"
            ) from e

        api_key = (settings.OPENAI_API_KEY or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        _openai_client = OpenAI(api_key=api_key)
        return _openai_client


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_query(text: str) -> List[float]:
    """Embed a free-text query using the configured OpenAI embedding model."""
    client = _get_openai_client()
    model = settings.OPENAI_EMBEDDING_MODEL
    # Only text-embedding-3-* models accept a `dimensions` parameter.
    # text-embedding-ada-002 does not support it.
    kwargs: Dict[str, Any] = {"model": model, "input": text}
    if model.startswith("text-embedding-3"):
        kwargs["dimensions"] = settings.EMBEDDING_DIM

    resp = client.embeddings.create(**kwargs)
    return list(resp.data[0].embedding)


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

def _category_ref(meta: Dict[str, Any], prefix: str) -> Optional[ProductCategoryRef]:
    """Pull a ProductCategoryRef out of flat metadata keys like `<prefix>_id`."""
    cid = meta.get(f"{prefix}_id")
    name = meta.get(f"{prefix}_name")
    if not cid and not name:
        return None
    return ProductCategoryRef(
        id=str(cid) if cid is not None else "",
        name=str(name) if name is not None else "",
        slug=meta.get(f"{prefix}_slug"),
    )


def _extract_facets(meta: Dict[str, Any]) -> List[ProductFacetValue]:
    """Reconstruct ProductFacetValue objects from flat `facet_*` metadata keys."""
    out: List[ProductFacetValue] = []
    for k, v in meta.items():
        if not isinstance(k, str) or not k.startswith("facet_"):
            continue
        identifier = k[len("facet_"):]
        if not identifier:
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
    cid = meta.get("sub_class_id") or meta.get("subcategoryId")
    name = meta.get("sub_class_name") or meta.get("subcategoryName")
    slug = meta.get("sub_class_slug") or meta.get("subcategorySlug")
    if not cid and not name:
        return None
    return ProductCategoryRef(
        id=str(cid) if cid is not None else "",
        name=str(name) if name is not None else "",
        slug=slug,
    )


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
    return ProductSearchHit(
        id=str(m.get("id", "")),
        score=float(m.get("score", 0.0) or 0.0),
        title=meta.get("title") or meta.get("name"),
        description=meta.get("description"),
        product_url=meta.get("product_url") or meta.get("url") or meta.get("slug"),
        image_urls=_image_urls(meta),
        cluster=_category_ref(meta, "super_category"),
        category=_category_ref(meta, "category"),
        class_=_category_ref(meta, "class"),
        sub_class=_sub_class_ref(meta),
        facets=_extract_facets(meta) or None,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def search_products(req: ProductFacetSearchRequest) -> Tuple[List[ProductSearchHit], Dict[str, Any]]:
    """Run a facet + (optional) semantic search against the Pinecone index.

    Returns (hits, debug_info).
    """
    index = _get_pinecone_index()

    if req.query and req.query.strip():
        vector = embed_query(req.query.strip())
    else:
        # Pure metadata search: Pinecone still requires a vector, so use a
        # zero vector. Scores will be uninformative, and we rely on the
        # metadata filter to return the correct products.
        vector = [0.0] * settings.EMBEDDING_DIM

    pc_filter = build_pinecone_filter(req)
    namespace = req.namespace if req.namespace is not None else settings.PINECONE_NAMESPACE

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

    if req.min_score is not None:
        hits = [h for h in hits if h.score >= req.min_score]

    debug = {"filter": pc_filter, "namespace": namespace, "returned": len(hits)}
    return hits, debug
