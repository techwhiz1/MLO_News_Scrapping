"""Configuration settings for the News & Events Scraper API"""

import os
from typing import List


def _load_dotenv(path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs from .env without overriding real env vars."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


_load_dotenv()


class Settings:
    """Application settings"""
    
    # API Configuration
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    API_DEBUG: bool = os.getenv("API_DEBUG", "False").lower() == "true"
    
    # Crawl4AI Configuration
    CRAWL4AI_VERBOSE: bool = os.getenv("CRAWL4AI_VERBOSE", "True").lower() == "true"
    CRAWL4AI_WAIT_FOR: str = os.getenv("CRAWL4AI_WAIT_FOR", "networkidle")
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "60"))
    
    # CORS Configuration
    # Default origins include localhost for development and production domain
    CORS_ORIGINS: List[str] = os.getenv(
        "CORS_ORIGINS", 
        "http://localhost:3000,http://localhost:8080,https://mininglifeonline.com,http://mininglifeonline.com"
    ).split(",")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Scraping Configuration
    MAX_PAGINATION_PAGES: int = int(os.getenv("MAX_PAGINATION_PAGES", "5"))
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))

    # Product catalog (ProductCategory / facets) — separate from DATABASE_URL
    PRODUCT_CATALOG_DATABASE_URL: str = os.getenv("PRODUCT_CATALOG_DATABASE_URL", "")

    # Pinecone (product vector store) configuration
    PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY", "")
    PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "products")
    PINECONE_NAMESPACE: str = os.getenv("PINECONE_NAMESPACE", "")
    PINECONE_ENVIRONMENT: str = os.getenv("PINECONE_ENVIRONMENT", "")  # only needed for legacy clients

    # Pinecone indexes for semantic search
    PINECONE_INDEX_JOBS: str = os.getenv("PINECONE_INDEX_JOBS", "jobs")
    PINECONE_INDEX_NEWS: str = os.getenv("PINECONE_INDEX_NEWS", "news")
    PINECONE_INDEX_PRODUCTS: str = os.getenv("PINECONE_INDEX_PRODUCTS", "products")
    PINECONE_INDEX_PRODUCTS_HOST: str = os.getenv(
        "PINECONE_INDEX_PRODUCTS_HOST",
        "https://products-vr5z7ba.svc.aped-4627-b74a.pinecone.io",
    )
    PINECONE_PRODUCTS_NAMESPACE: str = os.getenv("PINECONE_PRODUCTS_NAMESPACE", "__default__")
    PRODUCT_SEARCH_FIELDS: List[str] = [
        field.strip()
        for field in os.getenv(
            "PRODUCT_SEARCH_FIELDS",
            "",
        ).split(",")
        if field.strip()
    ]
    PRODUCT_SEARCH_FIELD_RERANK_ENABLED: bool = os.getenv(
        "PRODUCT_SEARCH_FIELD_RERANK_ENABLED", "True"
    ).lower() == "true"
    PRODUCT_SEARCH_FIELD_RERANK_MODEL: str = os.getenv(
        "PRODUCT_SEARCH_FIELD_RERANK_MODEL", "cohere-rerank-3.5"
    )
    # Pinecone rerank requires every returned record to contain every rank field.
    # Keep the default to a field that should exist on all products; add
    # description here only after the product index metadata has been backfilled.
    PRODUCT_SEARCH_FIELD_RERANK_FIELDS: List[str] = [
        field.strip()
        for field in os.getenv(
            "PRODUCT_SEARCH_FIELD_RERANK_FIELDS",
            "name",
        ).split(",")
        if field.strip()
    ]
    PRODUCT_SEARCH_FIELD_RERANK_CANDIDATE_MULTIPLIER: int = int(
        os.getenv("PRODUCT_SEARCH_FIELD_RERANK_CANDIDATE_MULTIPLIER", "3")
    )

    # OpenAI (query embedding for vector search)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    # Semantic query rewriting before vector search. If rewriting fails, search
    # falls back to deterministic synonym expansion plus the original query.
    SEMANTIC_QUERY_EXPANSION_ENABLED: bool = os.getenv(
        "SEMANTIC_QUERY_EXPANSION_ENABLED", "True"
    ).lower() == "true"
    SEMANTIC_QUERY_EXPANSION_MODEL: str = os.getenv(
        "SEMANTIC_QUERY_EXPANSION_MODEL", "gpt-5-mini"
    )
    SEMANTIC_MAX_QUERY_VARIANTS: int = int(os.getenv("SEMANTIC_MAX_QUERY_VARIANTS", "4"))
    SEMANTIC_CANDIDATE_MULTIPLIER: int = int(os.getenv("SEMANTIC_CANDIDATE_MULTIPLIER", "3"))
    SEMANTIC_RERANK_ENABLED: bool = os.getenv(
        "SEMANTIC_RERANK_ENABLED", "True"
    ).lower() == "true"
    SEMANTIC_RERANK_MODEL: str = os.getenv(
        "SEMANTIC_RERANK_MODEL", SEMANTIC_QUERY_EXPANSION_MODEL
    )
    SEMANTIC_RERANK_MAX_CANDIDATES: int = int(os.getenv("SEMANTIC_RERANK_MAX_CANDIDATES", "30"))
    SEMANTIC_RERANK_WEIGHT: float = float(os.getenv("SEMANTIC_RERANK_WEIGHT", "0.7"))

    # Product search uses one Pinecone integrated text search call by default.
    # Optional LLM expansion/rerank knobs remain available but are off for lower latency.
    PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED: bool = os.getenv(
        "PRODUCT_SEMANTIC_LLM_EXPANSION_ENABLED", "False"
    ).lower() == "true"
    PRODUCT_SEMANTIC_RERANK_ENABLED: bool = os.getenv(
        "PRODUCT_SEMANTIC_RERANK_ENABLED", "False"
    ).lower() == "true"
    PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS: int = int(
        os.getenv("PRODUCT_SEMANTIC_MAX_QUERY_VARIANTS", "10")
    )
    PRODUCT_SEMANTIC_CANDIDATE_MULTIPLIER: int = int(
        os.getenv("PRODUCT_SEMANTIC_CANDIDATE_MULTIPLIER", "2")
    )
    PRODUCT_SEMANTIC_RERANK_MAX_CANDIDATES: int = int(
        os.getenv("PRODUCT_SEMANTIC_RERANK_MAX_CANDIDATES", "10")
    )
    PRODUCT_SEMANTIC_PINECONE_WORKERS: int = int(
        os.getenv("PRODUCT_SEMANTIC_PINECONE_WORKERS", "4")
    )
    PRODUCT_SEARCH_MIN_SCORE: float = float(os.getenv("PRODUCT_SEARCH_MIN_SCORE", "0.50"))

    # Must match the embedding dimension used when indexing products into Pinecone.
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1536"))
    # text-embedding-3-small is the current lower-cost OpenAI embedding model.
    OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


settings = Settings()
