from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from models import (
    NewsScrapingRequest, 
    NewsScrapingResponse, 
    EventScrapingRequest, 
    EventScrapingResponse,
    JobScoreRequest, JobScoreResponse,
    ResumeScoreRequest, ResumeScoreResponse,
    ProductScrapingRequest, ProductScrapingResponse,
    ProductFacetSearchRequest, ProductFacetSearchResponse,
    WoodUsageRequest, WoodUsageResponse,
    SemanticSearchRequest, SemanticSearchResponse,
    ErrorResponse
)
from news_scraper import NewsScraper
from event_scraper import EventScraper
from product_scraper import ProductScraper
from wood_usage_scraper import WoodUsageScraper
from scoring_service import ScoringService
from database import get_db
from config import settings
import product_search
import semantic_search


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logging.basicConfig(level=logging.INFO)
    logging.info("Starting News & Events Scraper API...")
    yield
    # Shutdown
    logging.info("Shutting down News & Events Scraper API...")


# Create FastAPI app
app = FastAPI(
    title="News & Events Scraper API",
    description="API for scraping news articles and events using Crawl4AI",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS origins from environment or config
cors_origins = os.getenv("CORS_ORIGINS", "").strip()
if cors_origins:
    # Parse comma-separated origins from environment variable
    allowed_origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
else:
    # Use config file settings, strip whitespace from each origin
    if settings.CORS_ORIGINS:
        allowed_origins = [origin.strip() for origin in settings.CORS_ORIGINS if origin.strip()]
    else:
        # Default to allow all origins if nothing is configured
        allowed_origins = ["*"]

# Determine if we're using wildcard (all origins)
# When allow_origins=["*"], allow_credentials must be False (CORS security restriction)
is_wildcard = len(allowed_origins) == 1 and allowed_origins[0] == "*"

# Log the CORS configuration for debugging
logging.info(f"CORS configured with origins: {allowed_origins}, credentials: {not is_wildcard}")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=not is_wildcard,  # Only allow credentials if not using wildcard
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,  # Cache preflight requests for 1 hour
)

# Initialize services
scoring_service = ScoringService()

# Directory for saving response JSON files (before sending to client)
RESPONSE_JSON_DIR = os.getenv("RESPONSE_JSON_DIR", "response_data")


def _save_response_json(data, prefix: str) -> str:
    """Save response data as JSON file before sending. Returns path to saved file."""
    try:
        os.makedirs(RESPONSE_JSON_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{ts}.json"
        filepath = os.path.join(RESPONSE_JSON_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logging.info("Saved response to %s", filepath)
        return filepath
    except Exception as e:
        logging.warning("Could not save response JSON: %s", e)
        return ""


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "News & Events Scraper API",
        "version": "1.0.0",
        "endpoints": {
            "news": "/scrape-news",
            "events": "/scrape-events",
            "products": "/scrape-products",
            "product_facet_search": "/products/facet-search",
            "semantic_search": "/semantic-search",
            "wood_usage": "/analyze-wood-usage",
            "job_scoring": "/jobs/score",
            "resume_scoring": "/resumes/score",
            "health": "/health"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "News & Events Scraper API"}


@app.post("/scrape-news", response_model=NewsScrapingResponse)
async def scrape_news(request: NewsScrapingRequest):
    """
    Scrape news from the provided URL (can be a single news article URL or a news list URL)
    
    Args:
        request: NewsScrapingRequest containing the news URL or news list URL
        
    Returns:
        NewsScrapingResponse with extracted news data (list of news articles)
    """
    try:
        max_news = request.max_news or 5  # Ensure default is 10
        geo_area = request.geo_area  # Optional: filter by geographic areas
        logging.info(f"Starting news scraping for URL: {request.news_url} (max_news={max_news}, geo_area={geo_area})")
        async with NewsScraper() as scraper:
            result = await scraper.scrape_news(str(request.news_url), max_news, geo_area=geo_area)
            logging.info(f"Successfully scraped {result.total_news} news articles (requested: {max_news})")
            return result
            
    except Exception as e:
        logging.error(f"Error scraping news from {request.news_url}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scrape news: {str(e)}"
        )


@app.post("/scrape-events", response_model=EventScrapingResponse)
async def scrape_events(request: EventScrapingRequest):
    """
    Scrape events from the provided event list URL with pagination support
    
    Args:
        request: EventScrapingRequest containing the event list URL
        
    Returns:
        EventScrapingResponse with extracted events data
    """
    try:
        max_events = request.max_events or 10  # Ensure default is 10
        logging.info(f"Starting event scraping for URL: {request.event_list_url} (max_events={max_events})")
        async with EventScraper() as scraper:
            result = await scraper.scrape_events(str(request.event_list_url), max_events)
            logging.info(f"Successfully scraped {result.total_events} events (requested: {max_events})")
            data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result.dict()
            _save_response_json(data, "scrape_events")
            return result
            
    except Exception as e:
        logging.error(f"Error scraping events from {request.event_list_url}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scrape events: {str(e)}"
        )


@app.post(
    "/scrape-products",
    response_model=ProductScrapingResponse,
    response_model_by_alias=True,
)
async def scrape_products(request: ProductScrapingRequest):
    """
    Scrape a single product from its detail page URL.

    Args:
        request: ProductScrapingRequest containing the product detail page URL

    Returns:
        ProductScrapingResponse with the extracted product data
    """
    try:
        logging.info(f"Starting product scraping for URL: {request.product_url}")
        async with ProductScraper() as scraper:
            result = await scraper.scrape_product(str(request.product_url))
            logging.info(f"Product scraping complete for {request.product_url}")
            return result

    except Exception as e:
        logging.error(f"Error scraping product from {request.product_url}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scrape product: {str(e)}"
        )


@app.post(
    "/products/facet-search",
    response_model=ProductFacetSearchResponse,
    response_model_by_alias=True,
)
async def facet_search_products(request: ProductFacetSearchRequest):
    """
    Facet-based product search backed by the Pinecone vector database.

    Accepts optional free-text ``query`` (embedded via OpenAI for semantic
    similarity) together with structured category / facet filters. Any
    combination of filters is allowed, and the endpoint can also be used as a
    pure facet filter by omitting ``query``.

    Args:
        request: ProductFacetSearchRequest with query text and/or facet filters

    Returns:
        ProductFacetSearchResponse with the matching product hits.
    """
    try:
        logging.info(
            "Product facet search (query=%r, top_k=%s, facets=%s)",
            request.query,
            request.top_k,
            len(request.facets or []),
        )

        hits, debug = await asyncio.to_thread(product_search.search_products, request)

        return ProductFacetSearchResponse(
            hits=hits,
            total=len(hits),
            query=request.query,
            namespace=debug.get("namespace") or None,
        )

    except RuntimeError as e:
        logging.error(f"Product facet search config error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except Exception as e:
        logging.error(f"Error in product facet search: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search products: {str(e)}",
        )


@app.post("/semantic-search", response_model=SemanticSearchResponse)
async def semantic_search_endpoint(request: SemanticSearchRequest):
    """
    Semantic search across jobs, news, and products Pinecone indexes.

    Given a natural-language query (e.g. "companies which sell tires" or
    "companies looking for miners"), returns the most relevant microsite
    companies ranked by semantic similarity.

    Args:
        request: SemanticSearchRequest with query text and optional index filter.

    Returns:
        SemanticSearchResponse with ranked results from the matching indexes.
    """
    try:
        logging.info(
            "Semantic search (query=%r, top_k=%s, indexes=%s)",
            request.query,
            request.top_k,
            request.indexes,
        )

        hits = await asyncio.to_thread(semantic_search.search, request)

        return SemanticSearchResponse(
            query=request.query,
            results=hits,
            total=len(hits),
        )

    except RuntimeError as e:
        logging.error(f"Semantic search config error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except Exception as e:
        logging.error(f"Error in semantic search: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to perform semantic search: {str(e)}",
        )


@app.post("/analyze-wood-usage", response_model=WoodUsageResponse)
async def analyze_wood_usage(request: WoodUsageRequest):
    """
    Analyze a company website to determine why and how the company uses wood,
    based on its products and services.

    Args:
        request: WoodUsageRequest containing the company website URL

    Returns:
        WoodUsageResponse with analysis of the company's wood usage
    """
    try:
        logging.info(f"Starting wood-usage analysis for URL: {request.company_url}")
        async with WoodUsageScraper() as scraper:
            result = await scraper.analyze(str(request.company_url))
            logging.info(
                f"Wood-usage analysis complete for {request.company_url} "
                f"(confidence={result.confidence})"
            )
            return result

    except Exception as e:
        logging.error(f"Error analyzing wood usage for {request.company_url}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to analyze wood usage: {str(e)}"
        )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.detail,
            message=exc.detail,
            status_code=exc.status_code
        ).model_dump()
    )


@app.post("/jobs/score", response_model=JobScoreResponse)
async def score_job_against_resumes(request: JobScoreRequest):
    """Score a specific job against all resumes"""
    try:
        logging.info(f"Starting job scoring for job_id: {request.job_id}")
        
        # Get database session
        db = next(get_db())
        
        # Score job against all resumes
        scores = await scoring_service.score_job_against_resumes(request.job_id, db)
        
        logging.info(f"Completed job scoring. Found {len(scores)} resume scores")
        
        return JobScoreResponse(
            scores=scores,
            total_resumes=len(scores)
        )
        
    except Exception as e:
        logging.error(f"Error in job scoring: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error scoring job: {str(e)}"
        )


@app.post("/resumes/score", response_model=ResumeScoreResponse)
async def score_resume_against_jobs(request: ResumeScoreRequest):
    """Score a specific resume against selected jobs"""
    try:
        logging.info(f"Starting resume scoring for document_id: {request.document_id}")
        
        # Get database session
        db = next(get_db())
        
        # Score resume against selected jobs
        scores = await scoring_service.score_resume_against_jobs(
            request.document_id, 
            request.url, 
            request.job_ids,
            db
        )
        
        logging.info(f"Completed resume scoring. Found {len(scores)} job scores")
        
        return ResumeScoreResponse(
            scores=scores,
            total_jobs=len(scores)
        )
        
    except ValueError as e:
        logging.error(f"Invalid resume scoring request: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        logging.error(f"Error in resume scoring: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error scoring resume: {str(e)}"
        )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """General exception handler"""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="Internal Server Error",
            message=str(exc),
            status_code=500
        ).model_dump()
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8889, log_level="info", timeout_keep_alive=300, timeout_graceful_shutdown=300)
