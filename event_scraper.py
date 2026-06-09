import asyncio
import json
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
import re
from urllib.parse import urljoin, urlparse
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from openai import OpenAI
from bs4 import BeautifulSoup
from style_inliner import extract_and_parse_css, inline_css_styles, get_html_with_computed_styles
from models import (
    EventDetails, EventScrapingResponse, EventContentBlock, 
    EventContentStats, EventLink
)


class EventScraper:
    """Event scraping service using Crawl4AI"""
    
    def __init__(self):
        self.crawler = None
        # Use environment variable or fallback to the provided key
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        # Initialize OpenAI client
        self.openai_client = OpenAI(api_key=self.openai_api_key)
    
    async def __aenter__(self):
        self.crawler = AsyncWebCrawler(verbose=True)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.crawler:
            await self.crawler.close()
    
    async def scrape_events(self, event_list_url: str, max_events: int = 10) -> EventScrapingResponse:
        """
        Scrape events from the given event list URL
        
        Args:
            event_list_url: URL of event list page
            max_events: Maximum number of events to scrape (default: 10)
        """
        try:
            if not self.crawler:
                self.crawler = AsyncWebCrawler(verbose=True)
            
            # Extract all event URLs and thumbnails from the list page
            print(f"Extracting event URLs and thumbnails from: {event_list_url}")
            event_data = await self._extract_event_urls(event_list_url)
            print(f"Found {len(event_data)} events with thumbnails")
            
            if not event_data:
                return EventScrapingResponse(
                    events=[],
                    total_events=0,
                    source_url=event_list_url
                )
            
            # Scrape events concurrently up to max_events limit
            events = await self._scrape_events_concurrently(event_data, max_events)
            
            return EventScrapingResponse(
                events=events,
                total_events=len(events),
                source_url=event_list_url
            )
            
        except Exception as e:
            raise Exception(f"Error scraping events: {str(e)}")
    
    async def _extract_event_urls(self, list_url: str) -> List[Dict[str, str]]:
        """Extract all event URLs and thumbnails from the event list page"""
        try:
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=1,
                page_timeout=30000,
                delay_before_return_html=0.5
            )
            
            result = await self.crawler.arun(url=str(list_url), config=crawler_config)
            
            if not result.success:
                print(f"Failed to crawl event list page: {result.error_message}")
                return []
            
            soup = BeautifulSoup(result.html, 'html.parser')
            
            # Focus on body content only - remove header and footer
            body = soup.find('body')
            if not body:
                return []
            
            # Remove header, footer, nav elements
            for element in body.find_all(['header', 'footer', 'nav']):
                element.decompose()
            
            # Extract event cards with URLs and thumbnails
            event_data = []
            all_links = body.find_all('a', href=True)
            
            for link in all_links:
                href = link.get('href')
                if href and 'event' in href.lower():
                    # Skip PDF files
                    if href.lower().endswith('.pdf'):
                        continue
                    
                    # Skip URLs with hash/fragment identifiers (anchor links)
                    if '#' in href:
                        continue
                    
                    # Skip event submission URLs
                    if 'event-submission' in href.lower():
                        continue
                    
                    full_url = urljoin(list_url, href)
                    # Also check the full URL in case the base URL makes it a PDF
                    if full_url.lower().endswith('.pdf'):
                        continue
                    
                    # Also check the full URL for hash identifiers
                    if '#' in full_url:
                        continue
                    
                    # Also check the full URL for event-submission
                    if 'event-submission' in full_url.lower():
                        continue
                    
                    # Extract thumbnail image from the event card
                    thumbnail_url = self._extract_thumbnail_from_card(link, list_url)
                    
                    # Check if we already have this URL
                    if not any(ed['url'] == full_url for ed in event_data):
                        event_data.append({
                            'url': full_url,
                            'thumbnail': thumbnail_url
                        })
            
            print(f"Found {len(event_data)} event URLs with thumbnails from list page")
            return event_data
            
        except Exception as e:
            print(f"Error extracting event URLs: {str(e)}")
            return []
    
    def _extract_thumbnail_from_card(self, card_element, base_url: str) -> Optional[str]:
        """Extract thumbnail image from an event card element"""
        try:
            # Look for picture element (most modern approach)
            picture = card_element.find('picture')
            if picture:
                # Try to get img from picture
                img = picture.find('img')
                if img and img.get('src'):
                    src = img.get('src')
                    return urljoin(base_url, src)
                
                # Fallback: get first source's srcset
                source = picture.find('source')
                if source and source.get('srcset'):
                    srcset = source.get('srcset')
                    # Extract first URL from srcset (before space or comma)
                    img_url = srcset.split()[0].split(',')[0]
                    return urljoin(base_url, img_url)
            
            # Fallback: look for img directly in card
            img = card_element.find('img')
            if img and img.get('src'):
                src = img.get('src')
                return urljoin(base_url, src)
            
            # Fallback: look for data-src (lazy loading)
            if img and img.get('data-src'):
                src = img.get('data-src')
                return urljoin(base_url, src)
            
            return None
            
        except Exception as e:
            print(f"Error extracting thumbnail: {str(e)}")
            return None
    
    async def _scrape_events_concurrently(self, event_data: List[Dict[str, str]], max_events: int) -> List[EventDetails]:
        """Scrape multiple events concurrently, stopping once max_events are collected"""
        events: List[EventDetails] = []
        seen_urls = set()  # Track seen URLs to prevent duplicates
        total_events_count = len(event_data)
        index = 0
        max_concurrent = 5  # Limit concurrent requests
        current_time = datetime.now()
        
        print(f"Preparing to scrape up to {max_events} events from {total_events_count} event URLs")
        
        while index < total_events_count and len(events) < max_events:
            remaining_needed = max_events - len(events)
            batch_size = min(max_concurrent, remaining_needed, total_events_count - index)
            batch_data = event_data[index : index + batch_size]
            index += batch_size
            
            # Filter out already seen URLs
            batch_data = [ed for ed in batch_data if ed['url'] not in seen_urls]
            if not batch_data:
                continue
            
            print(f"Scraping batch of {len(batch_data)} events (collected so far: {len(events)})")
            
            semaphore = asyncio.Semaphore(max_concurrent)
            
            async def scrape_with_semaphore(event_dict: Dict[str, str]) -> Optional[EventDetails]:
                async with semaphore:
                    try:
                        return await self._scrape_single_event(event_dict['url'], event_dict.get('thumbnail'))
                    except Exception as e:
                        print(f"Error scraping event {event_dict['url']}: {str(e)}")
                        return None
            
            tasks = [scrape_with_semaphore(ed) for ed in batch_data]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for event_dict, result in zip(batch_data, batch_results):
                # Mark URL as seen
                seen_urls.add(event_dict['url'])
                
                if isinstance(result, Exception):
                    print(f"Exception in event scraping for {event_dict['url']}: {str(result)}")
                    continue
                
                if result is None:
                    print(f"No event data extracted from {event_dict['url']}; skipping")
                    continue
                
                if isinstance(result, EventDetails):
                    # Check if event is upcoming (not in the past)
                    if not self._is_upcoming_event(result, current_time):
                        print(f"Skipping past event '{result.title}' (date: {result.date_time})")
                        continue
                    
                    events.append(result)
                    print(f"Collected event '{result.title}' ({len(events)}/{max_events})")
                
                if len(events) >= max_events:
                    print(f"Reached desired event count ({max_events}); stopping further scraping")
                    break
        
        return events
    
    async def _scrape_single_event(self, event_url: str, list_thumbnail: Optional[str] = None) -> Optional[EventDetails]:
        """Scrape a single event"""
        try:
            # Create crawler config with wait time for JavaScript rendering
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=1,
                page_timeout=30000,
                delay_before_return_html=0.5
            )
            
            # Crawl the event page with fallback
            try:
                result = await self.crawler.arun(url=str(event_url), config=crawler_config)
            except Exception as crawl_error:
                print(f"Crawl error for {event_url}: {str(crawl_error)}")
                # Try again with simpler config
                simple_config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=1,
                    page_timeout=20000,
                    delay_before_return_html=1.0
                )
                result = await self.crawler.arun(url=str(event_url), config=simple_config)
            
            if not result.success:
                print(f"Failed to crawl event URL {event_url}: {result.error_message}")
                return None
            
            # Build structured content (HTML blocks, stats, etc.)
            structured_content = await self._build_content_structure(result)
            
            # Extract data using OpenAI for intelligent extraction
            extracted_data = await self._extract_event_with_openai(result)
            
            # Check if details/content exists - if not, skip this event
            details = extracted_data.get('details', '').strip() if extracted_data.get('details') else ''
            if not details:
                fallback_text = structured_content.get('plain_text') if structured_content else ''
                if fallback_text:
                    details = fallback_text
                else:
                    print(f"No details/content found for event {event_url}; skipping")
                    return None
            
            # Extract date_time as string to preserve exact format
            date_time_str = extracted_data.get('date_time')
            if date_time_str:
                date_time_str = str(date_time_str).strip()
            
            # Get image and video URLs
            # Prefer thumbnail from list page, fallback to extracted image URL, then to primary image from content
            image_url = (list_thumbnail or 
                        extracted_data.get('image_url') or 
                        (structured_content.get('primary_image') if structured_content else None))
            video_url = extracted_data.get('video_url') or (structured_content.get('primary_video') if structured_content else None)
            
            # Ensure speakers is a list
            speakers = extracted_data.get('speakers', [])
            if isinstance(speakers, str):
                speakers = [speakers] if speakers else []
            elif not speakers:
                speakers = []
            
            return EventDetails(
                title=extracted_data.get('title', ''),
                image_url=image_url,
                video_url=video_url,
                details=details,
                details_html=structured_content.get('content_html') if structured_content else None,
                content_blocks=structured_content.get('content_blocks') if structured_content else None,
                content_stats=structured_content.get('content_stats') if structured_content else None,
                links=structured_content.get('links') if structured_content else None,
                subheadings=structured_content.get('subheadings') if structured_content else None,
                date_time=date_time_str,
                location=extracted_data.get('location'),
                event_type=extracted_data.get('event_type'),
                access_type=extracted_data.get('access_type'),
                agenda=extracted_data.get('agenda'),
                speakers=speakers,
                category=extracted_data.get('category'),
                event_url=str(event_url)
            )
            
        except Exception as e:
            print(f"Error scraping single event {event_url}: {str(e)}")
            return None
    
    async def _build_content_structure(self, result) -> Dict[str, Any]:
        """Create sanitized HTML, block metadata, and link stats for the event details.
        Prefers Selenium getComputedStyle inlining when available and returns content; else CSS parse + inline."""
        try:
            html_content = result.html if hasattr(result, 'html') else ''
            base_url = getattr(result, 'url', None)
            root = None
            # Same workflow as JobScraper: fetch styles via Selenium (Chrome loads URL, getComputedStyle)
            if base_url and html_content:
                try:
                    html_with_styles = get_html_with_computed_styles(base_url)
                    if html_with_styles and len(html_with_styles.strip()) > 200:
                        fragment_soup = BeautifulSoup(html_with_styles, 'html.parser')
                        candidate = fragment_soup.body if fragment_soup.body else fragment_soup
                        if candidate and len((candidate.get_text(" ", strip=True) or "")) > 100:
                            root = candidate
                            print("Using Selenium computed styles for content_html")
                except Exception as e:
                    print("Selenium style fetch failed, using CSS fallback:", e)
                    root = None
            if root is None:
                print("Using CSS parse + inline fallback for content_html")
                soup = BeautifulSoup(html_content, 'html.parser')
                container = self._find_primary_content_node(soup)
                if not container:
                    container = soup.find('body') or soup
                fragment_soup = BeautifulSoup(str(container), 'html.parser')
                root = fragment_soup.body if fragment_soup.body else fragment_soup
                self._sanitize_article_fragment(root)
                self._normalize_resource_urls(root, base_url)
                css_rules = await extract_and_parse_css(soup, base_url)
                if css_rules:
                    inline_css_styles(root, css_rules)
            else:
                self._sanitize_article_fragment(root)
                self._normalize_resource_urls(root, base_url)
            plain_text = root.get_text(" ", strip=True)
            blocks, stats, first_image, first_video, headings = self._generate_content_blocks(root)
            links = self._collect_links(root, base_url)
            stats['links'] = len(links)
            # content_html = body HTML with all fetched styles as inline style (Selenium getComputedStyle or CSS parse+inline)
            content_html = self._serialize_fragment(root)
            
            # Convert stats dict to EventContentStats model
            content_stats = EventContentStats(**stats)
            
            return {
                'content_html': content_html,
                'plain_text': plain_text,
                'content_blocks': blocks,
                'content_stats': content_stats,
                'links': links,
                'primary_image': first_image,
                'primary_video': first_video,
                'subheadings': headings
            }
        except Exception as exc:
            print(f"Failed to assemble structured content: {exc}")
            return {
                'content_html': None,
                'plain_text': None,
                'content_blocks': [],
                'content_stats': EventContentStats(
                    paragraphs=0,
                    headings=0,
                    images=0,
                    videos=0,
                    embeds=0,
                    lists=0,
                    quotes=0,
                    links=0,
                    tables=0
                ),
                'links': [],
                'primary_image': None,
                'primary_video': None,
                'subheadings': []
            }
    
    def _find_primary_content_node(self, soup: BeautifulSoup):
        """Find the primary content container (prefer wrappers that include title + body for full data)."""
        selectors = [
            '#conference',
            '.conference_wrap',
            'article',
            '.article-content',
            '.article-body',
            '.event-content',
            '.event-details',
            '.event-description',
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
        """Remove unwanted elements and attributes; ignore footers (class contains 'footer')."""
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
        """Convert relative URLs to absolute"""
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
            try:
                tag[attr_name] = urljoin(base_url, url_value)
            except Exception:
                pass
    
    def _generate_content_blocks(self, root) -> tuple:
        """Generate content blocks from HTML structure"""
        blocks = []
        stats = {
            'paragraphs': 0,
            'headings': 0,
            'images': 0,
            'videos': 0,
            'embeds': 0,
            'lists': 0,
            'quotes': 0,
            'links': 0,
            'tables': 0
        }
        first_image = None
        first_video = None
        headings = []
        
        for element in root.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'img', 'video', 'ul', 'ol', 'blockquote', 'table', 'iframe', 'a']):
            if element.name == 'p':
                text = element.get_text(strip=True)
                if text:
                    blocks.append(EventContentBlock(
                        type='paragraph',
                        html=str(element),
                        text=text
                    ))
                    stats['paragraphs'] += 1
            
            elif element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                text = element.get_text(strip=True)
                if text:
                    level = element.name
                    blocks.append(EventContentBlock(
                        type='heading',
                        html=str(element),
                        text=text,
                        level=level
                    ))
                    stats['headings'] += 1
                    headings.append(text)
            
            elif element.name == 'img':
                src = element.get('src') or element.get('data-src', '')
                alt = element.get('alt', '')
                if src and not src.startswith('data:'):
                    if not first_image:
                        first_image = src
                    blocks.append(EventContentBlock(
                        type='image',
                        html=str(element),
                        src=src,
                        alt=alt,
                        caption=element.get('title') or alt
                    ))
                    stats['images'] += 1
            
            elif element.name == 'video':
                src = element.get('src') or ''
                source = element.find('source')
                if source:
                    src = source.get('src', '')
                if src and not src.startswith('data:'):
                    if not first_video:
                        first_video = src
                    blocks.append(EventContentBlock(
                        type='video',
                        html=str(element),
                        src=src
                    ))
                    stats['videos'] += 1
            
            elif element.name in ['ul', 'ol']:
                items = [li.get_text(strip=True) for li in element.find_all('li')]
                if items:
                    blocks.append(EventContentBlock(
                        type='list',
                        html=str(element),
                        items=items,
                        ordered=(element.name == 'ol')
                    ))
                    stats['lists'] += 1
            
            elif element.name == 'blockquote':
                text = element.get_text(strip=True)
                if text:
                    blocks.append(EventContentBlock(
                        type='quote',
                        html=str(element),
                        text=text
                    ))
                    stats['quotes'] += 1
            
            elif element.name == 'table':
                rows = []
                for tr in element.find_all('tr'):
                    row = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                    if row:
                        rows.append(row)
                if rows:
                    blocks.append(EventContentBlock(
                        type='table',
                        html=str(element),
                        rows=rows
                    ))
                    stats['tables'] += 1
            
            elif element.name == 'iframe':
                src = element.get('src', '')
                if src:
                    blocks.append(EventContentBlock(
                        type='embed',
                        html=str(element),
                        src=src
                    ))
                    stats['embeds'] += 1
        
        return blocks, stats, first_image, first_video, headings
    
    def _collect_links(self, root, base_url: Optional[str]) -> List[EventLink]:
        """Collect all links from the content"""
        links = []
        for a_tag in root.find_all('a', href=True):
            href = a_tag.get('href')
            if href and not href.startswith('#') and not href.startswith('javascript:'):
                try:
                    full_url = urljoin(base_url, href) if base_url else href
                    links.append(EventLink(
                        href=full_url,
                        text=a_tag.get_text(strip=True),
                        title=a_tag.get('title')
                    ))
                except Exception:
                    pass
        return links
    
    def _serialize_fragment(self, root) -> str:
        """Serialize the HTML fragment"""
        return str(root)
    
    async def _extract_event_with_openai(self, result) -> Dict[str, Any]:
        """Extract event data using OpenAI GPT-4o-mini"""
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
                # Remove any remaining header/footer elements by class or id
                for element in body_content.find_all(['div', 'section'], class_=lambda x: x and ('header' in x.lower() or 'footer' in x.lower() or 'nav' in x.lower())):
                    element.decompose()
                for element in body_content.find_all(['div', 'section'], id=lambda x: x and ('header' in x.lower() or 'footer' in x.lower() or 'nav' in x.lower())):
                    element.decompose()
                
                cleaned_text = body_content.get_text()
            else:
                cleaned_text = soup.get_text()
            
            # Remove extra whitespace and limit to avoid token limits
            cleaned_text = ' '.join(cleaned_text.split())
            cleaned_text = cleaned_text[:8000]
            
            print(f"Cleaned text length: {len(cleaned_text)} characters")
            print(f"Cleaned text preview: {cleaned_text[:200]}...")
            
            # Create prompt for OpenAI
            prompt = f"""
You are an event data extraction expert. Extract the following information from this event webpage content:

1. **title**: Event title/name
2. **image_url**: Main event image URL (if found in HTML)
3. **video_url**: Video URL if present
4. **details**: Full event description/details
5. **date_time**: Event date and time. Extract the EXACT date and time as shown on the page. This can be:
   - Single date: "January 23, 2026"
   - Single date with time range: "January 21, 2026, 10:15 AM-12:30 PM"
   - Date range: "January 27, 2026-January 28, 2026"
   - Date range with times: "January 26, 2026, 8:30 AM-January 29, 2026, 4:30 PM"
   Preserve the exact format from the webpage, including all dates, times, and separators (dash, comma, etc.).
6. **location**: Event location/venue
7. **event_type**: Type of event (conference, workshop, webinar, etc.)
8. **access_type**: Access type (Free, Paid, Requires Registration)
9. **agenda**: Event agenda or schedule
10. **speakers**: List of speaker names (as an array)
11. **category**: Event category (Physical, Virtual, or Hybrid)

Return the data as a JSON object with these exact keys. If a field is not found, use an empty string or empty array for speakers.

Webpage content:
{cleaned_text}

HTML snippet for images (first 2000 chars):
{html_content[:2000]}
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
            
            # Ensure speakers is a list
            if isinstance(extracted_data.get('speakers'), str):
                extracted_data['speakers'] = [extracted_data['speakers']] if extracted_data['speakers'] else []
            elif not extracted_data.get('speakers'):
                extracted_data['speakers'] = []
            
            print(f"Successfully extracted event data with OpenAI: {extracted_data.get('title', '')[:50]}...")
            return extracted_data
            
        except Exception as e:
            print(f"Error in OpenAI event extraction: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to basic extraction
            return self._extract_basic_event_info(result)
    
    def _extract_basic_event_info(self, result) -> Dict[str, Any]:
        """Fallback method to extract basic event information"""
        try:
            soup = BeautifulSoup(result.html, 'html.parser')
            
            # Extract title
            title = ""
            title_elem = soup.find('h1') or soup.find('title')
            if title_elem:
                title = title_elem.get_text().strip()
            
            # Extract content
            content = ""
            main_content = soup.find('main') or soup.find('article') or soup.find('div', class_='content')
            if main_content:
                content = main_content.get_text().strip()
            else:
                content = soup.get_text().strip()
            
            return {
                "title": title or "No title found",
                "image_url": None,
                "video_url": None,
                "details": content or "No details found",
                "date_time": None,
                "location": None,
                "event_type": None,
                "access_type": None,
                "agenda": None,
                "speakers": [],
                "category": None
            }
            
        except Exception as e:
            print(f"Error in basic event extraction: {e}")
            return {
                "title": "Extraction Failed",
                "image_url": None,
                "video_url": None,
                "details": "Failed to extract content",
                "date_time": None,
                "location": None,
                "event_type": None,
                "access_type": None,
                "agenda": None,
                "speakers": [],
                "category": None
            }
    
    def _parse_date(self, date_string: str) -> tuple[Optional[datetime], Optional[datetime]]:
        """
        Parse date string to datetime object(s).
        Handles:
        - Date ranges: "January 27, 2026-January 28, 2026"
        - Single date with time range: "January 21, 2026, 10:15 AM-12:30 PM"
        - Single dates: "January 23, 2026"
        
        Returns:
            tuple: (start_date, end_date) where end_date is None for single dates or time ranges on same date
        """
        try:
            date_string = date_string.strip()
            
            # Check for date range patterns (separated by dash, comma-dash, or "to")
            range_separators = [' - ', '-', ' to ', ' through ', ' until ']
            date_start = None
            date_end = None
            
            for separator in range_separators:
                if separator in date_string:
                    parts = date_string.split(separator, 1)
                    if len(parts) == 2:
                        start_str = parts[0].strip()
                        end_str = parts[1].strip()
                        
                        # Parse start date
                        date_start = self._parse_single_date(start_str)
                        
                        if date_start:
                            # Check if end_str looks like just a time (contains AM/PM but no month name)
                            months = ['January', 'February', 'March', 'April', 'May', 'June',
                                     'July', 'August', 'September', 'October', 'November', 'December',
                                     'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                            is_time_range = (
                                ('AM' in end_str.upper() or 'PM' in end_str.upper()) and
                                not any(month in end_str for month in months)
                            )
                            
                            if is_time_range:
                                # This is a time range on the same date
                                # Parse the end time and combine with the start date
                                end_time = self._parse_time(end_str, date_start)
                                if end_time:
                                    date_end = end_time
                                else:
                                    # If time parsing fails, just use start date (event is same day)
                                    date_end = None
                                return (date_start, date_end)
                            else:
                                # This is a date range - parse end date
                                date_end = self._parse_single_date(end_str, default_year=date_start.year)
                                if date_end:
                                    return (date_start, date_end)
            
            # If no range found, parse as single date
            date_start = self._parse_single_date(date_string)
            return (date_start, None)
            
        except Exception as e:
            print(f"Error parsing date '{date_string}': {e}")
            return (None, None)
    
    def _parse_single_date(self, date_string: str, default_year: Optional[int] = None) -> Optional[datetime]:
        """Parse a single date string to datetime object"""
        try:
            date_string = date_string.strip()
            
            # Common date formats to try
            date_formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%B %d, %Y, %I:%M %p",  # "January 21, 2026, 10:15 AM"
                "%B %d, %Y at %I:%M %p",  # "January 21, 2026 at 10:15 AM"
                "%d %B %Y at %I:%M %p",
                "%B %d, %Y",  # "January 21, 2026"
                "%d %B %Y",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%A, %B %d, %Y at %I:%M %p",
                "%B %d",  # Without year
                "%d %B",  # Without year
            ]
            
            # Try parsing with default year if provided and date doesn't have year
            if default_year:
                # Check if date string contains a year
                has_year = bool(re.search(r'\b(19|20)\d{2}\b', date_string))
                if not has_year:
                    # Try adding the year
                    for fmt in ["%B %d", "%d %B"]:
                        try:
                            parsed = datetime.strptime(date_string.strip(), fmt)
                            return parsed.replace(year=default_year)
                        except ValueError:
                            continue
            
            # Try all formats
            for fmt in date_formats:
                try:
                    parsed = datetime.strptime(date_string.strip(), fmt)
                    # If no year was parsed and default_year is provided, use it
                    if default_year and parsed.year == 1900:  # strptime default
                        parsed = parsed.replace(year=default_year)
                    return parsed
                except ValueError:
                    continue
            
            # If no format matches, try to extract date using regex
            date_patterns = [
                (r'\d{4}-\d{2}-\d{2}', "%Y-%m-%d"),
                (r'\d{2}/\d{2}/\d{4}', "%m/%d/%Y"),
                (r'\d{1,2}\s+\w+\s+\d{4}', "%d %B %Y"),
                (r'\w+\s+\d{1,2},?\s+\d{4}', "%B %d, %Y"),
            ]
            
            for pattern, fmt in date_patterns:
                match = re.search(pattern, date_string)
                if match:
                    try:
                        return datetime.strptime(match.group(), fmt)
                    except ValueError:
                        continue
            
            return None
            
        except Exception as e:
            print(f"Error parsing single date '{date_string}': {e}")
            return None
    
    def _parse_time(self, time_string: str, base_date: datetime) -> Optional[datetime]:
        """
        Parse a time string (e.g., "10:15 AM", "12:30 PM") and combine with a base date.
        
        Args:
            time_string: Time string to parse
            base_date: Base datetime to combine the time with
            
        Returns:
            datetime object with the time applied to the base date, or None if parsing fails
        """
        try:
            time_string = time_string.strip()
            
            # Time formats to try
            time_formats = [
                "%I:%M %p",  # "10:15 AM", "12:30 PM"
                "%I %p",     # "10 AM", "12 PM"
                "%H:%M",     # "10:15", "12:30"
                "%H:%M:%S",  # "10:15:30"
            ]
            
            for fmt in time_formats:
                try:
                    # Parse just the time part
                    from datetime import time as dt_time
                    time_obj = datetime.strptime(time_string, fmt).time()
                    # Combine with base date
                    return datetime.combine(base_date.date(), time_obj)
                except ValueError:
                    continue
            
            return None
            
        except Exception as e:
            print(f"Error parsing time '{time_string}': {e}")
            return None
    
    def _is_upcoming_event(self, event: EventDetails, current_time: datetime) -> bool:
        """
        Check if an event is upcoming (not in the past).
        Parses the date_time string to determine if the event is upcoming.
        For date ranges, checks if the end date is today or in the future.
        For single dates, checks if the date is today or in the future.
        """
        if not event.date_time:
            # If no date, assume it's upcoming (to be safe)
            return True
        
        try:
            # Parse the date_time string to get start and end dates
            date_start, date_end = self._parse_date(event.date_time)
            
            if not date_start:
                # If parsing fails, assume it's upcoming (to be safe)
                return True
            
            # For date ranges, check the end date (if available)
            # For single dates or time ranges on same day, check the start date
            date_to_check = date_end if date_end else date_start
            
            # Compare the date/time with current time
            current_datetime = current_time
            
            # Check if event has a specific time (not midnight) or if it's date-only
            event_time = date_to_check.time()
            is_date_only = (event_time.hour == 0 and event_time.minute == 0 and event_time.second == 0)
            
            if is_date_only:
                # Event is date-only - compare dates (event is upcoming if date is today or future)
                event_date = date_to_check.date()
                current_date = current_datetime.date()
                return event_date >= current_date
            else:
                # Event has specific time - compare datetimes
                return date_to_check >= current_datetime
                
        except Exception as e:
            print(f"Error checking if event is upcoming for date_time '{event.date_time}': {e}")
            # If parsing fails, assume it's upcoming (to be safe)
            return True