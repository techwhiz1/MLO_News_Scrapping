from models import SemanticSearchHit
import semantic_search


def test_bobcat_query_expands_to_skid_steer_terms(monkeypatch):
    monkeypatch.setattr(semantic_search.settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", False)
    variants = semantic_search._query_variants("please give me bobcats")
    joined = " ".join(variants).lower()

    assert "skid steer" in joined
    assert "compact loader" in joined
    assert "please give me bobcats" in variants


def test_bobcat_typo_expands_to_skid_steer_terms(monkeypatch):
    monkeypatch.setattr(semantic_search.settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", False)
    variants = semantic_search._query_variants("Bpbcats")
    joined = " ".join(variants).lower()

    assert "skid steer" in joined


def test_botcat_typo_expands_to_skid_steer_terms(monkeypatch):
    monkeypatch.setattr(semantic_search.settings, "SEMANTIC_QUERY_EXPANSION_ENABLED", False)
    variants = semantic_search._query_variants("botcat")
    joined = " ".join(variants).lower()

    assert "skid steer" in joined


def test_fuse_hits_deduplicates_and_boosts_repeated_semantic_matches():
    first = [
        SemanticSearchHit(id="p1", index="products", score=0.71, metadata={"name": "Skid Steer"}),
        SemanticSearchHit(id="p2", index="products", score=0.69, metadata={"name": "Loader"}),
    ]
    second = [
        SemanticSearchHit(id="p1", index="products", score=0.68, metadata={"name": "Skid Steer"}),
    ]

    fused = semantic_search._fuse_hits([first, second])

    assert [hit.id for hit in fused] == ["p1", "p2"]
    assert fused[0].score > 0.71
