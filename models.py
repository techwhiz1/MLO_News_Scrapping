from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from typing import Any, Optional, List, Dict, Union
from datetime import datetime


# Valid geographic areas for news filtering
VALID_GEO_AREAS = frozenset({
    "North America", "South America", "Europe", "Africa", "Asia", "Oceania", "Antarctica"
})


class NewsScrapingRequest(BaseModel):
    """Request model for news scraping API"""
    news_url: HttpUrl  # Can be a single news article URL or a news list URL
    max_news: Optional[int] = 5  # Maximum number of news articles to scrape
    geo_area: Optional[List[str]] = None  # Filter news by geographic areas (e.g., ["North America", "Europe"])


class NewsContentBlock(BaseModel):
    """Represents a single block element in the scraped news body"""
    type: str  # paragraph, heading, image, video, list, quote, embed, etc.
    html: Optional[str] = None
    text: Optional[str] = None
    level: Optional[str] = None  # Heading level (h1-h6)
    src: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    href: Optional[str] = None
    ordered: Optional[bool] = None
    items: Optional[List[str]] = None
    rows: Optional[List[List[str]]] = None


class NewsContentStats(BaseModel):
    """Aggregated counts of elements found in the article body"""
    paragraphs: int = 0
    headings: int = 0
    images: int = 0
    videos: int = 0
    embeds: int = 0
    lists: int = 0
    quotes: int = 0
    links: int = 0


class NewsLink(BaseModel):
    """Metadata for hyperlinks discovered within the article body"""
    href: str
    text: Optional[str] = None
    title: Optional[str] = None


class NewsDetails(BaseModel):
    """Model for individual news article details"""
    title: str
    image_urls: Optional[List[str]] = None  # All image URLs from the article
    video_urls: Optional[List[str]] = None  # All video URLs from the article
    thumbnail_url: Optional[str] = None  # Thumbnail image from news list page
    content: str
    content_html: Optional[str] = None  # Sanitized HTML version of the article body (original classes preserved)
    content_style: Optional[str] = None  # CSS extracted from the source page (includes @media responsive rules) + safety-net CSS
    content_blocks: Optional[List[NewsContentBlock]] = None
    content_stats: Optional[NewsContentStats] = None
    links: Optional[List[NewsLink]] = None
    subheadings: Optional[List[str]] = None
    date_time: Optional[datetime] = None
    author: Optional[str] = None
    tagline: Optional[str] = None
    short_description: Optional[str] = None
    category: Optional[str] = None  # One of: Financial, Precious Metals/stones, Critical Minerals, Base Mineral, Indigenous, Technology
    geo_area: Optional[str] = None  # Primary geographic area: North America, South America, Europe, Africa, Asia, Oceania, Antarctica
    source_url: str


class NewsScrapingResponse(BaseModel):
    """Response model for news scraping API"""
    news: List[NewsDetails]
    total_news: int
    source_url: str
    style: Optional[str] = None  # Global responsive CSS to apply when rendering content_html


class EventContentBlock(BaseModel):
    """Represents a single block element in the scraped event details"""
    type: str  # paragraph, heading, image, video, list, quote, embed, table, etc.
    html: Optional[str] = None
    text: Optional[str] = None
    level: Optional[str] = None  # Heading level (h1-h6)
    src: Optional[str] = None
    alt: Optional[str] = None
    caption: Optional[str] = None
    href: Optional[str] = None
    ordered: Optional[bool] = None
    items: Optional[List[str]] = None
    rows: Optional[List[List[str]]] = None


class EventContentStats(BaseModel):
    """Aggregated counts of elements found in the event details"""
    paragraphs: int = 0
    headings: int = 0
    images: int = 0
    videos: int = 0
    embeds: int = 0
    lists: int = 0
    quotes: int = 0
    links: int = 0
    tables: int = 0


class EventLink(BaseModel):
    """Metadata for hyperlinks discovered within the event details"""
    href: str
    text: Optional[str] = None
    title: Optional[str] = None


class EventScrapingRequest(BaseModel):
    """Request model for event scraping API"""
    event_list_url: HttpUrl
    max_events: Optional[int] = 10  # Maximum number of events to scrape


class EventDetails(BaseModel):
    """Model for individual event details"""
    title: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    details: str
    details_html: Optional[str] = None  # Sanitized HTML version of the event details
    content_blocks: Optional[List[EventContentBlock]] = None
    content_stats: Optional[EventContentStats] = None
    links: Optional[List[EventLink]] = None
    subheadings: Optional[List[str]] = None
    date_time: Optional[str] = None  # Store as string to preserve exact format (e.g., "January 26, 2026, 8:30 AM-January 29, 2026, 4:30 PM")
    location: Optional[str] = None
    event_type: Optional[str] = None
    access_type: Optional[str] = None  # Free, Paid, Requires Registration
    agenda: Optional[str] = None
    speakers: Optional[List[str]] = None
    category: Optional[str] = None  # Physical, Virtual
    event_url: str


class EventScrapingResponse(BaseModel):
    """Response model for event scraping API"""
    events: List[EventDetails]
    total_events: int
    source_url: str


class ErrorResponse(BaseModel):
    """Error response model"""
    error: str
    message: str
    status_code: int


# Job Scoring Models
class JobScoreRequest(BaseModel):
    """Request model for job scoring API"""
    job_id: str


class ResumeScoreRequest(BaseModel):
    """Request model for resume scoring API"""
    document_id: str
    url: str
    job_ids: List[str] = Field(
        ...,
        min_length=1,
        description="JobPost.id values to score this resume against",
    )


class ScoreResult(BaseModel):
    """Individual score result"""
    document_id: str
    job_id: str
    score: int


class JobScoreResponse(BaseModel):
    """Response model for job scoring API"""
    scores: List[ScoreResult]
    total_resumes: int


class ResumeScoreResponse(BaseModel):
    """Response model for resume scoring API"""
    scores: List[ScoreResult]
    total_jobs: int


# Product Scraping Models
class ProductScrapingRequest(BaseModel):
    """Request model for product scraping API — accepts a single product detail page URL"""
    product_url: HttpUrl


class ProductCategoryRef(BaseModel):
    """One node in the ProductCategory tree (id + display name from DB)."""
    id: str
    name: str
    slug: Optional[str] = None


class ProductFacetValue(BaseModel):
    """A facet from ProductFacetDefinition with an extracted value for this product."""
    facet_id: str
    value: Optional[Union[str, int, float]] = None
    value_type: Optional[str] = None
    key: Optional[str] = None
    label: Optional[str] = None
    sort_order: Optional[int] = None


class ProductDetails(BaseModel):
    """Model for a single scraped product"""
    model_config = ConfigDict(populate_by_name=True)

    title: str
    description: str
    image_urls: Optional[List[str]] = None
    video_urls: Optional[List[str]] = None
    doc_url: Optional[str] = None
    document_urls: Optional[List[str]] = None
    cluster: Optional[ProductCategoryRef] = None
    category: Optional[ProductCategoryRef] = None
    # JSON key is "class" (reserved word in Python, so we keep `class_` internally).
    class_: Optional[ProductCategoryRef] = Field(default=None, alias="class")
    sub_class: Optional[ProductCategoryRef] = None
    facets: Optional[List[ProductFacetValue]] = None
    attributes: Optional[Dict[str, str]] = None
    product_url: str


class ProductScrapingResponse(BaseModel):
    """Response model for product scraping API"""
    product: Optional[ProductDetails] = None
    source_url: str


# Product Facet Search Models (Pinecone-backed)
FacetScalar = Union[str, int, float, bool]


class ProductFacetFilter(BaseModel):
    """A single facet constraint for product facet search.

    Supply either `facet_id` or `key` (human-readable facet key). One of
    `value` / `values` / (`min`, `max`) must be provided.
    """
    facet_id: Optional[str] = None
    key: Optional[str] = None
    value: Optional[FacetScalar] = None
    values: Optional[List[FacetScalar]] = None  # $in filter
    min: Optional[Union[int, float]] = None
    max: Optional[Union[int, float]] = None


class ProductFacetSearchRequest(BaseModel):
    """Request model for facet-based product search on Pinecone."""
    query: Optional[str] = Field(
        default=None,
        description="Optional free-text query embedded for semantic search",
    )
    super_category_id: Optional[str] = None
    category_id: Optional[str] = None
    class_id: Optional[str] = None
    sub_class_id: Optional[str] = None
    facets: Optional[List[ProductFacetFilter]] = None
    top_k: int = Field(default=20, ge=1, le=200)
    min_score: Optional[float] = Field(
        default=None,
        description="Drop hits below this similarity score (0-1)",
    )
    namespace: Optional[str] = Field(
        default=None,
        description="Optional Pinecone namespace override",
    )
    include_metadata: bool = True
    field_rerank: Optional[bool] = Field(
        default=None,
        description="Override Pinecone field reranking for semantic product search",
    )
    rank_fields: Optional[List[str]] = Field(
        default=None,
        description="Ordered product fields used by Pinecone rerank; earlier fields have higher priority",
    )


class ProductSearchHit(BaseModel):
    """One product hit returned by the facet search API."""
    model_config = ConfigDict(populate_by_name=True)

    id: str
    score: float
    title: Optional[str] = None
    description: Optional[str] = None
    product_url: Optional[str] = None
    image_urls: Optional[List[str]] = None
    cluster: Optional[ProductCategoryRef] = None
    category: Optional[ProductCategoryRef] = None
    class_: Optional[ProductCategoryRef] = Field(default=None, alias="class")
    sub_class: Optional[ProductCategoryRef] = None
    facets: Optional[List[ProductFacetValue]] = None
    metadata: Optional[Dict[str, Any]] = None


class ProductFacetSearchResponse(BaseModel):
    """Response model for facet-based product search."""
    hits: List[ProductSearchHit]
    total: int
    query: Optional[str] = None
    namespace: Optional[str] = None


# Wood Usage Analysis Models
class WoodUsageRequest(BaseModel):
    """Request model for wood usage analysis API"""
    company_url: HttpUrl


class WoodUsageResponse(BaseModel):
    """Response model for wood usage analysis API"""
    company_name: Optional[str] = None
    company_url: str
    why_uses_wood: Optional[str] = None
    how_uses_wood: Optional[str] = None
    wood_related_products: Optional[List[str]] = None
    wood_related_services: Optional[List[str]] = None
    summary: Optional[str] = None
    confidence: Optional[str] = None  # high, medium, low, none
    raw_evidence: Optional[List[str]] = None


# Semantic Search Models
class SemanticSearchRequest(BaseModel):
    """Request model for semantic search across jobs, news, and products indexes."""
    query: str = Field(..., description="Natural language search query")
    top_k: int = Field(default=10, ge=1, le=100, description="Number of results per index")
    indexes: Optional[List[str]] = Field(
        default=None,
        description="Which indexes to search. Options: 'jobs', 'news', 'products'. If null, searches all.",
    )


class SemanticSearchHit(BaseModel):
    """A single search result from semantic search."""
    id: str
    score: float
    index: str = Field(..., description="Which index this result came from (jobs/news/products)")
    metadata: Optional[Dict[str, Any]] = None


class SemanticSearchResponse(BaseModel):
    """Response model for semantic search."""
    query: str
    results: List[SemanticSearchHit]
    total: int
