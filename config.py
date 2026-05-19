"""Configuration settings for the News & Events Scraper API"""

import os
from typing import List


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
    PRODUCT_CATALOG_DATABASE_URL: str = os.getenv(
        "PRODUCT_CATALOG_DATABASE_URL",
        "",
    )

    # Pinecone (product vector store) configuration
    PINECONE_API_KEY: str = os.getenv(
        "PINECONE_API_KEY",
        "",
    )
    PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "products")
    PINECONE_NAMESPACE: str = os.getenv("PINECONE_NAMESPACE", "")
    PINECONE_ENVIRONMENT: str = os.getenv("PINECONE_ENVIRONMENT", "")  # only needed for legacy clients

    # Pinecone indexes for semantic search
    PINECONE_INDEX_JOBS: str = os.getenv("PINECONE_INDEX_JOBS", "jobs")
    PINECONE_INDEX_NEWS: str = os.getenv("PINECONE_INDEX_NEWS", "news")
    PINECONE_INDEX_PRODUCTS: str = os.getenv("PINECONE_INDEX_PRODUCTS", "products")

    # OpenAI (query embedding for vector search)
    OPENAI_API_KEY: str = os.getenv(
        "OPENAI_API_KEY",
        "",
    )
    # Must match the embedding dimension used when indexing products into Pinecone.
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1536"))
    # text-embedding-ada-002 → 1536 dims (matches the model used to index products).
    OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002")


settings = Settings()
