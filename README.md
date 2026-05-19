# News & Events Scraper API

A FastAPI-based backend API for scraping news articles and events using Crawl4AI.

## Features

### News Scraping API
- Scrapes individual news article pages
- Extracts: Title, Image/Video URLs, Content, Date/Time, Author, Tagline, Short Description
- Preserves sanitized article HTML along with structured content blocks, element counts, and outbound links so the article can be re-rendered faithfully elsewhere

### Event Scraping API  
- Scrapes event list pages with pagination support
- Extracts: Title, Image/Video URLs, Details, Date/Time, Location, Event Type, Access Type, Agenda, Speakers, Category

### Product Scraping API
- Scrapes product list pages and extracts individual product detail URLs
- Uses LLM to intelligently detect product category and subcategory from content
- Extracts: Title, Description, Image URLs, Documentation URLs, Product Attributes (Condition, Price, Size/Capacity, Power Requirement, Weight, Usage Hours, Brand/Manufacturer, Model Year, Warranty)

## Setup Instructions

### 1. Create Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Linux/Mac
# or
venv\Scripts\activate  # On Windows
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Set OpenAI API Key (Optional)
The scraper uses OpenAI's GPT-4o-mini for intelligent content extraction. You can set your API key as an environment variable:

```bash
export OPENAI_API_KEY="your-openai-api-key-here"
```

Or the scraper will use the default key provided in the code.

### 4. Run the API
```bash
python main.py
```

The API will be available at `http://localhost:8000`

## API Endpoints

### Health Check
- **GET** `/health` - Check API health status

### Product Scraping
- **POST** `/scrape-products`
  - **Request Body:**
    ```json
    {
      "product_list_url": "https://example.com/products",
      "max_products": 5
    }
    ```
  - **Response:**
    ```json
    {
      "products": [
        {
          "title": "Product Name",
          "description": "Detailed product description...",
          "image_url": "https://example.com/product-image.jpg",
          "doc_url": "https://example.com/manual.pdf",
          "category": "DRILLING",
          "subcategory": "Rotary Drills",
          "attributes": {
            "condition": "Brand New",
            "price": "On Request",
            "size_capacity": "Heavy Duty",
            "power_requirement": "Diesel",
            "weight": "5000 kg",
            "usage_hours": null,
            "brand_manufacturer": "Caterpillar",
            "model_year": "2024",
            "warranty": "Included"
          },
          "product_url": "https://example.com/product-detail"
        }
      ],
      "total_products": 1,
      "source_url": "https://example.com/products"
    }
    ```

### News Scraping
- **POST** `/scrape-news`
  - **Request Body:**
    ```json
    {
      "news_url": "https://example.com/news-article"
    }
    ```
  - **Response:**
    ```json
    {
      "news": [
        {
          "title": "News Title",
          "image_url": "https://example.com/image.jpg",
          "video_url": null,
          "content": "Full news content...",
          "content_html": "<article><p>Full news content...</p></article>",
          "content_blocks": [
            {"type": "heading", "level": "h2", "text": "Section heading"},
            {"type": "paragraph", "text": "Paragraph text..."},
            {"type": "image", "src": "https://example.com/image.jpg", "caption": "Caption"}
          ],
          "content_stats": {
            "paragraphs": 6,
            "headings": 2,
            "images": 1,
            "videos": 0,
            "embeds": 0,
            "lists": 1,
            "quotes": 0,
            "links": 3
          },
          "links": [
            {"href": "https://example.com/more", "text": "Related article"}
          ],
          "subheadings": ["Section heading"],
          "date_time": "2024-01-01T10:00:00",
          "author": "Author Name",
          "tagline": "News tagline",
          "short_description": "Brief description",
          "source_url": "https://example.com/news-article"
        }
      ],
      "total_news": 1,
      "source_url": "https://example.com/news-article"
    }
    ```

### Event Scraping
- **POST** `/scrape-events`
  - **Request Body:**
    ```json
    {
      "event_list_url": "https://example.com/events"
    }
    ```
  - **Response:**
    ```json
    {
      "events": [
        {
          "title": "Event Title",
          "image_url": "https://example.com/event-image.jpg",
          "video_url": null,
          "details": "Event description...",
          "date_time": "2024-01-01T10:00:00",
          "location": "Event Location",
          "event_type": "Conference",
          "access_type": "Free",
          "agenda": "Event agenda...",
          "speakers": ["Speaker 1", "Speaker 2"],
          "category": "Physical",
          "event_url": "https://example.com/event-details"
        }
      ],
      "total_events": 1,
      "source_url": "https://example.com/events"
    }
    ```

## API Documentation

Once the server is running, visit:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

## Error Handling

The API includes comprehensive error handling:
- Input validation using Pydantic models
- HTTP status codes for different error types
- Detailed error messages
- Graceful handling of scraping failures

## Dependencies

- **FastAPI:** Web framework
- **Crawl4AI:** Web scraping and content extraction
- **Pydantic:** Data validation and serialization
- **Uvicorn:** ASGI server
- **BeautifulSoup4:** HTML parsing
- **lxml:** XML/HTML parser
- **python-dateutil:** Date parsing utilities

## Supported Product Categories

The Product Scraping API supports three main categories with their subcategories:

### 1. DRILLING
- Rotary Drills, Auger Drills, Long Hole Drills, Horizontal Directional Drills
- Core Drills, Jackleg Drilling, Drill Bits, Jumbos Drill, Roof Bolters For Coal
- Down-The-Hole Drills, Blast Hole Drills, Water Well Drills, Crawler Drills
- Percussion Drills, Tunnel Boring Machines, Rock Breakers (Mobile & Stationary)
- Accessories & Parts

### 2. Underground Mining Vehicles
- Load Haul Dump (LHD) Loaders, Mine Utility Vehicles, Underground Graders
- Water Truck, Maintenance Utility Vehicle, Battery Electric Vehicles (BEVs)
- Excavators, Emergency Rescue Vehicle, Personnel Carrier, Tow Tractor
- Lifting & Material Handling Equipment, Attachments, Tires, Accessories & Parts

### 3. Ventilation
- Auxiliary Fan, Booster Fan, Ventilation Ducting, Ventilation Regulator
- Air Door, Stopping (Vent Wall), Air Curtain, Air Shaft, Raise Bore Vent Shaft
- Reversible Fan, Explosion-Proof Fan, High-Pressure Fan, Ventilation Monitoring System
- Variable Speed Drive (VSD) Fan, Jet Fan, Silencer for Fans, Fan Starter Panel
- Ventilation Louvers, Fan Auto Start System, Airflow Control Damper
- Emergency Ventilation System, Ventilation-on-Demand (VoD) Controller
- Sensor Nodes (CO, CH4, O2, Airflow), Fan Mounting Skid, Accessories & Parts

## Notes

- The API uses Crawl4AI with LLM extraction strategies for intelligent content extraction
- Event scraping includes pagination support to gather all events from list pages
- Product scraping uses LLM to intelligently detect category and subcategory from content analysis
- Product scraping includes performance optimizations: concurrent processing, timeouts, and configurable limits
- Use `max_products` parameter to control the number of products scraped (default: 10, recommended: 5-10 for faster response)
- Date parsing supports multiple common formats
- The API is designed to handle various news, event, and product website structures
- CORS is enabled for cross-origin requests (configure properly for production)
