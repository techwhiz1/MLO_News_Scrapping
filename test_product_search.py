from models import ProductCategoryRef, ProductFacetFilter, ProductFacetSearchRequest, ProductSearchHit
import product_search
import semantic_search


class FakeCategoryRow:
    def __init__(self, id, name, slug):
        self.id = id
        self.name = name
        self.slug = slug


class FakeIndex:
    def __init__(self):
        self.calls = []
        self.search_calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        vector = kwargs["vector"]
        if vector == [0.1]:
            return {
                "matches": [
                    {
                        "id": "p1",
                        "score": 0.71,
                        "metadata": {"name": "Skid Steer", "description": "Compact loader"},
                    },
                    {
                        "id": "p2",
                        "score": 0.69,
                        "metadata": {"name": "Wheel Loader"},
                    },
                ]
            }
        if vector == [0.2]:
            return {
                "matches": [
                    {
                        "id": "p1",
                        "score": 0.68,
                        "metadata": {"name": "Skid Steer", "description": "Compact loader"},
                    }
                ]
            }
        return {"matches": []}

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        text = kwargs["query"]["inputs"]["text"]
        if (
            "bobcat" in text.lower()
            or "botcat" in text.lower()
            or "skid steer" in text.lower()
            or text == "query"
        ):
            return {
                "result": {
                    "hits": [
                        {
                            "_id": "p1",
                            "_score": 0.81,
                            "fields": {
                                "name": "John Deere 332G",
                                "description": "Skid Steer compact loader",
                                "superCategoryId": "cluster-1",
                                "superCategoryName": "Equipment",
                                "categoryId": "cat-1",
                                "categoryName": "Earthmoving",
                                "classId": "class-1",
                                "className": "Loaders",
                                "subcategoryId": "sub-1",
                                "subcategoryName": "Skid Steer Loaders",
                                "category_name": "Earthmoving",
                                "facet_condition": "New",
                                "facet_condition_label": "Condition",
                                "facet_condition_type": "select",
                            },
                        },
                        {
                            "_id": "p2",
                            "_score": 0.69,
                            "fields": {"name": "Wheel Loader"},
                        },
                    ]
                }
            }
        return {"result": {"hits": []}}


class FakeRerankMissingFieldIndex(FakeIndex):
    def search(self, **kwargs):
        if "rerank" in kwargs:
            self.search_calls.append(kwargs)
            raise Exception(
                "Rerank: Unable to rerank search results, a record "
                "(id: p2) is missing the specified keys in rank_fields"
            )
        return super().search(**kwargs)


def test_product_query_uses_semantic_variants_with_facet_filter(monkeypatch):
    fake_index = FakeIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", 10)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_CANDIDATE_MULTIPLIER", 1)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_PINECONE_WORKERS", 2)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.65)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", True)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_FIELDS", ["name"])
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_CANDIDATE_MULTIPLIER", 3)

    req = ProductFacetSearchRequest(
        query="please give me bobcats",
        top_k=2,
        facets=[ProductFacetFilter(key="condition", value="New")],
    )

    hits, debug = product_search.search_products(req)

    assert [hit.id for hit in hits] == ["p1", "p2"]
    assert hits[0].score > 0.71
    assert debug["semantic"] is True
    assert "skid steer" in " ".join(debug["query_variants"]).lower()
    assert "330g" in " ".join(debug["query_variants"]).lower()
    assert "332g" in " ".join(debug["query_variants"]).lower()
    assert len(fake_index.search_calls) == 1
    call = fake_index.search_calls[0]
    assert call["query"]["filter"] == {"facet_condition": {"$eq": "New"}}
    assert call["query"]["top_k"] == 6
    assert call["rerank"] == {
        "model": product_search.settings.PRODUCT_SEARCH_FIELD_RERANK_MODEL,
        "rank_fields": ["name"],
        "top_n": 6,
    }
    assert "skid steer" in call["query"]["inputs"]["text"].lower()
    assert call["namespace"] == product_search.settings.PINECONE_PRODUCTS_NAMESPACE
    assert "fields" not in call
    assert hits[0].cluster.name == "Equipment"
    assert hits[0].category.name == "Earthmoving"
    assert hits[0].class_.name == "Loaders"
    assert hits[0].sub_class.name == "Skid Steer Loaders"
    assert hits[0].facets[0].key == "condition"
    assert hits[0].facets[0].value == "New"
    assert debug["llm_expansion"] is False
    assert debug["llm_rerank"] is False
    assert debug["min_score"] == 0.65
    assert debug["pinecone_field_rerank"] is True
    assert debug["candidate_top_k"] == 6


def test_product_category_hydrates_from_catalog_when_only_leaf_id_present(monkeypatch):
    class FakeCatalogRepo:
        def four_layers_from_leaf(self, leaf_id):
            assert leaf_id == "sub-1"
            return {
                "super_category": FakeCategoryRow("cluster-1", "Equipment", "equipment"),
                "category": FakeCategoryRow("cat-1", "Earthmoving", "earthmoving"),
                "class_name": FakeCategoryRow("class-1", "Loaders", "loaders"),
                "sub_class_name": FakeCategoryRow("sub-1", "Skid Steer Loaders", "skid-steer-loaders"),
            }

    monkeypatch.setattr(product_search, "_catalog_repo", FakeCatalogRepo())

    hit = product_search._to_hit(
        {
            "id": "p1",
            "score": 0.9,
            "metadata": {
                "name": "John Deere 332G",
                "subcategoryId": "sub-1",
            },
        }
    )

    assert hit.cluster.name == "Equipment"
    assert hit.category.name == "Earthmoving"
    assert hit.class_.name == "Loaders"
    assert hit.sub_class.name == "Skid Steer Loaders"


def test_product_bobcat_variants_include_skid_steer_models(monkeypatch):
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", 10)

    variants = product_search._product_query_variants("I am looking for bobcats")
    joined = " ".join(variants).lower()

    assert "skid steer" in joined
    assert "330g" in joined
    assert "332g" in joined


def test_product_query_text_folds_synonyms_into_single_pinecone_input(monkeypatch):
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", 10)

    text, variants = product_search._product_query_text("I am looking for bobcats")

    assert "bobcats" in text.lower()
    assert "skid steer" in text.lower()
    assert "330g" in text.lower()
    assert "332g" in text.lower()
    assert len(variants) <= 10


def test_product_query_text_expands_adt_acronym(monkeypatch):
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", 10)

    text, variants = product_search._product_query_text("adt")

    assert "adt" in text.lower()
    assert "articulated dump truck" in text.lower()
    assert "john deere 460e" in text.lower()
    assert len(variants) <= 10


def test_product_score_calibration_boosts_skid_steer_synonym_matches():
    hit = ProductSearchHit(
        id="p1",
        score=0.25,
        title="John Deere 332G",
        description="Skid Steer compact loader",
        sub_class=ProductCategoryRef(id="sub-1", name="Skid Steer Loaders"),
    )

    scored = product_search._calibrate_product_scores("bobcat", [hit])

    assert scored[0].score >= 0.90


def test_product_score_calibration_boosts_exact_skid_steer_name_matches():
    hit = ProductSearchHit(
        id="p1",
        score=0.19,
        title="Skid Steer",
        description="Compact construction equipment",
    )

    scored = product_search._calibrate_product_scores("skid steer", [hit])

    assert scored[0].score >= 0.94


def test_product_score_calibration_boosts_exact_adt_title_matches():
    hit = ProductSearchHit(
        id="p1",
        score=0.23,
        title="John Deere ADT 460E",
        description="Articulated dump truck",
    )

    scored = product_search._calibrate_product_scores("adt", [hit])

    assert scored[0].score >= 0.95


def test_product_query_skips_llm_expansion_and_rerank_when_disabled(monkeypatch):
    fake_index = FakeIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", 1)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_CANDIDATE_MULTIPLIER", 1)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.65)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", False)
    monkeypatch.setattr(
        semantic_search,
        "_llm_query_variants",
        lambda query: (_ for _ in ()).throw(AssertionError("LLM expansion should not run")),
    )
    monkeypatch.setattr(
        semantic_search,
        "_rerank_hits",
        lambda query, hits: (_ for _ in ()).throw(AssertionError("LLM rerank should not run")),
    )

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(query="botcat", top_k=2)
    )

    assert hits
    assert debug["semantic"] is True
    assert debug["llm_expansion"] is False
    assert debug["llm_rerank"] is False


def test_product_query_uses_llm_expansion_when_enabled(monkeypatch):
    fake_index = FakeIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", True)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", 10)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.65)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", False)
    monkeypatch.setattr(semantic_search, "_llm_query_variants", lambda query: ["rubber tire skid steer machine"])

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(query="I am looking for bobcats", top_k=2)
    )

    assert hits
    assert "rubber tire skid steer machine" in debug["query_variants"]
    assert debug["llm_expansion"] is True


def test_product_semantic_search_filters_scores_strictly_above_default_threshold(monkeypatch):
    fake_index = FakeIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", 1)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_CANDIDATE_MULTIPLIER", 1)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.80)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", False)

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(query="bobcat", top_k=2)
    )

    assert [hit.id for hit in hits] == ["p1"]
    assert all(hit.score > 0.80 for hit in hits)
    assert debug["min_score"] == 0.80


def test_product_semantic_search_filters_before_top_k_limit(monkeypatch):
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.65)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", False)
    monkeypatch.setattr(product_search, "_product_query_text", lambda query: ("query", ["query"]))
    monkeypatch.setattr(product_search, "_search_product_index", lambda **kwargs: ([], False))
    monkeypatch.setattr(
        product_search,
        "_product_rerank_hits",
        lambda query, hits: [
            semantic_search.SemanticSearchHit(
                id="low",
                index="products",
                score=0.40,
                metadata={"name": "Low score"},
            ),
            semantic_search.SemanticSearchHit(
                id="high",
                index="products",
                score=0.90,
                metadata={"name": "High score"},
            ),
        ],
    )

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(query="loader", top_k=1)
    )

    assert [hit.id for hit in hits] == ["high"]
    assert debug["min_score"] == 0.65


def test_product_field_rerank_can_be_overridden_per_request(monkeypatch):
    fake_index = FakeIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", True)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_CANDIDATE_MULTIPLIER", 4)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELDS", ["name"])
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.65)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", False)

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(
            query="bobcat",
            top_k=2,
            rank_fields=["name", "description", "name"],
        )
    )

    call = fake_index.search_calls[0]
    assert hits
    assert call["query"]["top_k"] == 8
    assert call["fields"] == ["name", "description"]
    assert call["rerank"]["rank_fields"] == ["name", "description"]
    assert call["rerank"]["top_n"] == 8
    assert debug["rank_fields"] == ["name", "description"]

    fake_index.search_calls.clear()
    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(query="bobcat", top_k=2, field_rerank=False)
    )

    call = fake_index.search_calls[0]
    assert hits
    assert "rerank" not in call
    assert call["query"]["top_k"] == 2
    assert debug["pinecone_field_rerank"] is False


def test_product_field_rerank_missing_fields_retries_without_rerank(monkeypatch):
    fake_index = FakeRerankMissingFieldIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", True)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_FIELDS", ["name", "description"])
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_CANDIDATE_MULTIPLIER", 3)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELDS", [])
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.65)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", False)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", False)

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(query="bobcat", top_k=2)
    )

    assert hits
    assert len(fake_index.search_calls) == 2
    assert "rerank" in fake_index.search_calls[0]
    assert "rerank" not in fake_index.search_calls[1]
    assert debug["pinecone_field_rerank_requested"] is True
    assert debug["pinecone_field_rerank"] is False
    assert debug["rank_fields"] == []


def test_product_semantic_search_scores_candidates_before_threshold_and_top_k(monkeypatch):
    seen = {}
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", True)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_FIELDS", ["name"])
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_FIELD_RERANK_CANDIDATE_MULTIPLIER", 4)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEARCH_MIN_SCORE", 0.65)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", False)
    monkeypatch.setattr(product_search, "_product_query_text", lambda query: ("query", ["query"]))

    def fake_search(**kwargs):
        seen.update(kwargs)
        return [
            semantic_search.SemanticSearchHit(
                id="low",
                index="products",
                score=0.20,
                metadata={"name": "Low"},
            ),
            semantic_search.SemanticSearchHit(
                id="high",
                index="products",
                score=0.90,
                metadata={"name": "High"},
            ),
            semantic_search.SemanticSearchHit(
                id="mid",
                index="products",
                score=0.70,
                metadata={"name": "Mid"},
            ),
        ], True

    monkeypatch.setattr(product_search, "_search_product_index", fake_search)

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(query="anything", top_k=1)
    )

    assert seen["top_k"] == 4
    assert seen["rerank_top_n"] == 4
    assert [hit.id for hit in hits] == ["high"]
    assert debug["candidate_top_k"] == 4
    assert debug["rerank_top_n"] == 4
    assert debug["min_score"] == 0.65


def test_product_rerank_uses_shared_reranker_with_product_candidate_cap(monkeypatch):
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_ENABLED", True)
    monkeypatch.setattr(product_search.settings, "PRODUCT_SEMANTIC_RERANK_MAX_CANDIDATES", 2)

    seen = {}

    def fake_rerank(query, hits):
        seen["query"] = query
        seen["ids"] = [hit.id for hit in hits]
        hits[1].score = 0.95
        hits[0].score = 0.50
        return sorted(hits, key=lambda h: h.score, reverse=True)

    monkeypatch.setattr(semantic_search, "_rerank_hits", fake_rerank)
    hits = [
        semantic_search.SemanticSearchHit(id="p1", index="products", score=0.90, metadata={}),
        semantic_search.SemanticSearchHit(id="p2", index="products", score=0.80, metadata={}),
        semantic_search.SemanticSearchHit(id="p3", index="products", score=0.70, metadata={}),
    ]

    reranked = product_search._product_rerank_hits("bobcat", hits)

    assert seen == {"query": "bobcat", "ids": ["p1", "p2"]}
    assert [hit.id for hit in reranked] == ["p2", "p1", "p3"]
    assert reranked[2].score < 0.2


def test_product_facet_only_search_keeps_zero_vector_path(monkeypatch):
    fake_index = FakeIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "EMBEDDING_DIM", 3)

    req = ProductFacetSearchRequest(
        top_k=5,
        facets=[ProductFacetFilter(key="condition", value="Used")],
    )

    hits, debug = product_search.search_products(req)

    assert hits == []
    assert debug["semantic"] is False
    assert debug["min_score"] is None
    assert len(fake_index.calls) == 1
    assert fake_index.calls[0]["vector"] == [0.0, 0.0, 0.0]
    assert fake_index.calls[0]["filter"] == {"facet_condition": {"$eq": "Used"}}


def test_product_facet_only_respects_explicit_min_score(monkeypatch):
    fake_index = FakeIndex()
    monkeypatch.setattr(product_search, "_get_pinecone_index", lambda: fake_index)
    monkeypatch.setattr(product_search.settings, "EMBEDDING_DIM", 3)

    hits, debug = product_search.search_products(
        ProductFacetSearchRequest(top_k=5, min_score=0.65)
    )

    assert hits == []
    assert debug["min_score"] == 0.65
