import asyncio
import json
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
import re
from urllib.parse import urljoin, urlparse, urlunparse
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from openai import OpenAI
from bs4 import BeautifulSoup
import logging
import aiohttp
from models import (
    NewsScrapingResponse,
    NewsDetails,
    NewsContentBlock,
    NewsContentStats,
    NewsLink,
    VALID_GEO_AREAS,
)


class NewsScraper:
    """News scraping service using Crawl4AI"""
    
    def __init__(self):
        self.crawler = None
        # Use environment variable or fallback to the provided key
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        # Initialize OpenAI client
        self.openai_client = OpenAI(api_key=self.openai_api_key)
    
    @staticmethod
    def _url_without_query(url: str) -> str:
        """Strip query string and fragment from a URL for comparison."""
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', '', ''))

    async def __aenter__(self):
        self.crawler = AsyncWebCrawler(verbose=True)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.crawler:
            await self.crawler.close()
    
    async def scrape_news(self, news_url: str, max_news: int = 5, geo_area: Optional[List[str]] = None) -> NewsScrapingResponse:
        """
        Scrape news from the given URL (can be a single news article URL or a news list URL)
        
        Args:
            news_url: URL of news article or news list page
            max_news: Maximum number of news articles to scrape (default: 5)
            geo_area: Optional list of geographic areas to filter by (e.g., ["North America", "Europe"]).
                     Valid areas: North America, South America, Europe, Africa, Asia, Oceania, Antarctica.
        """
        try:
            if not self.crawler:
                self.crawler = AsyncWebCrawler(verbose=True)
            
            # Normalize and validate geo_area filter if provided
            geo_filter = self._normalize_geo_area_filter(geo_area) if geo_area else None
            
            # First, check if this is a list URL by extracting news URLs, thumbnails, and titles
            news_items_data = await self._extract_news_urls(news_url)
            
            if not news_items_data:
                # If no news URLs found, treat as single news article
                # But skip if it's a PDF file
                if str(news_url).lower().endswith('.pdf'):
                    print(f"Skipping PDF file: {news_url}")
                    return NewsScrapingResponse(
                        news=[],
                        total_news=0,
                        source_url=str(news_url)
                    )
                print(f"No news URLs found, treating as single article: {news_url}")
                # Create a list with single item and no thumbnail/title/date_time
                news_items_data = [(news_url, None, None, None)]
            
            # Create dictionaries mapping URLs to thumbnails, titles, and date_time for easy lookup
            url_to_thumbnail = {url: thumb for url, thumb, _, _ in news_items_data}
            url_to_title = {url: title for url, _, title, _ in news_items_data}
            url_to_date_time = {url: dt for url, _, _, dt in news_items_data}
            news_urls = [url for url, _, _, _ in news_items_data]
            
            # Scrape news articles up to max_news limit, passing thumbnails, titles, date_time, and geo filter
            news_items = await self._scrape_news_concurrently(news_urls, max_news, url_to_thumbnail, url_to_title, url_to_date_time, geo_filter)
            
            # The style field contains the safety-net responsive CSS.
            # Each article's full CSS (origin page CSS + safety-net) is built
            # per-article inside _build_content_structure because different
            # source URLs have different stylesheets.  The response-level style
            # is the safety-net that the frontend should always include.
            responsive_style = self._generate_responsive_safety_css()
            
            return NewsScrapingResponse(
                news=news_items,
                total_news=len(news_items),
                source_url=str(news_url),
                style=responsive_style
            )
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            raise Exception(f"Error scraping news: {str(e)}\nDetails: {error_details}")
    
    async def _extract_news_urls(self, list_url: str) -> List[tuple]:
        """Extract all news article URLs, thumbnails, titles, and date_time from a news list page
        
        Returns:
            List of tuples: [(news_url, thumbnail_url, title, date_time), ...] where thumbnail_url, title, and date_time can be None
        """
        try:
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=1,
                page_timeout=30000,
                delay_before_return_html=5.0
            )
            
            result = await self.crawler.arun(url=str(list_url), config=crawler_config)
            
            if not result.success:
                print(f"Failed to crawl news list page: {result.error_message}")
                return []
            
            soup = BeautifulSoup(result.html, 'html.parser')
            
            landing_page_base = self._url_without_query(str(list_url)).lower()
            
            # Focus on body content only - remove header and footer
            body = soup.find('body')
            if not body:
                return []
            
            # Remove header, footer, nav, menu elements
            for element in body.find_all(['header', 'footer', 'nav', 'menu']):
                element.decompose()
            
            # Remove elements with "menu" or "footer" in class or id name
            # Exception: Don't remove elements with "page--white-menu" in class name
            # This catches patterns like class="container-fluid footer", class="site-footer", etc.
            ignore_keywords = ['menu', 'footer']
            for keyword in ignore_keywords:
                # Remove by class - search ALL element types (not just specific tags)
                # to catch footers regardless of the HTML tag used
                for element in body.find_all(True, 
                                            class_=lambda x: x and keyword in str(x).lower() and 'page--white-menu' not in str(x).lower()):
                    element.decompose()
                # Remove by id - search ALL element types
                for element in body.find_all(True, 
                                            id=lambda x: x and keyword in str(x).lower()):
                    element.decompose()
            
            # Remove label/tag/category elements (e.g. "division-label", "category-tag")
            # These contain non-article links (division pages, category pages) that shouldn't
            # be treated as news URLs. Only target small/inline elements, not major containers.
            label_keywords = ['label', 'badge']
            for keyword in label_keywords:
                for element in body.find_all(['p', 'span', 'small', 'a', 'li', 'ul'],
                                            class_=lambda x: x and keyword in str(x).lower()):
                    element.decompose()
            
            # Determine if we should extract all URLs or only those with "news" in href
            extract_all = 'news' in str(list_url).lower()
            
            # Extract news items: (url, thumbnail_url, title)
            news_items = []
            seen_urls = set()
            
            # Strategy 1: Extract from table rows (most common case)
            # Look for table rows that contain both a link and an image
            tables = body.find_all('table')
            table_items_found = 0
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    # Find all links in this row
                    links = row.find_all('a', href=True)
                    for link in links:
                        href = link.get('href')
                        if not href:
                            continue
                        
                        # Filter URLs based on extract_all flag
                        # Also allow URLs with date-based path pattern (YYYY/MM/DD/)
                        if not extract_all and 'news' not in href.lower() and 'activity' not in href.lower() and 'simple' not in href.lower() and not re.search(r'/\d{4}/\d{2}/\d{2}/', href):
                            continue
                        
                        # Skip PDF files
                        if href.lower().endswith('.pdf'):
                            continue
                        
                        full_url = urljoin(list_url, href)
                        if full_url.lower().endswith('.pdf'):
                            continue
                        
                        # Skip URLs that are the landing page itself (with or without query params)
                        if self._url_without_query(full_url).lower() == landing_page_base:
                            continue
                        
                        # Skip bare domain root URLs (e.g. https://archive.example.com/) - never individual articles
                        parsed_url = urlparse(full_url)
                        if parsed_url.path.strip('/') == '':
                            continue
                        
                        # Skip the specific calibre-news archive page
                        normalized_url = full_url.lower().rstrip('/')
                        if normalized_url == 'https://www.equinoxgold.com/calibre-news' or normalized_url.endswith('/calibre-news'):
                            continue
                        
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)
                        
                        # Extract title from link text (the text of the <a> tag)
                        # This is the primary source of the title from the list page
                        title = link.get_text(strip=True)
                        # If no text in link, try to find text in parent td or th
                        if not title:
                            parent_cell = link.find_parent(['td', 'th'])
                            if parent_cell:
                                # Get all text from the cell, but prefer text that's not in nested links
                                cell_text = parent_cell.get_text(separator=' ', strip=True)
                                # Remove any nested link hrefs from the text
                                for nested_link in parent_cell.find_all('a', href=True):
                                    nested_href = nested_link.get('href', '')
                                    if nested_href in cell_text:
                                        cell_text = cell_text.replace(nested_href, '').strip()
                                title = cell_text if cell_text else None
                        
                        # Find thumbnail in the same row
                        thumbnail_url = None
                        # Look for img in the same row
                        imgs_in_row = row.find_all('img')
                        for img in imgs_in_row:
                            img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                            if img_src and not img_src.startswith('data:') and not img_src.startswith('#'):
                                thumbnail_url = urljoin(list_url, img_src)
                                break
                        
                        # Extract date_time from the same row (optional - can be empty)
                        date_time = None
                        date_selectors = [
                            '.date', '.published', '.news-date', '.article-date', 
                            '.publish-date', '[class*="date"]', '[class*="time"]', 
                            'time', '.timestamp'
                        ]
                        for selector in date_selectors:
                            date_elem = row.select_one(selector)
                            if date_elem:
                                date_text = date_elem.get_text().strip()
                                if date_text:
                                    date_time = date_text
                                    break
                                # Check for datetime attribute
                                datetime_attr = date_elem.get('datetime')
                                if datetime_attr:
                                    date_time = datetime_attr
                                    break
                        # If no date found with selectors, try to find date-like text in row cells
                        if not date_time:
                            cells = row.find_all(['td', 'th'])
                            for cell in cells:
                                cell_text = cell.get_text(strip=True)
                                # Simple pattern matching for dates (YYYY-MM-DD, MM/DD/YYYY, etc.)
                                if re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', cell_text) or \
                                   re.search(r'\d{1,2}[-/]\d{1,2}[-/]\d{4}', cell_text) or \
                                   re.search(r'\w+\s+\d{1,2},?\s+\d{4}', cell_text):
                                    date_time = cell_text
                                    break
                        
                        news_items.append((full_url, thumbnail_url, title, date_time))
                        table_items_found += 1
            
            # Strategy 1.5: Extract from .articles-listing structure (e.g. wajax.com)
            # This handles pages where articles are listed in a specific HTML structure
            # with class "articles-listing" containing "article-item" divs
            articles_listing = body.find(class_='articles-listing')
            if articles_listing and len(news_items) == 0:
                article_items = articles_listing.find_all(class_='article-item')
                for item in article_items:
                    # Skip the article-category section (not related to news URL)
                    category_section = item.find(class_='article-category')
                    if category_section:
                        category_section.decompose()
                    
                    # Extract URL and title from article-title heading or primary-btn link
                    article_url = None
                    title = None
                    
                    title_elem = item.find(class_='article-title')
                    if title_elem:
                        title_link = title_elem.find('a', href=True)
                        if title_link:
                            article_url = urljoin(list_url, title_link['href'])
                            title = title_link.get_text(strip=True)
                    
                    # Fallback: try the "More Details" button
                    if not article_url:
                        btn_link = item.find('a', class_='primary-btn', href=True)
                        if btn_link:
                            article_url = urljoin(list_url, btn_link['href'])
                            if not title:
                                title = btn_link.get_text(strip=True)
                    
                    if not article_url:
                        continue
                    
                    # Skip URLs that are the landing page itself
                    if self._url_without_query(article_url).lower() == landing_page_base:
                        continue
                    
                    if article_url in seen_urls:
                        continue
                    seen_urls.add(article_url)
                    
                    # Extract thumbnail from article-featured-image
                    thumbnail_url = None
                    featured_img_container = item.find(class_='article-featured-image')
                    if featured_img_container:
                        img = featured_img_container.find('img')
                        if img:
                            img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                            if img_src and not img_src.startswith('data:') and not img_src.startswith('#'):
                                thumbnail_url = urljoin(list_url, img_src)
                    
                    # Extract date_time if available
                    date_time = None
                    date_selectors = ['.date', '.published', '.news-date', '.article-date', 'time']
                    for selector in date_selectors:
                        date_elem = item.select_one(selector)
                        if date_elem:
                            date_text = date_elem.get_text(strip=True)
                            if date_text:
                                date_time = date_text
                                break
                    
                    news_items.append((article_url, thumbnail_url, title, date_time))
            
            # Strategy 2: Extract from page structures ignoring tables (fallback when no table or no URLs found in table)
            # If no table exists or no news URLs found in tables, search entire page ignoring tables
            if (len(tables) == 0 or table_items_found == 0) and len(news_items) == 0:
                # Create a copy of body and remove all tables to ignore them completely
                body_copy = BeautifulSoup(str(body), 'html.parser')
                body_copy_body = body_copy.find('body')
                if body_copy_body:
                    # Remove all tables from the search area
                    for table in body_copy_body.find_all('table'):
                        table.decompose()
                    
                    # Find all links in the page (ignoring tables)
                    all_links = body_copy_body.find_all('a', href=True)
                    
                    # Process each link found
                    for link in all_links:
                        href = link.get('href')
                        if not href:
                            continue
                        
                        # Filter URLs based on extract_all flag
                        # Also allow URLs with date-based path pattern (YYYY/MM/DD/)
                        if not extract_all and 'news' not in href.lower() and 'activity' not in href.lower() and 'simple' not in href.lower() and not re.search(r'/\d{4}/\d{2}/\d{2}/', href):
                            continue
                        
                        # Skip PDF files
                        if href.lower().endswith('.pdf'):
                            continue
                        
                        full_url = urljoin(list_url, href)
                        if full_url.lower().endswith('.pdf'):
                            continue
                        
                        # Skip URLs that are the landing page itself (with or without query params)
                        if self._url_without_query(full_url).lower() == landing_page_base:
                            continue
                        
                        # Skip bare domain root URLs (e.g. https://archive.example.com/) - never individual articles
                        parsed_url = urlparse(full_url)
                        if parsed_url.path.strip('/') == '':
                            continue
                        
                        # Skip the specific calibre-news archive page
                        normalized_url = full_url.lower().rstrip('/')
                        if normalized_url == 'https://www.equinoxgold.com/calibre-news' or normalized_url.endswith('/calibre-news'):
                            continue
                        
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)
                        
                        # Extract title from link text
                        title = link.get_text(strip=True)
                        # If no text in link, try to find text in parent elements
                        if not title:
                            # Try to find heading in nearby elements
                            parent = link.find_parent(['div', 'article', 'section', 'li', 'p'])
                            if parent:
                                # Try to get text from headings in the parent
                                heading = parent.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                                if heading:
                                    title = heading.get_text(strip=True)
                                else:
                                    # Get text from parent, excluding nested links
                                    parent_text = parent.get_text(separator=' ', strip=True)
                                    # Remove the link's href from text if it appears
                                    link_href = link.get('href', '')
                                    if link_href and link_href in parent_text:
                                        parent_text = parent_text.replace(link_href, '').strip()
                                    # Remove any other nested link hrefs
                                    for nested_link in parent.find_all('a', href=True):
                                        nested_href = nested_link.get('href', '')
                                        if nested_href and nested_href in parent_text:
                                            parent_text = parent_text.replace(nested_href, '').strip()
                                    if parent_text:
                                        # Take first part of parent text as title
                                        title = parent_text.split('\n')[0].strip()[:200]
                        
                        # Find thumbnail and date from the same parent container (sibling divs)
                        thumbnail_url = None
                        date_time = None
                        
                        # Find the parent container - look for a parent that has multiple direct children (sibling structure)
                        parent = link.find_parent(['div', 'article', 'section', 'li'])
                        # Try to find a more suitable parent container if current parent has siblings
                        # Look up the hierarchy to find a container with sibling divs
                        current = link.find_parent(['div', 'article', 'section'])
                        best_parent = parent  # Keep the immediate parent as fallback
                        while current:
                            # Check if this container has multiple direct children (sibling structure)
                            direct_children = [child for child in current.children if hasattr(child, 'name') and child.name]
                            if len(direct_children) >= 2:  # Has multiple siblings, likely the container we want
                                best_parent = current
                                break
                            current = current.find_parent(['div', 'article', 'section'])
                        
                        # Use the best parent found (container with siblings) or fallback to immediate parent
                        parent = best_parent
                        
                        if parent:
                            # Look for images in the parent container - check direct children first (sibling divs)
                            direct_children = [child for child in parent.children if hasattr(child, 'find_all')]
                            for child in direct_children:
                                # Check this direct child (sibling div) for images
                                imgs_in_child = child.find_all('img', recursive=True)
                                if imgs_in_child:
                                    img = imgs_in_child[0]
                                    img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                                    if img_src and not img_src.startswith('data:') and not img_src.startswith('#'):
                                        thumbnail_url = urljoin(list_url, img_src)
                                        break  # Use the first valid image found in sibling divs
                            
                            # If no image found in direct children, try recursive search in entire parent (fallback)
                            if not thumbnail_url:
                                imgs_near_link = parent.find_all('img', recursive=True)
                                for img in imgs_near_link:
                                    img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                                    if img_src and not img_src.startswith('data:') and not img_src.startswith('#'):
                                        thumbnail_url = urljoin(list_url, img_src)
                                        break  # Use the first valid image found in the container
                            
                            # Extract date_time - first try date selectors
                            date_selectors = [
                                '.module_date-time', '.date', '.published', '.news-date', '.article-date', 
                                '.publish-date', '[class*="date"]', '[class*="time"]', 
                                '[class*="date-time"]', 'time', '.timestamp'
                            ]
                            for selector in date_selectors:
                                date_elem = parent.select_one(selector)
                                if date_elem:
                                    date_text = date_elem.get_text().strip()
                                    if date_text:
                                        date_time = date_text
                                        break
                                    # Check for datetime attribute
                                    datetime_attr = date_elem.get('datetime')
                                    if datetime_attr:
                                        date_time = datetime_attr
                                        break
                            
                            # If no date found with selectors, search through direct children (sibling divs) for date patterns
                            if not date_time:
                                # Look through direct children first (sibling divs)
                                direct_children = [child for child in parent.children if hasattr(child, 'get_text')]
                                for child in direct_children:
                                    child_text = child.get_text(separator=' ', strip=True)
                                    # More comprehensive date pattern matching
                                    date_patterns = [
                                        r'\b\w+\s+\d{1,2},?\s+\d{4}(?:\s*[-–]\s*\d{1,2}:\d{2}\s*(?:am|pm|AM|PM))?\b',  # Month Day, Year with optional time (e.g., "January 8, 2026 - 07:30 am")
                                        r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b',  # YYYY-MM-DD or YYYY/MM/DD
                                        r'\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b',  # MM/DD/YYYY or DD/MM/YYYY
                                        r'\b\w+\s+\d{1,2},?\s+\d{4}\b',      # Month Day, Year (e.g., "December 16, 2025")
                                        r'\b\d{1,2}\s+\w+\s+\d{4}\b',        # Day Month Year
                                    ]
                                    for pattern in date_patterns:
                                        date_match = re.search(pattern, child_text, re.IGNORECASE)
                                        if date_match:
                                            date_time = date_match.group().strip()
                                            break
                                    if date_time:
                                        break
                            
                            # If still not found, try pattern matching in the entire parent text (fallback)
                            if not date_time:
                                parent_text = parent.get_text(separator=' ', strip=True)
                                date_patterns = [
                                    r'\b\w+\s+\d{1,2},?\s+\d{4}(?:\s*[-–]\s*\d{1,2}:\d{2}\s*(?:am|pm|AM|PM))?\b',  # Month Day, Year with optional time
                                    r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b',
                                    r'\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b',
                                    r'\b\w+\s+\d{1,2},?\s+\d{4}\b',
                                    r'\b\d{1,2}\s+\w+\s+\d{4}\b',
                                ]
                                for pattern in date_patterns:
                                    date_match = re.search(pattern, parent_text, re.IGNORECASE)
                                    if date_match:
                                        date_time = date_match.group().strip()
                                        break
                        
                        # Add news item even if thumbnail or date_time is empty
                        news_items.append((full_url, thumbnail_url, title, date_time))
            
            extraction_mode = "all URLs" if extract_all else "URLs containing 'news'"
            thumbnails_found = sum(1 for _, thumb, _, _ in news_items if thumb)
            titles_found = sum(1 for _, _, title, _ in news_items if title)
            dates_found = sum(1 for _, _, _, dt in news_items if dt)
            print(f"Found {len(news_items)} URLs from list page (extracted {extraction_mode}, {thumbnails_found} with thumbnails, {titles_found} with titles, {dates_found} with dates)")
            return news_items
            
        except Exception as e:
            print(f"Error extracting news URLs: {str(e)}")
            import traceback
            traceback.print_exc()
            return []
    
    def _normalize_geo_area_filter(self, geo_area: List[str]) -> Optional[frozenset]:
        """Normalize geo_area filter values and return valid set, or None if invalid/empty."""
        if not geo_area:
            return None
        normalized = set()
        for area in geo_area:
            area_clean = str(area).strip()
            if not area_clean:
                continue
            # Case-insensitive match against valid areas
            for valid in VALID_GEO_AREAS:
                if area_clean.lower() == valid.lower():
                    normalized.add(valid)
                    break
            # Handle common typo "Nort America" -> "North America"
            if area_clean.lower() in ("nort america", "northamerica"):
                normalized.add("North America")
        return frozenset(normalized) if normalized else None
    
    async def _scrape_news_concurrently(self, news_urls: List[str], max_news: int, url_to_thumbnail: Optional[Dict[str, Optional[str]]] = None, url_to_title: Optional[Dict[str, Optional[str]]] = None, url_to_date_time: Optional[Dict[str, Optional[str]]] = None, geo_filter: Optional[frozenset] = None) -> List[NewsDetails]:
        """Scrape multiple news articles concurrently, stopping once max_news are collected
        
        Args:
            news_urls: List of news article URLs to scrape
            max_news: Maximum number of news articles to collect
            url_to_thumbnail: Optional dictionary mapping URLs to thumbnail URLs from list page
            url_to_title: Optional dictionary mapping URLs to titles from list page
            url_to_date_time: Optional dictionary mapping URLs to date_time strings from list page
        """
        news_items: List[NewsDetails] = []
        total_urls = len(news_urls)
        index = 0
        max_concurrent = 5  # Limit concurrent requests
        
        if url_to_thumbnail is None:
            url_to_thumbnail = {}
        if url_to_title is None:
            url_to_title = {}
        if url_to_date_time is None:
            url_to_date_time = {}
        
        geo_filter_desc = f" (filtered by geo_area: {sorted(geo_filter)})" if geo_filter else ""
        print(f"Preparing to scrape up to {max_news} news articles from {total_urls} news URLs{geo_filter_desc}")
        
        while index < total_urls and len(news_items) < max_news:
            remaining_needed = max_news - len(news_items)
            batch_size = min(max_concurrent, remaining_needed, total_urls - index)
            batch_urls = news_urls[index : index + batch_size]
            index += batch_size
            
            print(f"Scraping batch of {len(batch_urls)} news articles (collected so far: {len(news_items)})")
            
            semaphore = asyncio.Semaphore(max_concurrent)
            
            async def scrape_with_semaphore(url: str) -> Optional[NewsDetails]:
                async with semaphore:
                    try:
                        thumbnail_url = url_to_thumbnail.get(url)
                        list_title = url_to_title.get(url)
                        list_date_time = url_to_date_time.get(url)
                        return await self._scrape_single_news(url, thumbnail_url=thumbnail_url, list_title=list_title, list_date_time=list_date_time)
                    except Exception as e:
                        print(f"Error scraping news {url}: {str(e)}")
                        return None
            
            tasks = [scrape_with_semaphore(url) for url in batch_urls]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for url, result in zip(batch_urls, batch_results):
                if isinstance(result, Exception):
                    print(f"Exception in news scraping for {url}: {str(result)}")
                    continue
                
                if isinstance(result, NewsDetails):
                    # Apply geo_area filter if specified
                    if geo_filter and result.geo_area and result.geo_area not in geo_filter:
                        print(f"Skipping news '{result.title}' - geo_area '{result.geo_area}' not in filter {sorted(geo_filter)}")
                        continue
                    # If geo_filter is set but article has no detected geo_area, skip it
                    if geo_filter and not result.geo_area:
                        print(f"Skipping news '{result.title}' - no geo_area detected")
                        continue
                    news_items.append(result)
                    print(f"Collected news '{result.title}' ({len(news_items)}/{max_news})")
                elif result is None:
                    print(f"No news data extracted from {url}; skipping")
                
                if len(news_items) >= max_news:
                    print(f"Reached desired news count ({max_news}); stopping further scraping")
                    break
        
        return news_items
    
    async def _scrape_single_news(self, news_url: str, thumbnail_url: Optional[str] = None, list_title: Optional[str] = None, list_date_time: Optional[str] = None) -> Optional[NewsDetails]:
        """Scrape a single news article
        
        Args:
            news_url: URL of the news article to scrape
            thumbnail_url: Optional thumbnail URL from the news list page (only source for thumbnail)
            list_title: Optional title from the news list page (text of the link)
            list_date_time: Optional date_time string from the news list page
        """
        try:
            # Create crawler config with wait time for JavaScript rendering
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=1,
                page_timeout=30000,
                delay_before_return_html=5.0
            )
            
            # Crawl the news page with fallback
            try:
                result = await self.crawler.arun(url=str(news_url), config=crawler_config)
            except Exception as crawl_error:
                print(f"Crawl error for {news_url}: {str(crawl_error)}")
                # Try again with simpler config
                simple_config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=1,
                    page_timeout=20000,
                    delay_before_return_html=5.0
                )
                result = await self.crawler.arun(url=str(news_url), config=simple_config)
            
            if not result.success:
                print(f"Failed to crawl URL: {result.error_message}")
                return None
            
            structured_content = await self._build_content_structure(result)
            
            # Extract data using OpenAI for intelligent extraction
            extracted_data = await self._extract_with_openai(
                result,
                structured_content.get('content_html') if structured_content else None
            )
            
            # Check if content exists - if not, try falling back to structured text
            content = extracted_data.get('content', '').strip() if extracted_data.get('content') else ''
            if not content:
                fallback_text = structured_content.get('plain_text') if structured_content else ''
                if fallback_text:
                    content = fallback_text
            
            if not content:
                print(f"No content found for news article {news_url}; skipping")
                return None
            
            # Parse date - prefer date extracted from the detailed article page,
            # fall back to the landing page date only when the detail page has none.
            parsed_date = None
            if extracted_data.get('date_time'):
                parsed_date = self._parse_date(extracted_data['date_time'])
            if not parsed_date and list_date_time:
                parsed_date = self._parse_date(list_date_time)
            
            # Collect all image URLs and video URLs from content blocks
            image_urls = []
            video_urls = []
            if structured_content and structured_content.get('content_blocks'):
                for block in structured_content['content_blocks']:
                    if block.get('type') == 'image' and block.get('src'):
                        img_src = block['src']
                        if img_src and img_src not in image_urls:
                            image_urls.append(img_src)
                    elif block.get('type') == 'video' and block.get('src'):
                        vid_src = block['src']
                        if vid_src and vid_src not in video_urls:
                            video_urls.append(vid_src)
            
            # Also check extracted_data for image_urls/video_urls arrays (from _extract_basic_info)
            if extracted_data.get('image_urls'):
                for img_url in extracted_data['image_urls']:
                    if img_url and img_url not in image_urls:
                        image_urls.append(img_url)
            if extracted_data.get('video_urls'):
                for vid_url in extracted_data['video_urls']:
                    if vid_url and vid_url not in video_urls:
                        video_urls.append(vid_url)
            
            # Fallback to primary_image/video or singular image_url/video_url if no images/videos found in blocks
            if not image_urls:
                primary_img = extracted_data.get('image_url') or (structured_content.get('primary_image') if structured_content else None)
                if primary_img:
                    image_urls.append(primary_img)
            if not video_urls:
                primary_vid = extracted_data.get('video_url') or (structured_content.get('primary_video') if structured_content else None)
                if primary_vid:
                    video_urls.append(primary_vid)
            
            # Thumbnail only comes from landing page, not from detailed news page
            final_thumbnail_url = thumbnail_url
            
            # Always use title from list page (link text from <a> tag), not from detailed page
            # The title should come from the list page where the news URL link is located
            final_title = list_title if list_title else 'Untitled'
            
            if final_title == 'Untitled':
                print(f"Skipping news article with 'Untitled' title: {news_url}")
                return None
            
            short_description = extracted_data.get('short_description') or self._create_short_description(content)
            tagline = extracted_data.get('tagline')
            if not tagline and structured_content:
                subheadings = structured_content.get('subheadings') or []
                if subheadings:
                    tagline = subheadings[0]
            
            # Categorize the news article
            category = await self._categorize_news(final_title, content, short_description, tagline)
            
            # Detect geographic area of the news
            detected_geo = await self._detect_geo_area(final_title, content, short_description, tagline)
            
            return NewsDetails(
                title=final_title,
                image_urls=image_urls if image_urls else None,
                video_urls=video_urls if video_urls else None,
                thumbnail_url=final_thumbnail_url,
                content=content,
                content_html=structured_content.get('content_html') if structured_content else None,
                content_style=structured_content.get('content_style') if structured_content else None,
                content_blocks=structured_content.get('content_blocks') if structured_content else None,
                content_stats=structured_content.get('content_stats') if structured_content else None,
                links=structured_content.get('links') if structured_content else None,
                subheadings=structured_content.get('subheadings') if structured_content else None,
                date_time=parsed_date,
                author=extracted_data.get('author'),
                tagline=tagline,
                short_description=short_description,
                category=category,
                geo_area=detected_geo,
                source_url=str(news_url)
            )
            
        except Exception as e:
            print(f"Error scraping single news article {news_url}: {str(e)}")
            return None
    
    async def _categorize_news(self, title: str, content: str, short_description: Optional[str] = None, tagline: Optional[str] = None) -> Optional[str]:
        """
        Categorize news article into one of 6 categories using OpenAI:
        Financial, Precious Metals/stones, Critical Minerals, Base Mineral, Indigenous, Technology
        """
        try:
            # Build context for categorization
            context_parts = []
            if title:
                context_parts.append(f"Title: {title}")
            if tagline:
                context_parts.append(f"Tagline: {tagline}")
            if short_description:
                context_parts.append(f"Description: {short_description}")
            
            # Use first 2000 chars of content for categorization
            content_snippet = content[:2000] if content else ""
            if content_snippet:
                context_parts.append(f"Content: {content_snippet}")
            
            context = "\n\n".join(context_parts)
            
            prompt = f"""Categorize the following news article into exactly ONE of these 6 categories:

1. **Financial** - News about financial markets, investments, banking, economic policies, stock markets, mergers & acquisitions, corporate finance, economic indicators, monetary policy, fiscal policy, financial regulations, market trends, economic forecasts, business financial performance, financial services, cryptocurrency (when financial focus), economic development, trade finance, etc.

2. **Precious Metals/stones** - News about gold, silver, platinum, palladium, diamonds, gemstones, precious metal mining, precious metal prices, jewelry industry, precious metal trading, gemstone mining, precious metal exploration, precious metal refining, precious metal markets, etc.

3. **Critical Minerals** - News about rare earth elements, lithium, cobalt, nickel, graphite, manganese, tungsten, molybdenum, vanadium, antimony, beryllium, chromium, germanium, indium, rhenium, tantalum, tellurium, tin, titanium, zinc (when critical), critical mineral supply chains, battery minerals, technology minerals, strategic minerals, critical mineral mining, critical mineral exploration, critical mineral processing, etc.

4. **Base Mineral** - News about iron ore, copper, aluminum, lead, zinc (general), nickel (general), tin, bauxite, base metal mining, base metal prices, base metal markets, base metal exploration, base metal production, steel industry, base metal refining, etc.

5. **Indigenous** - News about Indigenous communities, First Nations, Indigenous rights, Indigenous land claims, Indigenous partnerships, Indigenous businesses, Indigenous culture, Indigenous economic development, Indigenous employment, Indigenous education, Indigenous health, Indigenous governance, treaty negotiations, reconciliation, Indigenous consultation, etc.

6. **Technology** - News about mining technology, automation, AI in mining, digital transformation, software, hardware, innovation, robotics, IoT, data analytics, cybersecurity, technology companies, tech startups, software development, technology infrastructure, etc.

**Rules:**
- Return ONLY the category name exactly as listed above (e.g., "Financial", "Precious Metals/stones", "Critical Minerals", "Base Mineral", "Indigenous", "Technology")
- If the article clearly fits multiple categories, choose the PRIMARY/MOST RELEVANT category
- If the article doesn't clearly fit any category, choose the closest match
- Return only the category name, nothing else

News Article:
{context}

Category:"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a news categorization expert. Return only the exact category name."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=50
            )
            
            category = response.choices[0].message.content.strip()
            
            # Validate category is one of the 6 valid options
            valid_categories = [
                "Financial",
                "Precious Metals/stones",
                "Critical Minerals",
                "Base Mineral",
                "Indigenous",
                "Technology"
            ]
            
            # Normalize category (remove quotes, extra whitespace)
            category = category.strip('"\'')
            
            # Check if category matches any valid option (case-insensitive)
            for valid_cat in valid_categories:
                if category.lower() == valid_cat.lower():
                    print(f"Categorized news as: {valid_cat}")
                    return valid_cat
            
            # If no exact match, try to find partial match
            category_lower = category.lower()
            for valid_cat in valid_categories:
                if valid_cat.lower() in category_lower or category_lower in valid_cat.lower():
                    print(f"Categorized news as: {valid_cat} (from '{category}')")
                    return valid_cat
            
            # Fallback: return the category as-is if no match found
            print(f"Warning: Could not match category '{category}', returning as-is")
            return category if category else None
            
        except Exception as e:
            print(f"Error categorizing news: {e}")
            return None
    
    async def _detect_geo_area(self, title: str, content: str, short_description: Optional[str] = None, tagline: Optional[str] = None) -> Optional[str]:
        """
        Detect the primary geographic area of a news article using OpenAI.
        Returns one of: North America, South America, Europe, Africa, Asia, Oceania, Antarctica
        """
        try:
            context_parts = []
            if title:
                context_parts.append(f"Title: {title}")
            if tagline:
                context_parts.append(f"Tagline: {tagline}")
            if short_description:
                context_parts.append(f"Description: {short_description}")
            
            content_snippet = content[:2000] if content else ""
            if content_snippet:
                context_parts.append(f"Content: {content_snippet}")
            
            context = "\n\n".join(context_parts)
            
            prompt = f"""Determine the PRIMARY geographic area/continent this news article is about or located in.

Consider: locations mentioned, where events occur, company headquarters, mining sites, country names, regions, etc.

Return exactly ONE of these 7 options:
- North America
- South America
- Europe
- Africa
- Asia
- Oceania
- Antarctica

Rules:
- Return ONLY the area name exactly as listed above
- Choose the PRIMARY/MOST RELEVANT geographic focus
- If the article spans multiple regions, choose the main one
- If truly global or cannot determine, return "North America" as default
- Oceania includes Australia, New Zealand, Pacific islands

News Article:
{context}

Primary geographic area:"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a geographic classification expert. Return only the exact area name."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=30
            )
            
            geo_area = response.choices[0].message.content.strip().strip('"\'')
            
            for valid in VALID_GEO_AREAS:
                if geo_area.lower() == valid.lower():
                    print(f"Detected geo_area: {valid}")
                    return valid
            
            # Normalize common variations
            geo_lower = geo_area.lower()
            if "north america" in geo_lower or "north america" == geo_lower:
                return "North America"
            if "south america" in geo_lower:
                return "South America"
            if "europe" in geo_lower:
                return "Europe"
            if "africa" in geo_lower:
                return "Africa"
            if "asia" in geo_lower:
                return "Asia"
            if "oceania" in geo_lower or "australia" in geo_lower or "pacific" in geo_lower:
                return "Oceania"
            if "antarctica" in geo_lower:
                return "Antarctica"
            
            print(f"Warning: Could not match geo_area '{geo_area}', using North America as default")
            return "North America"
            
        except Exception as e:
            print(f"Error detecting geo_area: {e}")
            return None
    
    async def _extract_with_openai(self, result, article_html: Optional[str] = None) -> Dict[str, Any]:
        """Extract news data using OpenAI GPT-4o-mini"""
        try:
            # Get the HTML content
            html_content = result.html
            
            # Clean HTML to reduce token usage - focus on body content only
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove unwanted elements (header, footer, nav, scripts, styles)
            for element in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
                element.decompose()
            
            # Try to get only body content, excluding header and footer
            body_content = soup.find('body')
            if body_content:
                # Remove elements with "menu" or "footer" in class or id name
                # Exception: Don't remove elements with "page--white-menu" in class name
                ignore_keywords = ['menu', 'footer']
                for keyword in ignore_keywords:
                    # Remove by class - check if keyword appears anywhere in class string
                    # But exclude elements with "page--white-menu" in class name
                    for element in body_content.find_all(['div', 'section', 'aside', 'nav', 'header'], 
                                                        class_=lambda x: x and keyword in str(x).lower() and 'page--white-menu' not in str(x).lower()):
                        element.decompose()
                    # Remove by id - check if keyword appears anywhere in id string
                    for element in body_content.find_all(['div', 'section', 'aside', 'nav', 'header'], 
                                                        id=lambda x: x and keyword in str(x).lower()):
                        element.decompose()
                
                # Remove elements with "undefined" in class name using CSS selector (more reliable)
                try:
                    for element in body_content.select('[class*="undefined"]'):
                        element.decompose()
                except Exception:
                    pass
                
                # Also check all elements directly for undefined class
                elements_to_remove = []
                for element in body_content.find_all(True):
                    if element is None or not hasattr(element, 'get'):
                        continue
                    try:
                        class_attr = element.get('class')
                        if class_attr:
                            if isinstance(class_attr, list):
                                if 'undefined' in [c.lower() for c in class_attr]:
                                    elements_to_remove.append(element)
                            elif 'undefined' in str(class_attr).lower():
                                elements_to_remove.append(element)
                    except (AttributeError, TypeError):
                        continue
                
                for element in elements_to_remove:
                    try:
                        if element and hasattr(element, 'decompose'):
                            element.decompose()
                    except (AttributeError, ValueError):
                        pass
                
                # Remove img tags with specific src pattern (expoactivity images)
                try:
                    for img in body_content.find_all('img', src=True):
                        src = img.get('src', '')
                        if src and 'themes/abitibi/img/expoactivity' in src:
                            img.decompose()
                except Exception:
                    pass
                
                # Remove elements with "popup" or "top__line" in id name
                elements_to_remove = []
                for element in body_content.find_all(True):  # Check all elements
                    if element is None or not hasattr(element, 'get'):
                        continue
                    try:
                        id_attr = element.get('id', '')
                        if id_attr and ('popup' in str(id_attr).lower() or 'top__line' in str(id_attr).lower()):
                            elements_to_remove.append(element)
                    except (AttributeError, TypeError):
                        continue
                
                for element in elements_to_remove:
                    try:
                        element.decompose()
                    except (AttributeError, ValueError):
                        pass
                
                # Remove captcha elements
                captcha_keywords = ['captcha', 'recaptcha', 'hcaptcha', 'turnstile', 'cloudflare']
                for keyword in captcha_keywords:
                    # Remove by class
                    for element in body_content.find_all(['div', 'section', 'iframe', 'form'], class_=lambda x: x and keyword in str(x).lower()):
                        element.decompose()
                    # Remove by id
                    for element in body_content.find_all(['div', 'section', 'iframe', 'form'], id=lambda x: x and keyword in str(x).lower()):
                        element.decompose()
                    # Remove iframes with captcha in src
                    for element in body_content.find_all('iframe', src=lambda x: x and keyword in str(x).lower()):
                        element.decompose()
                
                cleaned_text = body_content.get_text()
            else:
                cleaned_text = soup.get_text()
            
            # Remove extra whitespace and limit to avoid token limits
            cleaned_text = ' '.join(cleaned_text.split())
            cleaned_text = cleaned_text[:8000]
            
            print(f"Cleaned text length: {len(cleaned_text)} characters")
            print(f"Cleaned text preview: {cleaned_text[:200]}...")
            
            structured_snippet = (article_html or html_content)
            structured_snippet = structured_snippet[:4000] if structured_snippet else ''
            
            # Create prompt for OpenAI
            today_str = datetime.now().strftime("%Y-%m-%d")
            prompt = f"""
You are a news data extraction expert. Extract the following information from this news webpage content:

1. **title**: The main news article title/headline - MUST be a detailed, specific title describing the news content. 
   - DO NOT use generic titles like "News" or "news"
   - Extract the actual, complete headline/title of the article
   - The title should be descriptive and specific to the article content
   - If you cannot find a specific, detailed title, return an empty string for the title field

2. **image_url**: The main news image URL (if found in the HTML)
3. **video_url**: Any video URL if present
4. **content**: The full news article content/text
5. **date_time**: The ORIGINAL PUBLICATION DATE of this specific news article.
   - Look for dates near the article title, byline, or at the beginning of the article.
   - Common labels: "Published", "Posted", "Date", or a date shown right below the headline.
   - Return the date in the format found on the page (e.g., "March 15, 2025", "2025-03-15").
   - CRITICAL: Do NOT use today's date ({today_str}). Do NOT guess or make up a date.
   - Do NOT use copyright year, footer dates, or unrelated dates from sidebars/widgets.
   - If no clear publication date is found in the article, return an empty string.
6. **author**: Article author name
7. **tagline**: Article tagline or subtitle
8. **short_description**: Brief summary or description (2-3 sentences)

Return the data as a JSON object with these exact keys. If a field is not found, use an empty string.

IMPORTANT: The title must be the actual article headline, not a generic word like "News". If you cannot find a specific, detailed title, return an empty string for the title field.

Webpage content:
{cleaned_text}

HTML snippet for images (first 2000 chars):
{structured_snippet}
"""
            
            # Call OpenAI API
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a data extraction expert. Always return valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=2000
            )
            
            # Parse the response
            extracted_data = json.loads(response.choices[0].message.content)
            
            print(f"Successfully extracted data with OpenAI: {extracted_data.get('title', '')[:50]}...")
            return extracted_data
            
        except Exception as e:
            print(f"Error in OpenAI extraction: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to basic extraction
            return self._extract_basic_info(result)
    
    def _process_extracted_content(self, result) -> Dict[str, Any]:
        """Process the extracted content from Crawl4AI result (following sample code pattern)"""
        try:
            # Handle different types of extracted content (exactly like sample code)
            if hasattr(result, 'extracted_content') and result.extracted_content:
                extracted_content = result.extracted_content
                
                # If it's a list, take the first item
                if isinstance(extracted_content, list) and len(extracted_content) > 0:
                    data = extracted_content[-1]
                elif isinstance(extracted_content, str):
                    data = json.loads(extracted_content)[-1]
                else:
                    data = extracted_content
                
                # Validate and clean the data (following sample code pattern)
                cleaned_data = {
                    "title": str(data.get("title", "")).strip() or "",
                    "image_url": str(data.get("image_url", "")).strip() or "",
                    "video_url": str(data.get("video_url", "")).strip() or "",
                    "content": str(data.get("content", "")).strip() or "",
                    "date_time": str(data.get("date_time", "")).strip() or "",
                    "author": str(data.get("author", "")).strip() or "",
                    "tagline": str(data.get("tagline", "")).strip() or "",
                    "short_description": str(data.get("short_description", "")).strip() or ""
                }
                
                print(f"Successfully extracted data with Crawl4AI AI: {cleaned_data['title'][:50]}...")
                return cleaned_data
            else:
                print("No content extracted by Crawl4AI AI, falling back to basic extraction")
                return self._extract_basic_info(result)
                
        except (json.JSONDecodeError, TypeError, IndexError) as e:
            print(f"Failed to parse Crawl4AI AI response: {e}")
            return self._extract_basic_info(result)
    
    def _parse_date(self, date_string: str) -> Optional[datetime]:
        """Parse date string to datetime object.
        
        Rejects dates that resolve to today (common LLM hallucination).
        """
        try:
            # Common date formats to try
            date_formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%B %d, %Y",
                "%d %B %Y",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ]
            
            parsed = None
            
            for fmt in date_formats:
                try:
                    parsed = datetime.strptime(date_string.strip(), fmt)
                    break
                except ValueError:
                    continue
            
            # If no format matches, try to extract date using regex
            if not parsed:
                date_patterns = [
                    r'\d{4}-\d{2}-\d{2}',
                    r'\d{2}/\d{2}/\d{4}',
                    r'\d{1,2}\s+\w+\s+\d{4}'
                ]
                
                for pattern in date_patterns:
                    match = re.search(pattern, date_string)
                    if match:
                        matched_date = match.group()
                        for fmt in date_formats:
                            try:
                                parsed = datetime.strptime(matched_date, fmt)
                                break
                            except ValueError:
                                continue
                    if parsed:
                        break
            
            if parsed and parsed.date() == datetime.now().date():
                print(f"Rejecting date '{date_string}' — it equals today's date (likely hallucinated)")
                return None
            
            return parsed
            
        except Exception:
            return None
    
    def _create_short_description(self, content: str, limit: int = 240) -> Optional[str]:
        """Generate a concise summary from the extracted content"""
        if not content:
            return None
        normalized = ' '.join(content.split())
        if len(normalized) <= limit:
            return normalized
        truncated = normalized[:limit].rsplit(' ', 1)[0]
        return f"{truncated}..."
    
    # ── CSS extraction helpers ───────────────────────────────────────────
    
    def _rewrite_css_urls(self, css_text: str, base_url: str) -> str:
        """Rewrite relative url() references in CSS to absolute URLs so they
        work when the CSS is rendered on a different domain (the frontend)."""
        if not css_text or not base_url:
            return css_text
        
        def _replace(match):
            raw = match.group(1).strip().strip('"').strip("'")
            if not raw or raw.startswith('data:') or raw.startswith('http://') or raw.startswith('https://') or raw.startswith('//'):
                return match.group(0)
            absolute = urljoin(base_url, raw)
            return f'url("{absolute}")'
        
        return re.sub(r'url\(([^)]+)\)', _replace, css_text)
    
    @staticmethod
    def _is_valid_css(text: str) -> bool:
        """Return True if *text* looks like CSS rather than an HTML error page."""
        if not text or not text.strip():
            return False
        stripped = text.strip()
        # HTML pages returned by 404/redirect start with these
        if stripped[:15].lower().startswith('<!doctype') or stripped[:10].lower().startswith('<html'):
            return False
        if '<head>' in stripped[:500].lower() or '<body>' in stripped[:500].lower():
            return False
        return True
    
    def _resolve_base_url(self, soup: BeautifulSoup, page_url: Optional[str]) -> str:
        """Determine the correct base URL for resolving relative paths.
        
        Checks for a <base href="..."> tag (which many CMS pages use to
        set a root different from the page URL).  Falls back to page_url.
        """
        base_tag = soup.find('base', href=True)
        if base_tag:
            base_href = base_tag['href'].strip()
            if base_href:
                # <base href> may itself be relative – resolve against page URL
                return urljoin(page_url or '', base_href)
        return page_url or ''
    
    async def _extract_page_css(self, soup: BeautifulSoup, page_url: Optional[str]) -> str:
        """Extract ALL raw CSS from the page: <style> tags and external
        <link rel='stylesheet'> files.  Media queries and responsive rules
        are preserved exactly as the origin defined them.
        
        Correctly honours <base href> for resolving relative stylesheet paths
        and validates that fetched resources are actually CSS (not HTML error
        pages).
        """
        css_parts: List[str] = []
        
        # Honour <base href> tag for resolving relative URLs
        base_url = self._resolve_base_url(soup, page_url)
        
        # 1. Inline <style> tags
        for style_tag in soup.find_all('style'):
            css_text = style_tag.string or style_tag.get_text()
            if css_text and css_text.strip() and self._is_valid_css(css_text):
                if base_url:
                    css_text = self._rewrite_css_urls(css_text, base_url)
                css_parts.append(css_text)
        
        # 2. External stylesheets via <link rel="stylesheet">
        for link in soup.find_all('link', rel='stylesheet'):
            href = link.get('href')
            if not href:
                continue
            # Resolve href against the <base href> (or page URL)
            css_url = urljoin(base_url, href)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        css_url,
                        timeout=aiohttp.ClientTimeout(total=15),
                        headers={'User-Agent': 'Mozilla/5.0'}
                    ) as resp:
                        if resp.status != 200:
                            logging.warning("CSS fetch %s returned status %s", css_url, resp.status)
                            continue
                        # Check Content-Type – accept text/css or plain text
                        content_type = (resp.headers.get('Content-Type') or '').lower()
                        if 'html' in content_type:
                            logging.warning("Skipping %s – Content-Type is HTML, not CSS", css_url)
                            continue
                        css_text = await resp.text()
                        if not self._is_valid_css(css_text):
                            logging.warning("Skipping %s – response body looks like HTML, not CSS", css_url)
                            continue
                        # Rewrite relative url() references inside the CSS
                        css_text = self._rewrite_css_urls(css_text, css_url)
                        css_parts.append(f"/* source: {css_url} */\n{css_text}")
            except Exception as e:
                logging.warning("Failed to fetch stylesheet %s: %s", css_url, e)
        
        return '\n\n'.join(css_parts) if css_parts else ''
    
    @staticmethod
    def _generate_responsive_safety_css() -> str:
        """Minimal safety-net CSS appended after the origin's own CSS.
        Prevents content overflow on narrow screens without overriding the
        origin's responsive rules.
        
        The frontend should wrap content_html inside:
            <div class="news-content-wrapper"> ... content_html ... </div>
        """
        return (
            "/* ── responsive safety-net ── */\n"
            ".news-content-wrapper {\n"
            "  max-width: 100%;\n"
            "  width: 100%;\n"
            "  overflow-x: hidden;\n"
            "  box-sizing: border-box;\n"
            "  word-wrap: break-word;\n"
            "  overflow-wrap: break-word;\n"
            "}\n"
            ".news-content-wrapper * {\n"
            "  max-width: 100%;\n"
            "  box-sizing: border-box;\n"
            "}\n"
            ".news-content-wrapper img {\n"
            "  max-width: 100%;\n"
            "  height: auto;\n"
            "}\n"
            ".news-content-wrapper video,\n"
            ".news-content-wrapper iframe,\n"
            ".news-content-wrapper embed,\n"
            ".news-content-wrapper object {\n"
            "  max-width: 100%;\n"
            "  height: auto;\n"
            "}\n"
            ".news-content-wrapper table {\n"
            "  max-width: 100%;\n"
            "  overflow-x: auto;\n"
            "  display: block;\n"
            "}\n"
            ".news-content-wrapper pre,\n"
            ".news-content-wrapper code {\n"
            "  max-width: 100%;\n"
            "  overflow-x: auto;\n"
            "  white-space: pre-wrap;\n"
            "  word-wrap: break-word;\n"
            "}\n"
        )
    
    async def _build_content_structure(self, result) -> Dict[str, Any]:
        """Create sanitized HTML, block metadata, link stats, and extracted CSS
        for the article body.
        
        The content_html preserves the original HTML structure with its class
        names and IDs (NO computed-style inlining) so that the original CSS
        can style it exactly as the source page – including @media responsive
        rules.  All page CSS is returned separately as content_style."""
        try:
            html_content = result.html if hasattr(result, 'html') else ''
            page_url = getattr(result, 'url', None)
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Honour <base href> tag for resolving all relative URLs
            base_url = self._resolve_base_url(soup, page_url)
            
            # 1. Extract ALL raw CSS from the page before modifying the tree.
            #    This preserves @media queries, responsive rules, etc.
            page_css = await self._extract_page_css(soup, page_url)
            
            # 2. Locate the primary content node
            container = self._find_primary_content_node(soup)
            if not container:
                container = soup.find('body') or soup
            
            # 3. Work on a copy so the original soup stays intact
            fragment_soup = BeautifulSoup(str(container), 'html.parser')
            root = fragment_soup.body if fragment_soup.body else fragment_soup
            
            # 4. Sanitize (remove scripts, footers, nav, etc.) but keep
            #    original classes, IDs, and any author-set inline styles.
            self._sanitize_article_fragment(root)
            self._normalize_resource_urls(root, base_url)
            
            # 5. Build metadata
            plain_text = root.get_text(" ", strip=True)
            blocks, stats, first_image, first_video, headings = self._generate_content_blocks(root)
            links = self._collect_links(root, base_url)
            stats['links'] = len(links)
            
            # 6. Serialize – original HTML structure, no computed-style bloat
            content_html = self._serialize_fragment(root)
            
            # 7. Assemble content_style: origin page CSS + responsive safety-net
            safety_css = self._generate_responsive_safety_css()
            if page_css:
                content_style = page_css + "\n\n" + safety_css
            else:
                content_style = safety_css
            
            return {
                'content_html': content_html,
                'content_style': content_style,
                'plain_text': plain_text,
                'content_blocks': blocks,
                'content_stats': stats,
                'links': links,
                'primary_image': first_image,
                'primary_video': first_video,
                'subheadings': headings
            }
        except Exception as exc:
            print(f"Failed to assemble structured content: {exc}")
            return {
                'content_html': None,
                'content_style': None,
                'plain_text': None,
                'content_blocks': [],
                'content_stats': {
                    'paragraphs': 0,
                    'headings': 0,
                    'images': 0,
                    'videos': 0,
                    'embeds': 0,
                    'lists': 0,
                    'quotes': 0,
                    'links': 0
                },
                'links': [],
                'primary_image': None,
                'primary_video': None,
                'subheadings': []
            }
    
    def _find_primary_content_node(self, soup: BeautifulSoup):
        # Prefer wrappers that include title + body (e.g. #conference, .content) so content_html has full data
        selectors = [
            '#conference',
            '.conference_wrap',
            'article',
            '.article-content',
            '.article-body',
            '.news-content',
            '.news-article',
            '.post-content',
            '.entry-content',
            '.story-content',
            'main article',
            'main',
            '.content'
        ]
        for selector in selectors:
            candidate = soup.select_one(selector)
            if candidate and len(candidate.get_text(" ", strip=True)) > 100:
                return candidate
        if soup.find('article'):
            return soup.find('article')
        if soup.find('main'):
            return soup.find('main')
        return soup.find('body') or soup
    
    def _sanitize_article_fragment(self, root):
        # Remove chrome/scripts and footers (any element whose class contains "footer")
        removable_tags = [
            'script', 'style', 'noscript', 'form', 'button', 'input',
            'svg', 'canvas', 'iframe[title="Consent"]', 'footer', 'header', 'nav', 'menu'
        ]
        for tag_name in removable_tags:
            for tag in root.select(tag_name):
                tag.decompose()
        # Remove elements whose class name contains "footer" (e.g. site-footer, article-footer)
        try:
            for element in root.select('[class*="footer"]'):
                element.decompose()
        except Exception:
            pass
        # Remove elements with "undefined" in class name using CSS selector (more reliable)
        try:
            for element in root.select('[class*="undefined"]'):
                element.decompose()
        except Exception:
            pass
        
        # Also check all elements directly for undefined class
        elements_to_remove = []
        for element in root.find_all(True):
            if element is None or not hasattr(element, 'get'):
                continue
            try:
                class_attr = element.get('class')
                if class_attr:
                    if isinstance(class_attr, list):
                        if 'undefined' in [c.lower() for c in class_attr]:
                            elements_to_remove.append(element)
                    elif 'undefined' in str(class_attr).lower():
                        elements_to_remove.append(element)
            except (AttributeError, TypeError):
                continue
        
        for element in elements_to_remove:
            try:
                if element and hasattr(element, 'decompose'):
                    element.decompose()
            except (AttributeError, ValueError):
                pass
        
        # Remove img tags with specific src pattern (expoactivity images)
        try:
            for img in root.find_all('img', src=True):
                src = img.get('src', '')
                if src and 'themes/abitibi/img/expoactivity' in src:
                    img.decompose()
        except Exception:
            pass
        
        # Remove elements with "popup" or "top__line" in id name
        elements_to_remove = []
        for element in root.find_all(True):  # Check all elements
            if element is None or not hasattr(element, 'get'):
                continue
            try:
                id_attr = element.get('id', '')
                if id_attr and ('popup' in str(id_attr).lower() or 'top__line' in str(id_attr).lower()):
                    elements_to_remove.append(element)
            except (AttributeError, TypeError):
                continue
        
        for element in elements_to_remove:
            try:
                element.decompose()
            except (AttributeError, ValueError):
                pass
        
        # Remove captcha elements
        captcha_keywords = ['captcha', 'recaptcha', 'hcaptcha', 'turnstile', 'cloudflare']
        for keyword in captcha_keywords:
            # Remove by class
            for element in root.find_all(['div', 'section', 'iframe', 'form'], class_=lambda x: x and keyword in str(x).lower()):
                element.decompose()
            # Remove by id
            for element in root.find_all(['div', 'section', 'iframe', 'form'], id=lambda x: x and keyword in str(x).lower()):
                element.decompose()
            # Remove iframes with captcha in src
            for element in root.find_all('iframe', src=lambda x: x and keyword in str(x).lower()):
                element.decompose()
        
        # Keep style (inline styles); strip only event handlers (on*) for safety
        for tag in root.find_all(True):
            cleaned_attrs = {}
            for attr, value in tag.attrs.items():
                attr_lower = attr.lower()
                if attr_lower.startswith('on'):
                    continue
                cleaned_attrs[attr] = value
            tag.attrs = cleaned_attrs
    
    def _normalize_resource_urls(self, root, base_url: Optional[str]):
        if not base_url:
            return
        url_tags = ['a', 'img', 'video', 'source', 'iframe', 'audio']
        for tag in root.find_all(url_tags):
            attr_name = 'href' if tag.name == 'a' else 'src'
            url_value = tag.get(attr_name)
            if not url_value or url_value.startswith('data:'):
                continue
            if url_value.lower().startswith('javascript:'):
                tag[attr_name] = ''
                continue
            absolute = self._absolute_url(url_value, base_url)
            tag[attr_name] = absolute
        for video in root.find_all('video'):
            poster = video.get('poster')
            if poster:
                video['poster'] = self._absolute_url(poster, base_url)
    
    def _generate_content_blocks(self, root):
        stats = {
            'paragraphs': 0,
            'headings': 0,
            'images': 0,
            'videos': 0,
            'embeds': 0,
            'lists': 0,
            'quotes': 0
        }
        blocks: List[Dict[str, Any]] = []
        first_image = None
        first_video = None
        headings: List[str] = []
        max_blocks = 500
        for element in root.find_all(True):
            if len(blocks) >= max_blocks:
                break
            name = element.name
            if name == 'p':
                text = element.get_text(" ", strip=True)
                if text:
                    stats['paragraphs'] += 1
                    blocks.append({'type': 'paragraph', 'text': text, 'html': str(element)})
            elif name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                text = element.get_text(" ", strip=True)
                if text:
                    stats['headings'] += 1
                    headings.append(text)
                    blocks.append({
                        'type': 'heading',
                        'level': name,
                        'text': text,
                        'html': str(element)
                    })
            elif name in ['ul', 'ol']:
                if element.find_parent(['ul', 'ol']):
                    continue
                items = [li.get_text(" ", strip=True) for li in element.find_all('li', recursive=False) if li.get_text(" ", strip=True)]
                if items:
                    stats['lists'] += 1
                    blocks.append({
                        'type': 'list',
                        'ordered': name == 'ol',
                        'items': items,
                        'html': str(element)
                    })
            elif name == 'img':
                src = element.get('src')
                if src:
                    stats['images'] += 1
                    if not first_image:
                        first_image = src
                    caption = None
                    figure = element.find_parent('figure')
                    if figure:
                        caption_tag = figure.find('figcaption')
                        if caption_tag:
                            caption = caption_tag.get_text(" ", strip=True)
                    blocks.append({
                        'type': 'image',
                        'src': src,
                        'alt': element.get('alt'),
                        'caption': caption,
                        'html': str(element)
                    })
            elif name == 'video':
                src = element.get('src')
                if not src:
                    source_tag = element.find('source')
                    if source_tag and source_tag.get('src'):
                        src = source_tag.get('src')
                if src:
                    stats['videos'] += 1
                    if not first_video:
                        first_video = src
                    blocks.append({
                        'type': 'video',
                        'src': src,
                        'html': str(element)
                    })
            elif name == 'iframe':
                src = element.get('src')
                if src:
                    stats['embeds'] += 1
                    blocks.append({
                        'type': 'embed',
                        'src': src,
                        'html': str(element)
                    })
            elif name == 'blockquote':
                text = element.get_text(" ", strip=True)
                if text:
                    stats['quotes'] += 1
                    blocks.append({'type': 'quote', 'text': text, 'html': str(element)})
            elif name == 'table':
                rows = []
                for tr in element.find_all('tr'):
                    cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(['td', 'th'])]
                    if cells:
                        rows.append(cells)
                if rows:
                    blocks.append({'type': 'table', 'rows': rows, 'html': str(element)})
        return blocks, stats, first_image, first_video, headings
    
    def _collect_links(self, root, base_url: Optional[str]) -> List[Dict[str, Optional[str]]]:
        links: List[Dict[str, Optional[str]]] = []
        seen = set()
        for anchor in root.find_all('a', href=True):
            href = anchor.get('href')
            if not href or href.startswith('#') or href.lower().startswith('javascript:'):
                continue
            absolute = self._absolute_url(href, base_url)
            if absolute in seen:
                continue
            seen.add(absolute)
            links.append({
                'href': absolute,
                'text': anchor.get_text(" ", strip=True) or None,
                'title': anchor.get('title')
            })
        return links
    
    def _absolute_url(self, url: str, base_url: Optional[str]) -> str:
        if not url:
            return url
        if url.startswith(('http://', 'https://')):
            return url
        if url.startswith('//'):
            if base_url:
                return urljoin(base_url, url)
            return f"https:{url}" if url.startswith('//') else url
        return urljoin(base_url, url) if base_url else url
    
    def _serialize_fragment(self, root) -> Optional[str]:
        if not root:
            return None
        fragments = []
        for child in getattr(root, 'contents', []):
            if hasattr(child, 'decode'):
                fragments.append(child.decode())
            else:
                fragments.append(str(child))
        serialized = ''.join(fragments).strip()
        return serialized or None
    
    def _extract_basic_info(self, result) -> Dict[str, Any]:
        """Enhanced HTML parsing method to extract news information"""
        try:
            # Get the raw HTML content
            html_content = result.html
            
            # Use BeautifulSoup for extraction
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract title - try multiple selectors with priority
            title = ""
            title_selectors = [
                'h1.news-title',
                'h1.article-title', 
                'h1',
                'title',
                '.news-title',
                '.article-title',
                '[class*="title"]',
                '[class*="headline"]',
                'h2',
                'h3'
            ]
            
            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem and title_elem.get_text().strip():
                    title = title_elem.get_text().strip()
                    break
            
            # Extract main content - try multiple selectors with priority
            content = ""
            content_selectors = [
                '.news-content',
                '.article-content',
                '.content',
                'main',
                'article',
                '.post-content',
                '[class*="content"]',
                '[class*="body"]',
                '.news-body',
                '.article-body'
            ]
            
            for selector in content_selectors:
                content_elem = soup.select_one(selector)
                if content_elem:
                    # Remove unwanted elements
                    for unwanted in content_elem(["script", "style", "nav", "header", "footer", "aside", "menu"]):
                        unwanted.decompose()
                    # Remove elements with "menu" or "footer" in class or id name
                    # Exception: Don't remove elements with "page--white-menu" in class name
                    ignore_keywords = ['menu', 'footer']
                    for keyword in ignore_keywords:
                        # Remove by class - check if keyword appears anywhere in class string
                        # But exclude elements with "page--white-menu" in class name
                        for element in content_elem.find_all(['div', 'section', 'aside', 'nav', 'header'], 
                                                            class_=lambda x: x and keyword in str(x).lower() and 'page--white-menu' not in str(x).lower()):
                            element.decompose()
                        # Remove by id - check if keyword appears anywhere in id string
                        for element in content_elem.find_all(['div', 'section', 'aside', 'nav', 'header'], 
                                                            id=lambda x: x and keyword in str(x).lower()):
                            element.decompose()
                    
                    # Remove elements with "undefined" in class name using CSS selector (more reliable)
                    try:
                        for element in content_elem.select('[class*="undefined"]'):
                            element.decompose()
                    except Exception:
                        pass
                    
                    # Also check all elements directly for undefined class
                    elements_to_remove = []
                    for element in content_elem.find_all(True):
                        if element is None or not hasattr(element, 'get'):
                            continue
                        try:
                            class_attr = element.get('class')
                            if class_attr:
                                if isinstance(class_attr, list):
                                    if 'undefined' in [c.lower() for c in class_attr]:
                                        elements_to_remove.append(element)
                                elif 'undefined' in str(class_attr).lower():
                                    elements_to_remove.append(element)
                        except (AttributeError, TypeError):
                            continue
                    
                    for element in elements_to_remove:
                        try:
                            if element and hasattr(element, 'decompose'):
                                element.decompose()
                        except (AttributeError, ValueError):
                            pass
                    
                    # Remove img tags with specific src pattern (expoactivity images)
                    try:
                        for img in content_elem.find_all('img', src=True):
                            src = img.get('src', '')
                            if src and 'themes/abitibi/img/expoactivity' in src:
                                img.decompose()
                    except Exception:
                        pass
                    
                    # Remove elements with "popup" or "top__line" in id name
                    elements_to_remove = []
                    for element in content_elem.find_all(True):  # Check all elements
                        if element is None or not hasattr(element, 'get'):
                            continue
                        try:
                            id_attr = element.get('id', '')
                            if id_attr and ('popup' in str(id_attr).lower() or 'top__line' in str(id_attr).lower()):
                                elements_to_remove.append(element)
                        except (AttributeError, TypeError):
                            continue
                    
                    for element in elements_to_remove:
                        try:
                            element.decompose()
                        except (AttributeError, ValueError):
                            pass
                    
                    # Remove captcha elements
                    captcha_keywords = ['captcha', 'recaptcha', 'hcaptcha', 'turnstile', 'cloudflare']
                    for keyword in captcha_keywords:
                        for element in content_elem.find_all(['div', 'section', 'iframe', 'form'], class_=lambda x: x and keyword in str(x).lower()):
                            element.decompose()
                        for element in content_elem.find_all(['div', 'section', 'iframe', 'form'], id=lambda x: x and keyword in str(x).lower()):
                            element.decompose()
                        for element in content_elem.find_all('iframe', src=lambda x: x and keyword in str(x).lower()):
                            element.decompose()
                    content = content_elem.get_text().strip()
                    if content and len(content) > 100:  # Ensure we have substantial content
                        break
            
            # If no specific content area found, try to get text from body
            if not content or len(content) < 100:
                body = soup.find('body')
                if body:
                    # Remove script and style elements
                    for script in body(["script", "style", "nav", "header", "footer", "aside", "menu"]):
                        script.decompose()
            # Remove elements with "menu" or "footer" in class or id name
            # Exception: Don't remove elements with "page--white-menu" in class name
            ignore_keywords = ['menu', 'footer']
            for keyword in ignore_keywords:
                # Remove by class - check if keyword appears anywhere in class string
                # But exclude elements with "page--white-menu" in class name
                for element in body.find_all(['div', 'section', 'aside', 'nav', 'header'], 
                                            class_=lambda x: x and keyword in str(x).lower() and 'page--white-menu' not in str(x).lower()):
                    element.decompose()
                # Remove by id - check if keyword appears anywhere in id string
                for element in body.find_all(['div', 'section', 'aside', 'nav', 'header'], 
                                            id=lambda x: x and keyword in str(x).lower()):
                    element.decompose()
                    
                    # Remove elements with "undefined" in class name using CSS selector (more reliable)
                    try:
                        for element in body.select('[class*="undefined"]'):
                            element.decompose()
                    except Exception:
                        pass
                    
                    # Also check all elements directly for undefined class
                    elements_to_remove = []
                    for element in body.find_all(True):
                        if element is None or not hasattr(element, 'get'):
                            continue
                        try:
                            class_attr = element.get('class')
                            if class_attr:
                                if isinstance(class_attr, list):
                                    if 'undefined' in [c.lower() for c in class_attr]:
                                        elements_to_remove.append(element)
                                elif 'undefined' in str(class_attr).lower():
                                    elements_to_remove.append(element)
                        except (AttributeError, TypeError):
                            continue
                    
                    for element in elements_to_remove:
                        try:
                            if element and hasattr(element, 'decompose'):
                                element.decompose()
                        except (AttributeError, ValueError):
                            pass
                    
                    # Remove img tags with specific src pattern (expoactivity images)
                    try:
                        for img in body.find_all('img', src=True):
                            src = img.get('src', '')
                            if src and 'themes/abitibi/img/expoactivity' in src:
                                img.decompose()
                    except Exception:
                        pass
                    
                    # Remove elements with "popup" or "top__line" in id name
                    elements_to_remove = []
                    for element in body.find_all(True):  # Check all elements
                        if element is None or not hasattr(element, 'get'):
                            continue
                        try:
                            id_attr = element.get('id', '')
                            if id_attr and ('popup' in str(id_attr).lower() or 'top__line' in str(id_attr).lower()):
                                elements_to_remove.append(element)
                        except (AttributeError, TypeError):
                            continue
                    
                    for element in elements_to_remove:
                        try:
                            element.decompose()
                        except (AttributeError, ValueError):
                            pass
                    
                    # Remove captcha elements
                    captcha_keywords = ['captcha', 'recaptcha', 'hcaptcha', 'turnstile', 'cloudflare']
                    for keyword in captcha_keywords:
                        for element in body.find_all(['div', 'section', 'iframe', 'form'], class_=lambda x: x and keyword in str(x).lower()):
                            element.decompose()
                        for element in body.find_all(['div', 'section', 'iframe', 'form'], id=lambda x: x and keyword in str(x).lower()):
                            element.decompose()
                        for element in body.find_all('iframe', src=lambda x: x and keyword in str(x).lower()):
                            element.decompose()
                    content = body.get_text().strip()
            
            # Extract image URL - try multiple selectors with priority
            image_url = None
            img_selectors = [
                '.news-image img',
                '.article-image img',
                '.featured-image img',
                '.hero-image img',
                'img[class*="news"]',
                'img[class*="article"]',
                'img[class*="featured"]',
                'img[class*="hero"]',
                'img'
            ]
            
            for selector in img_selectors:
                img_elem = soup.select_one(selector)
                if img_elem and img_elem.get('src'):
                    src = img_elem.get('src')
                    if src and not src.startswith('data:') and not src.startswith('#'):  # Skip data URLs and anchors
                        # Convert relative URLs to absolute if needed
                        if src.startswith('/'):
                            # Try to get base URL from the page
                            base_url = str(result.url) if hasattr(result, 'url') else ""
                            if base_url:
                                from urllib.parse import urljoin
                                image_url = urljoin(base_url, src)
                            else:
                                image_url = src
                        else:
                            image_url = src
                        break
            
            # Extract author - try multiple selectors
            author = None
            author_selectors = [
                '.author',
                '.byline',
                '.news-author',
                '.article-author',
                '[class*="author"]',
                '[class*="byline"]',
                '.writer',
                '.reporter'
            ]
            
            for selector in author_selectors:
                author_elem = soup.select_one(selector)
                if author_elem and author_elem.get_text().strip():
                    author = author_elem.get_text().strip()
                    break
            
            # Extract date - try multiple selectors
            date_time = None
            date_selectors = [
                '.date',
                '.published',
                '.news-date',
                '.article-date',
                '.publish-date',
                '[class*="date"]',
                '[class*="time"]',
                'time',
                '.timestamp'
            ]
            
            for selector in date_selectors:
                date_elem = soup.select_one(selector)
                if date_elem:
                    date_text = date_elem.get_text().strip()
                    if date_text:
                        date_time = date_text
                        break
                    # Check for datetime attribute
                    datetime_attr = date_elem.get('datetime')
                    if datetime_attr:
                        date_time = datetime_attr
                        break
            
            # Extract tagline/subtitle
            tagline = None
            tagline_selectors = [
                '.tagline',
                '.subtitle',
                '.lead',
                '.excerpt',
                '[class*="tagline"]',
                '[class*="subtitle"]'
            ]
            
            for selector in tagline_selectors:
                tagline_elem = soup.select_one(selector)
                if tagline_elem and tagline_elem.get_text().strip():
                    tagline = tagline_elem.get_text().strip()
                    break
            
            # Create short description from content
            short_description = None
            if content:
                # Take first 200 characters as short description
                short_description = content[:200].strip()
                if len(content) > 200:
                    short_description += "..."
            
            return {
                'title': title or 'No title found',
                'content': content or 'No content found',
                'image_url': image_urls[0] if image_urls else None,  # First image for backward compat
                'image_urls': image_urls,  # All images
                'video_url': video_urls[0] if video_urls else None,  # First video for backward compat
                'video_urls': video_urls,  # All videos
                'date_time': date_time,
                'author': author,
                'tagline': tagline,
                'short_description': short_description
            }
            
        except Exception as e:
            print(f"Error in enhanced extraction: {e}")
            import traceback
            traceback.print_exc()
            return {
                'title': 'Extraction Failed',
                'content': 'Failed to extract content',
                'image_url': None,
                'image_urls': [],
                'video_url': None,
                'video_urls': [],
                'date_time': None,
                'author': None,
                'tagline': None,
                'short_description': None
            }
