"""
Load product category tree and facet definitions from the product catalog database.

Uses PRODUCT_CATALOG_DATABASE_URL (PostgreSQL). Table/column names match the
remote schema: ProductCategory, ProductFacetDefinition, ProductSubClassFacet.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import settings

_lock = threading.Lock()
_cached_engine: Optional[Engine] = None
_cached_snapshot: Optional[Dict[str, Any]] = None


def get_product_catalog_engine() -> Engine:
    global _cached_engine
    url = (settings.PRODUCT_CATALOG_DATABASE_URL or "").strip()
    if not url:
        raise RuntimeError(
            "PRODUCT_CATALOG_DATABASE_URL is not set. "
            "Set it to your PostgreSQL connection string for the product catalog."
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    with _lock:
        if _cached_engine is None:
            _cached_engine = create_engine(url, pool_pre_ping=True, pool_recycle=300)
        return _cached_engine


@dataclass
class CategoryRow:
    id: str
    name: str
    slug: Optional[str]
    parent_id: Optional[str]


class ProductCatalogRepository:
    """Reads ProductCategory tree and facets from the catalog DB."""

    def __init__(self, engine: Optional[Engine] = None):
        self._engine = engine or get_product_catalog_engine()

    def load_snapshot(self) -> Dict[str, Any]:
        """Load categories, children map, and subclass→facet rows (cached process-wide)."""
        global _cached_snapshot
        with _lock:
            if _cached_snapshot is not None:
                return _cached_snapshot

        rows = self._fetch_categories()
        by_id: Dict[str, CategoryRow] = {r.id: r for r in rows}
        children: Dict[str, List[str]] = {}
        for r in rows:
            if r.parent_id:
                children.setdefault(r.parent_id, []).append(r.id)

        facet_links = self._fetch_subclass_facets()
        common_facets = self._fetch_common_facets()
        snapshot = {
            "by_id": by_id,
            "children": children,
            "facet_links": facet_links,
            "common_facets": common_facets,
        }
        with _lock:
            _cached_snapshot = snapshot
        return snapshot

    def _fetch_categories(self) -> List[CategoryRow]:
        with self._engine.connect() as conn:
            result = conn.execute(
                text(
                    'SELECT id, name, slug, "parentId" FROM "ProductCategory"'
                )
            )
            return [
                CategoryRow(
                    id=row[0],
                    name=row[1],
                    slug=row[2],
                    parent_id=row[3],
                )
                for row in result
            ]

    def _fetch_subclass_facets(self) -> List[Dict[str, Any]]:
        with self._engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT sf."subClassId", sf."facetId", sf."sortOrder",
                           fd.key, fd.label, fd.slug, fd."valueType",
                           fd.unit, fd.description, fd.options
                    FROM "ProductSubClassFacet" sf
                    JOIN "ProductFacetDefinition" fd ON fd.id = sf."facetId"
                    ORDER BY sf."subClassId", sf."sortOrder", fd.label
                    """
                )
            )
            return [
                {
                    "sub_class_id": row[0],
                    "facet_id": row[1],
                    "sort_order": row[2],
                    "key": row[3],
                    "label": row[4],
                    "slug": row[5],
                    "value_type": row[6],
                    "unit": row[7],
                    "description": row[8],
                    "options": row[9],
                }
                for row in result
            ]

    def _fetch_common_facets(self) -> List[Dict[str, Any]]:
        """Global facets (ProductFacetDefinition.isCommon = TRUE) that apply to every product."""
        with self._engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT fd.id, fd.key, fd.label, fd.slug, fd."valueType",
                           fd.unit, fd.description, fd.options
                    FROM "ProductFacetDefinition" fd
                    WHERE fd."isCommon" = TRUE
                    ORDER BY fd.label
                    """
                )
            )
            return [
                {
                    "sub_class_id": None,
                    "facet_id": row[0],
                    "sort_order": 0,
                    "key": row[1],
                    "label": row[2],
                    "slug": row[3],
                    "value_type": row[4],
                    "unit": row[5],
                    "description": row[6],
                    "options": row[7],
                    "is_common": True,
                }
                for row in result
            ]

    def path_root_to_node(self, node_id: str) -> List[CategoryRow]:
        """Ancestors from root → … → node (inclusive)."""
        snap = self.load_snapshot()
        by_id: Dict[str, CategoryRow] = snap["by_id"]
        chain: List[CategoryRow] = []
        current: Optional[str] = node_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            row = by_id.get(current)
            if not row:
                break
            chain.append(row)
            current = row.parent_id
        chain.reverse()
        return chain

    def leaf_nodes(self) -> List[str]:
        """Category ids that have no children (typical classification targets)."""
        snap = self.load_snapshot()
        by_id = snap["by_id"]
        children = snap["children"]
        return [cid for cid in by_id if cid not in children]

    def leaf_breadcrumb_lines(self) -> List[str]:
        """Compact lines: `id | Super > Cat > Class > Sub` for LLM prompts."""
        lines: List[str] = []
        for leaf_id in sorted(self.leaf_nodes()):
            path = self.path_root_to_node(leaf_id)
            names = " > ".join(p.name for p in path)
            lines.append(f"{leaf_id} | {names}")
        return lines

    def four_layers_from_leaf(self, leaf_id: str) -> Dict[str, Optional[CategoryRow]]:
        """
        Map path root→leaf onto super_category, category, class_name, sub_class_name.
        Shorter paths leave trailing layers as None.
        """
        path = self.path_root_to_node(leaf_id)
        keys = ["super_category", "category", "class_name", "sub_class_name"]
        out: Dict[str, Optional[CategoryRow]] = {k: None for k in keys}
        for i, row in enumerate(path[:4]):
            out[keys[i]] = row
        return out

    def facet_definitions_for_subclass(self, sub_class_id: str) -> List[Dict[str, Any]]:
        """Facet definitions linked to this sub-class, ordered by sortOrder."""
        snap = self.load_snapshot()
        defs = [f for f in snap["facet_links"] if f["sub_class_id"] == sub_class_id]
        defs.sort(key=lambda x: (x["sort_order"], x["label"] or ""))
        return defs

    def common_facet_definitions(self) -> List[Dict[str, Any]]:
        """Global facets (ProductFacetDefinition.isCommon = TRUE) applied to all products."""
        snap = self.load_snapshot()
        return list(snap.get("common_facets") or [])

def clear_product_catalog_cache() -> None:
    """Test / admin hook."""
    global _cached_snapshot
    with _lock:
        _cached_snapshot = None
