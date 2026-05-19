import asyncio
import json
import logging
import os
import re
from typing import List, Dict, Optional, Union
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from openai import AsyncOpenAI

from models import (
    ProductCategoryRef,
    ProductDetails,
    ProductFacetValue,
    ProductScrapingResponse,
)
from product_catalog import CategoryRow, ProductCatalogRepository


class ProductScraper:
    """Scrapes a single product detail page: images, videos, specs & attributes."""

    RELATED_SECTION_MARKERS = re.compile(
        r"related|similar|you may also|recommended|customers also|"
        r"accessories for other|more from|people also|compare|cross[- ]?sell|"
        r"upsell|also[- ]bought|frequently[- ]bought",
        re.IGNORECASE,
    )

    RELATED_BLOCK_SELECTORS = [
        "section.related.products",
        ".related.products",
        ".woocommerce .related",
        "div.related",
        ".upsells",
        ".cross-sells",
        "ul.cross-sells",
        ".products-upsell",
        ".similar-products",
    ]

    NAVIGATION_MARKERS = re.compile(
        r"footer|header|nav|sidebar|breadcrumb|menu|cookie|banner|popup|modal|"
        r"newsletter|subscribe|sign[- ]?up|log[- ]?in",
        re.IGNORECASE,
    )

    # Right-rail / lead-gen blocks to exclude from product description (not product copy).
    LEAD_GEN_HEADING = re.compile(
        r"^\s*find\s+(a\s+)?location\s*$|^\s*dealer\s+locator\s*$|"
        r"^\s*request\s+(a\s+)?quote\s*$|^\s*request\s+quote\s*$|^\s*get\s+(a\s+)?quote\s*$|"
        r"^\s*locate\s+(a\s+)?dealer\s*$",
        re.IGNORECASE,
    )

    LEAD_GEN_BLOB = re.compile(
        r"find\s+(a\s+)?location|dealer\s+locator|request\s+(a\s+)?quote|get\s+(a\s+)?quote|"
        r"enter\s+your\s+location",
        re.IGNORECASE,
    )

    COLUMN_SIDEBAR_HINT = re.compile(
        r"sidebar|secondary|complementary|right-?col|right-?column|right-?rail|"
        r"aside|quote-?widget|dealer-?loc|lead-?form|product-?sidebar|"
        r"dealer|locator|request-?quote|quote-?form|find-?dealer|location-?finder",
        re.IGNORECASE,
    )

    # Explicit chrome / lead-gen widgets that pollute the description on CMS
    # templates (Brandt / Kentico / SharePoint style pages in particular).
    CHROME_NOISE_SELECTORS = [
        # Print / share / tool buttons
        ".printlink", ".print-link", ".print-friendly", ".printer-friendly",
        "a.print", "a.printer-friendly",
        ".social-share", ".share-buttons", ".share-links", ".social-links",
        # Mobile duplicate call-to-action strip (Location / Quote / Promotions…)
        ".cta-buttons",
        # Right-rail column containers (Brandt uses .col-sm-3.right-content)
        ".col-sm-3.right-content",
        ".col-md-3.right-content",
        ".col-lg-3.right-content",
        "aside.right-content",
        ".right-content.sidebar",
        # Dealer finder / location widgets
        ".dealerFinder", ".dealer-finder", ".dealer-locator",
        ".location-finder", ".find-location", ".find-a-location",
        "[id*='DealerFinder']", "[id*='dealerFinder']",
        "[id*='LocationFinder']", "[id*='locationFinder']",
        # Quote / lead forms (Kentico / ASP.NET style zones)
        "[id*='RequestForQuote']", "[id*='RequestQuote']",
        "[id*='QuoteForm']", "[id*='quoteForm']",
        "[id*='zoneRightColumn']", "[id*='ZoneRightColumn']",
    ]

    VIDEO_HOST_PATTERNS = re.compile(
        r"youtube\.com|youtu\.be|vimeo\.com|wistia\.(com|net)|brightcove|"
        r"dailymotion|vidyard|loom\.com",
        re.IGNORECASE,
    )

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY", "")
        )
        self.request_timeout = 90

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def scrape_product(self, product_url: str) -> ProductScrapingResponse:
        """Scrape a single product detail page."""
        try:
            logging.info(f"Scraping product page: {product_url}")

            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=1,
                page_timeout=self.request_timeout * 1000,
            )

            async with AsyncWebCrawler(verbose=True) as crawler:
                result = await crawler.arun(url=product_url, config=crawler_config)

            if not result.success:
                logging.error(f"Failed to crawl {product_url}: {result.error_message}")
                return ProductScrapingResponse(product=None, source_url=product_url)

            html = result.html
            markdown = getattr(result, "markdown", "") or ""
            soup = BeautifulSoup(html, "html.parser")
            base_url = product_url

            self._strip_boilerplate_tags(soup)

            product_section = self._find_product_section(soup)

            image_urls = self._extract_product_images(product_section, soup, base_url)
            video_urls = self._extract_product_videos(product_section, soup, html, base_url)
            html_specs = self._extract_specs_from_html(soup, product_section)
            desc_root = self._description_root(soup, product_section)
            document_urls = self._collect_document_urls(desc_root, base_url)
            if desc_root:
                self._strip_pdf_anchors(desc_root)
            raw_description = self._extract_full_description(soup, product_section)
            doc_url = document_urls[0] if document_urls else self._extract_doc_url(soup, base_url)

            try:
                catalog_repo = ProductCatalogRepository()
            except RuntimeError as e:
                logging.error(f"Product catalog database not configured: {e}")
                return ProductScrapingResponse(product=None, source_url=product_url)

            catalog_data = await asyncio.to_thread(catalog_repo.load_snapshot)
            leaf_lines = await asyncio.to_thread(catalog_repo.leaf_breadcrumb_lines)

            llm_data = await self._extract_classification_llm(
                markdown, html, product_url, html_specs, leaf_lines
            )

            title = (llm_data.get("title") or "").strip() or self._extract_title(soup)
            leaf_id = (llm_data.get("leaf_category_id") or "").strip()

            # Use LLM to extract clean product description (no button text, nav, etc.)
            description = await self._extract_description_llm(
                markdown, html, product_url, raw_description
            )

            # Attributes: only use values that actually come from the source HTML
            # (tables / dl / definition lists). No LLM-synthesized attributes,
            # so nothing is made up that the page does not contain.
            attributes: Dict[str, str] = dict(html_specs) if html_specs else {}

            by_id: Dict[str, CategoryRow] = catalog_data["by_id"]
            if leaf_id and leaf_id not in by_id:
                logging.warning(f"LLM returned unknown leaf_category_id {leaf_id!r}; clearing")
                leaf_id = ""

            cluster_ref: Optional[ProductCategoryRef] = None
            cat_ref: Optional[ProductCategoryRef] = None
            class_ref: Optional[ProductCategoryRef] = None
            sub_ref: Optional[ProductCategoryRef] = None

            # Build the facet-definition list: sub-class-specific facets first
            # (when a leaf was identified), then always append the global
            # `isCommon = TRUE` facets. De-duplicate by facet_id.
            facet_defs: List[Dict] = []
            seen_facet_ids: set[str] = set()

            if leaf_id:
                layers = await asyncio.to_thread(
                    catalog_repo.four_layers_from_leaf, leaf_id
                )
                cluster_ref = self._row_to_ref(layers["super_category"])
                cat_ref = self._row_to_ref(layers["category"])
                class_ref = self._row_to_ref(layers["class_name"])
                sub_ref = self._row_to_ref(layers["sub_class_name"])

                sub_defs = await asyncio.to_thread(
                    catalog_repo.facet_definitions_for_subclass, leaf_id
                )
                for fd in sub_defs:
                    fid = fd.get("facet_id")
                    if fid and fid not in seen_facet_ids:
                        facet_defs.append(fd)
                        seen_facet_ids.add(fid)

            common_defs = await asyncio.to_thread(
                catalog_repo.common_facet_definitions
            )
            for fd in common_defs:
                fid = fd.get("facet_id")
                if fid and fid not in seen_facet_ids:
                    facet_defs.append(fd)
                    seen_facet_ids.add(fid)

            facet_values: Optional[List[ProductFacetValue]] = None
            if facet_defs:
                facet_llm = await self._extract_facets_llm(
                    markdown, html, product_url, html_specs, facet_defs
                )
                raw_fv = facet_llm.get("facet_values") or []
                by_facet = {
                    str(x.get("facet_id", "")).strip(): x.get("value")
                    for x in raw_fv
                    if isinstance(x, dict) and x.get("facet_id")
                }
                merged: List[ProductFacetValue] = []
                for fd in facet_defs:
                    fid = fd["facet_id"]
                    merged.append(
                        ProductFacetValue(
                            facet_id=fid,
                            value=self._coerce_facet_value(
                                by_facet.get(fid), fd.get("value_type")
                            ),
                            value_type=fd.get("value_type"),
                            key=fd.get("key"),
                            label=fd.get("label"),
                            sort_order=fd.get("sort_order"),
                        )
                    )
                facet_values = merged

            product = ProductDetails(
                title=title,
                description=description,
                image_urls=image_urls or None,
                video_urls=video_urls or None,
                doc_url=doc_url,
                document_urls=document_urls or None,
                cluster=cluster_ref,
                category=cat_ref,
                class_=class_ref,
                sub_class=sub_ref,
                facets=facet_values,
                attributes=attributes or None,
                product_url=product_url,
            )

            logging.info(f"Successfully scraped product: {title}")
            return ProductScrapingResponse(product=product, source_url=product_url)

        except Exception as e:
            logging.error(f"Error scraping product {product_url}: {e}", exc_info=True)
            return ProductScrapingResponse(product=None, source_url=product_url)

    # ------------------------------------------------------------------
    # Identify the main product section (exclude related-products, nav, footer)
    # ------------------------------------------------------------------

    def _strip_boilerplate_tags(self, soup: BeautifulSoup) -> None:
        """Remove scripts/styles so visible text and image extraction match page content."""
        for name in ("script", "style", "noscript", "template"):
            for el in list(soup.find_all(name)):
                el.decompose()

    def _find_product_section(self, soup: BeautifulSoup) -> Optional[Tag]:
        """Try to locate the main product content area, excluding related/similar sections."""
        self._remove_unwanted_sections(soup)

        for selector in [
            "[itemtype*='schema.org/Product']",
            "[itemtype*='schema.org/IndividualProduct']",
            "div.product.type-product",
            "div.product-single",
            "div#product-single",
            "#product-single",
            ".product-detail-page",
            ".product-detail",
            "#product-detail", "#product-main", "#product-content",
            ".product-main", ".product-content",
            # Bootstrap-style left-content column (Brandt and similar CMS pages)
            ".col-sm-9.left-content",
            ".col-md-9.left-content",
            ".col-lg-9.left-content",
            ".left-content",
            "main",
            "[role='main']",
            "article",
        ]:
            tag = soup.select_one(selector)
            if tag:
                return tag

        return None

    FOOTER_SELECTORS = [
        "footer",
        "[role='contentinfo']",
        "#footer", "#site-footer", "#page-footer", "#main-footer",
        ".footer", ".site-footer", ".page-footer", ".main-footer",
        "#colophon", ".colophon",
    ]

    def _remove_unwanted_sections(self, soup: BeautifulSoup) -> None:
        """Remove footer, navigation chrome, and related/recommended product sections."""
        for selector in self.FOOTER_SELECTORS:
            for el in soup.select(selector):
                el.decompose()

        for selector in self.RELATED_BLOCK_SELECTORS:
            for el in soup.select(selector):
                el.decompose()

        # Strip explicit chrome widgets (print/share buttons, right-rail
        # CTA columns, dealer finders, quote forms) before any id/class
        # heuristics so their descendants cannot leak into the description.
        for selector in self.CHROME_NOISE_SELECTORS:
            for el in list(soup.select(selector)):
                if el.parent is None:
                    continue  # already removed as a child of an earlier match
                el.decompose()

        for tag in list(soup.find_all(True)):
            if tag.attrs is None:
                continue
            if tag.name in ("body", "html"):
                continue
            tag_id = tag.get("id", "") or ""
            tag_class = " ".join(tag.get("class", []) or [])
            id_class = f"{tag_id} {tag_class}"
            if self.RELATED_SECTION_MARKERS.search(id_class):
                tag.decompose()
            elif self.NAVIGATION_MARKERS.search(id_class):
                tag.decompose()

        for heading in soup.find_all(re.compile(r"^h[2-6]$", re.I)):
            text = heading.get_text(strip=True)
            if self.RELATED_SECTION_MARKERS.search(text):
                parent = heading.find_parent(["section", "div", "aside"])
                if parent:
                    parent.decompose()
                else:
                    for sib in list(heading.next_siblings):
                        if isinstance(sib, Tag):
                            sib.decompose()
                    heading.decompose()

        self._remove_lead_generation_blocks(soup)

    def _remove_lead_generation_blocks(self, soup: BeautifulSoup) -> None:
        """Drop right-column dealer locators, quote forms, etc. (keep main product + tabs)."""
        for aside in list(soup.find_all("aside")):
            preview = aside.get_text(" ", strip=True)[:800]
            if self.LEAD_GEN_BLOB.search(preview):
                aside.decompose()

        for heading in list(soup.find_all(re.compile(r"^h[1-6]$", re.I))):
            if not self.LEAD_GEN_HEADING.match(heading.get_text(strip=True)):
                continue
            node: Optional[Tag] = heading
            remove_el: Optional[Tag] = None
            for _ in range(10):
                parent = node.parent if node else None
                if not parent or not isinstance(parent, Tag):
                    break
                if parent.name in ("body", "html"):
                    break
                if parent.name == "aside":
                    remove_el = parent
                    break
                blob = f"{parent.get('id', '')} {' '.join(parent.get('class') or [])}"
                if self.COLUMN_SIDEBAR_HINT.search(blob):
                    remove_el = parent
                    break
                node = parent
            if remove_el is None:
                remove_el = heading.find_parent("aside")
            if remove_el is not None and remove_el.name not in ("body", "html"):
                remove_el.decompose()
                continue
            parent = heading.find_parent(["section", "div"])
            if parent and parent.name not in ("body", "html"):
                ptxt = parent.get_text(" ", strip=True)[:400]
                if self.LEAD_GEN_BLOB.search(ptxt):
                    parent.decompose()

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------

    SIZE_SUFFIX_RE = re.compile(
        r"-\d{2,4}x\d{2,4}"
        r"(?=-scaled\.|\.\w{3,4}(?:\?|$))"
    )

    SCALED_SUFFIX_RE = re.compile(r"-scaled(?=\.\w{3,4}(?:\?|$))")

    def _narrow_product_image_container(
        self, soup: BeautifulSoup, product_section: Optional[Tag]
    ) -> Tag:
        """
        Prefer the primary product gallery column so thumbnails from
        related/similar products (often outside this block) are skipped.
        """
        selectors = [
            ".woocommerce-product-gallery",
            ".woocommerce-product-gallery__wrapper",
            "div.product div.images",
            "div.images",
            ".product-images",
            "#product-images",
            "[class*='product-gallery']",
            ".product-media",
            ".fotorama--gallery",
            ".gallery--product",
        ]

        def first_with_img(root: Optional[Tag]) -> Optional[Tag]:
            if not root:
                return None
            for sel in selectors:
                el = root.select_one(sel)
                if el and el.find("img"):
                    return el
            return None

        search_roots: list[Tag] = []
        if product_section:
            search_roots.append(product_section)
        search_roots.append(soup)

        for root in search_roots:
            hit = first_with_img(root)
            if hit:
                return hit

        prod = soup.select_one("[itemtype*='schema.org/Product']")
        if prod:
            hit = first_with_img(prod)
            if hit:
                return hit
            for sel in ("div.images", ".images"):
                el = prod.select_one(sel)
                if el and el.find("img"):
                    return el

        return product_section or soup

    def _inside_related_or_grid_block(self, tag: Optional[Tag]) -> bool:
        """True if tag sits under a related / upsell / product-list region we should not scrape."""
        if tag is None:
            return False
        for el in tag.parents:
            if not isinstance(el, Tag):
                continue
            if el.name in ("body", "html"):
                break
            eid = el.get("id") or ""
            ecls = " ".join(el.get("class") or [])
            blob = f"{eid} {ecls}"
            if self.RELATED_SECTION_MARKERS.search(blob):
                return True
        return False

    def _image_search_roots(
        self, soup: BeautifulSoup, product_section: Optional[Tag]
    ) -> List[Tag]:
        """Several DOM roots, narrow→wide, deduplicated — fixes empty galleries / odd templates."""
        roots: list[Tag] = []
        seen: set[int] = set()

        def add(root: Optional[Tag]) -> None:
            if root is None:
                return
            i = id(root)
            if i in seen:
                return
            seen.add(i)
            roots.append(root)

        add(self._narrow_product_image_container(soup, product_section))
        add(product_section)
        add(soup.select_one("[itemtype*='schema.org/Product']"))
        add(soup.select_one("div.product.type-product"))
        add(soup.select_one("div.product"))
        add(soup.select_one(".single-product"))
        add(soup.find("main"))
        add(soup.body)
        if not roots:
            root_el = soup.find()  # first top-level element, if any
            add(root_el)
        return roots

    def _collect_images_from_root(self, scope: Tag, base_url: str) -> List[str]:
        raw_urls: list[str] = []

        for img in scope.find_all("img"):
            if self._inside_related_or_grid_block(img):
                continue
            best = self._best_img_src(img)
            if best:
                full = urljoin(base_url, best)
                if not self._should_skip_image(full, img):
                    raw_urls.append(full)

        for source in scope.find_all("source"):
            if self._inside_related_or_grid_block(source):
                continue
            srcset = source.get("srcset") or source.get("data-srcset", "")
            if srcset:
                best = self._pick_highest_srcset(srcset)
                if best:
                    full = urljoin(base_url, best)
                    if not self._should_skip_image(full):
                        raw_urls.append(full)

        for a_tag in scope.find_all("a", href=True):
            if self._inside_related_or_grid_block(a_tag):
                continue
            href = a_tag["href"]
            if re.search(r"\.(jpe?g|png|webp|gif|avif|tiff?|bmp)(\?|$)", href, re.I):
                full = urljoin(base_url, href)
                if not self._should_skip_image(full):
                    raw_urls.append(full)

        for bg_tag in scope.find_all(True, style=True):
            if self._inside_related_or_grid_block(bg_tag):
                continue
            sty = bg_tag.get("style") or ""
            match = re.search(r"url\(['\"]?(.*?)['\"]?\)", sty)
            if match:
                full = urljoin(base_url, match.group(1))
                if not self._should_skip_image(full):
                    raw_urls.append(full)

        return raw_urls

    def _extract_product_images(
        self, product_section: Optional[Tag], soup: BeautifulSoup, base_url: str
    ) -> List[str]:
        merged: list[str] = []
        seen_url: set[str] = set()
        for root in self._image_search_roots(soup, product_section):
            for u in self._collect_images_from_root(root, base_url):
                if u not in seen_url:
                    seen_url.add(u)
                    merged.append(u)
        return self._deduplicate_images(merged)

    def _best_img_src(self, img: Tag) -> Optional[str]:
        """Pick the highest quality src from an <img> tag."""
        for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
            srcset = img.get(attr, "")
            if srcset:
                best = self._pick_highest_srcset(srcset)
                if best:
                    return best

        for attr in [
            "data-src", "data-lazy-src", "data-original", "data-full-url",
            "data-large_image", "data-large-image", "src",
        ]:
            val = img.get(attr)
            if val and not val.startswith("data:"):
                return val
        return None

    def _pick_highest_srcset(self, srcset: str) -> Optional[str]:
        """Parse srcset and return the URL with the largest width descriptor."""
        candidates: list[tuple[int, str]] = []
        for part in srcset.split(","):
            tokens = part.strip().split()
            if not tokens:
                continue
            url = tokens[0]
            width = 0
            if len(tokens) > 1:
                desc = tokens[1].lower()
                m = re.match(r"(\d+)w", desc)
                if m:
                    width = int(m.group(1))
                else:
                    m = re.match(r"(\d+(?:\.\d+)?)x", desc)
                    if m:
                        width = int(float(m.group(1)) * 1000)
            candidates.append((width, url))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][1]

    def _should_skip_image(self, url: str, img_tag: Optional[Tag] = None) -> bool:
        url_lower = url.lower()

        if url_lower.endswith(".svg") or ".svg?" in url_lower or "data:image/svg" in url_lower:
            return True

        decorative_patterns = [
            "logo", "icon", "sprite", "pixel", "tracking", "beacon",
            "spacer", "blank", "transparent", "placeholder", "avatar",
            "flag", "badge", "social", "facebook", "twitter", "linkedin",
            "instagram", "pinterest", "youtube", "google", "payment",
            "visa", "mastercard", "paypal", "amex",
        ]
        if any(p in url_lower for p in decorative_patterns):
            return True

        if img_tag:
            alt = (img_tag.get("alt") or "").lower()
            cls = " ".join(img_tag.get("class") or []).lower()
            if any(p in alt or p in cls for p in ["logo", "icon", "social"]):
                return True
            width = img_tag.get("width", "")
            height = img_tag.get("height", "")
            try:
                w = int(width) if width else 0
                h = int(height) if height else 0
                if w and h and w < 24 and h < 24:
                    return True
            except (ValueError, TypeError):
                pass

        return False

    def _image_base_key(self, url: str) -> str:
        """
        Normalize a URL to a base key so that variants of the same image
        (e.g. -300x200.jpg, -150x150.jpg, -scaled.jpg, original.jpg)
        all collapse to the same key.
        """
        parsed = urlparse(url)
        path = parsed.path
        path = self.SIZE_SUFFIX_RE.sub("", path)
        path = self.SCALED_SUFFIX_RE.sub("", path)
        return f"{parsed.netloc}{path}".lower()

    def _deduplicate_images(self, urls: list[str]) -> list[str]:
        """
        Group image URLs by their base key and keep only the highest-quality
        version from each group (longest URL path, which typically has no
        size suffix = original).
        """
        groups: Dict[str, list[str]] = {}
        for url in urls:
            key = self._image_base_key(url)
            groups.setdefault(key, []).append(url)

        result: list[str] = []
        seen_keys: set[str] = set()
        for url in urls:
            key = self._image_base_key(url)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            variants = groups[key]
            best = self._pick_best_variant(variants)
            result.append(best)

        return result

    def _pick_best_variant(self, variants: list[str]) -> str:
        """
        Given multiple URLs for the same image, pick the highest quality:
        prefer the one without size suffixes; among equals, prefer the longest path.
        """
        def score(url: str) -> tuple:
            parsed = urlparse(url)
            path = parsed.path
            has_size = bool(self.SIZE_SUFFIX_RE.search(path))
            has_scaled = bool(self.SCALED_SUFFIX_RE.search(path))
            return (not has_size, not has_scaled, len(path))

        return max(variants, key=score)

    # ------------------------------------------------------------------
    # Video extraction
    # ------------------------------------------------------------------

    def _extract_product_videos(
        self, product_section: Optional[Tag], soup: BeautifulSoup,
        html: str, base_url: str,
    ) -> List[str]:
        scope = product_section or soup
        seen: set[str] = set()
        videos: list[str] = []

        for video_tag in scope.find_all("video"):
            src = video_tag.get("src")
            if src:
                full = urljoin(base_url, src)
                if full not in seen:
                    seen.add(full)
                    videos.append(full)
            for source in video_tag.find_all("source"):
                src = source.get("src")
                if src:
                    full = urljoin(base_url, src)
                    if full not in seen:
                        seen.add(full)
                        videos.append(full)

        for iframe in scope.find_all("iframe", src=True):
            src = iframe["src"]
            if self.VIDEO_HOST_PATTERNS.search(src):
                full = urljoin(base_url, src)
                if full not in seen:
                    seen.add(full)
                    videos.append(full)

        for a_tag in scope.find_all("a", href=True):
            href = a_tag["href"]
            if re.search(r"\.(mp4|webm|ogg|mov|avi)(\?|$)", href, re.I):
                full = urljoin(base_url, href)
                if full not in seen:
                    seen.add(full)
                    videos.append(full)
            elif self.VIDEO_HOST_PATTERNS.search(href):
                full = urljoin(base_url, href)
                if full not in seen:
                    seen.add(full)
                    videos.append(full)

        for embed in scope.find_all(["embed", "object"], True):
            src = embed.get("src") or embed.get("data")
            if src and self.VIDEO_HOST_PATTERNS.search(src):
                full = urljoin(base_url, src)
                if full not in seen:
                    seen.add(full)
                    videos.append(full)

        video_url_pattern = re.compile(
            r'(?:https?://)?(?:www\.)?'
            r'(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)'
            r'[A-Za-z0-9_-]{11}',
            re.IGNORECASE,
        )
        for match in video_url_pattern.finditer(html):
            url = match.group(0)
            if not url.startswith("http"):
                url = "https://" + url
            if url not in seen:
                seen.add(url)
                videos.append(url)

        return videos

    # ------------------------------------------------------------------
    # Documentation / PDF extraction
    # ------------------------------------------------------------------

    _PDF_HREF_RE = re.compile(r"\.pdf(?:[#?]|$)", re.I)
    # CMS brochure handlers (e.g. Brandt: .../ESeries-pdf.aspx, /ContentItems/Brochures/...).
    _BROCHURE_PATH_RE = re.compile(
        r"/brochures?(?:/|$)|contentitems[/]brochures?|/product[-_]?documents?/|"
        r"/downloads?(?:/|$)|/media/brochures?/",
        re.I,
    )
    _PDF_HANDLER_EXT_RE = re.compile(
        r"(?:^|[-/._])pdf\.(?:aspx|ashx|php|html?)(?:$|[?#])"
        r"|(?:spec(?:ification)?s?[-._])?pdf\.(?:aspx|ashx)(?:$|[?#])",
        re.I,
    )

    @staticmethod
    def _href_is_pdf(href: str) -> bool:
        """True when the URL clearly points at a PDF file or PDF stream."""
        if not href or href.startswith("#"):
            return False
        h = href.strip()
        if h.lower().startswith("javascript:"):
            return False
        if ProductScraper._PDF_HREF_RE.search(h):
            return True
        low = h.lower()
        if "format=pdf" in low or "/pdf/" in low or "type=pdf" in low:
            return True
        return False

    def _href_is_product_document(self, href: str) -> bool:
        """
        Product documentation URLs: direct PDFs plus common brochure/download handlers
        (.aspx etc.) used when the visible link text looks like "ESeries-pdf".
        """
        if not href or href.startswith("#"):
            return False
        h = href.strip()
        if h.lower().startswith("javascript:"):
            return False
        if self._href_is_pdf(h):
            return True
        low = h.lower()
        if self._BROCHURE_PATH_RE.search(low) and re.search(
            r"\.(?:aspx|ashx|php|do)(?:$|[?#])", low
        ):
            return True
        if self._PDF_HANDLER_EXT_RE.search(low):
            return True
        return False

    def _anchor_is_document_link(self, a: Tag) -> bool:
        """Same as href rules plus <a download=\"...pdf\">."""
        href = (a.get("href") or "").strip()
        if self._href_is_product_document(href):
            return True
        dl = (a.get("download") or "").strip().lower()
        if dl.endswith(".pdf") or dl == "pdf":
            return True
        return False

    def _collect_document_urls(
        self, scope: Optional[Tag], base_url: str
    ) -> List[str]:
        """Absolute URLs for product documentation PDFs within the description scope."""
        if not scope:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for a in scope.find_all("a", href=True):
            if not self._anchor_is_document_link(a):
                continue
            href = a.get("href") or ""
            full = urljoin(base_url, href.split("#")[0])
            if full not in seen:
                seen.add(full)
                out.append(full)
        return out

    def _strip_pdf_anchors(self, scope: Tag) -> None:
        """Remove brochure/document links so anchor text does not appear in description."""
        for a in list(scope.find_all("a", href=True)):
            if self._anchor_is_document_link(a):
                a.decompose()

    def _extract_doc_url(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        for link in soup.find_all("a", href=True):
            if self._anchor_is_document_link(link):
                href = link.get("href") or ""
                return urljoin(base_url, href.split("#")[0])
        return None

    # ------------------------------------------------------------------
    # HTML-based spec extraction (tables, dl/dt/dd) — strictly from the
    # source page, no AI, no whole-page fallback, no marketing bullets.
    # ------------------------------------------------------------------

    # Headings that unambiguously label a block of product specifications.
    # "Features", "Details", "Parameters" and "Characteristics" are on purpose
    # NOT included - those sections typically hold marketing bullets, not
    # key/value attribute pairs, and were the main source of fake attributes.
    SPEC_HEADING_RE = re.compile(
        r"^\s*(?:"
        r"technical\s+specifications?|"
        r"technical\s+data|"
        r"technical\s+specs|"
        r"technical\s+details|"
        r"specifications?|"
        r"specs|"
        r"spec\s+sheet|"
        r"product\s+specifications?|"
        r"product\s+specs|"
        r"dimensions?(?:\s+(?:and|&)\s+weights?)?|"
        r"weights?(?:\s+(?:and|&)\s+dimensions?)?"
        r")\s*:?\s*$",
        re.I,
    )

    # Container ids/classes that unambiguously say "this is a specs block".
    # Uses word-boundary matching so "inspection", "perspective", "retrospective",
    # "biotechnical" etc. do NOT match (unlike a naive [id*='spec'] selector).
    SPEC_SECTION_CLASS_ID_RE = re.compile(
        r"(?:^|[-_ ])(?:"
        r"specs?|specifications?|"
        r"technical[-_ ]?(?:specs?|data|specifications?|details)|"
        r"spec[-_ ]?sheet|"
        r"dimensions?|"
        r"product[-_ ]?specs?|product[-_ ]?specifications?"
        r")(?:[-_ ]|$)",
        re.I,
    )

    # Non-attribute labels that sometimes slip into table headers.
    _NON_ATTRIBUTE_KEYS = {
        "share", "read more", "learn more", "see more",
        "description", "overview", "continue reading",
        "click here", "view details", "add to cart", "buy now",
    }

    def _looks_like_attribute_pair(self, key: str, val: str) -> bool:
        """Reject sentence-like or marketing fragments; keep real spec pairs."""
        k = (key or "").strip()
        v = (val or "").strip()
        if not k or not v:
            return False
        if len(k) > 60 or len(v) > 400:
            return False
        if k.endswith((".", "?", "!")):
            return False
        if len(k.split()) > 7:
            return False
        if k.lower() in self._NON_ATTRIBUTE_KEYS:
            return False
        return True

    def _find_spec_sections(
        self, root: Tag, product_section: Optional[Tag]
    ) -> List[Tag]:
        """Elements that are explicitly labelled as specification containers."""
        sections: List[Tag] = []
        seen: set[int] = set()

        def add(el: Optional[Tag]) -> None:
            if el is None or id(el) in seen:
                return
            seen.add(id(el))
            sections.append(el)

        for heading in root.find_all(re.compile(r"^h[1-6]$", re.I)):
            if not self.SPEC_HEADING_RE.match(heading.get_text(strip=True)):
                continue
            container = heading.find_parent(["section", "article", "div"])
            if container is not None and container.name not in ("body", "html"):
                add(container)

        for el in root.find_all(True):
            tag_id = el.get("id") or ""
            tag_class = " ".join(el.get("class") or [])
            if not tag_id and not tag_class:
                continue
            if self.SPEC_SECTION_CLASS_ID_RE.search(tag_id) or \
               self.SPEC_SECTION_CLASS_ID_RE.search(tag_class):
                add(el)

        # If we did spot labelled spec sections elsewhere but a product section
        # exists, restrict to those intersecting the product section so random
        # matches elsewhere on the page cannot sneak attributes in.
        if product_section is not None and sections:
            filtered = [
                s for s in sections
                if s is product_section or s in product_section.descendants
                or product_section in s.descendants
            ]
            if filtered:
                return filtered
        return sections

    def _extract_specs_from_html(
        self,
        soup: BeautifulSoup,
        product_section: Optional[Tag] = None,
    ) -> Dict[str, str]:
        """
        Extract attribute/value pairs ONLY from explicitly-labelled spec
        containers (tables + definition lists). Anything not inside a clear
        "Specifications" / "Technical Data" block is ignored - we return an
        empty dict rather than guessing at marketing copy.
        """
        root: Tag = product_section if product_section is not None else soup

        sections = self._find_spec_sections(root, product_section)
        if not sections:
            return {}

        specs: Dict[str, str] = {}

        for section in sections:
            for table in section.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all(["th", "td"])
                    if len(cells) == 2:
                        key = cells[0].get_text(" ", strip=True)
                        val = cells[1].get_text(" ", strip=True)
                        if self._looks_like_attribute_pair(key, val):
                            specs.setdefault(key, val)
                    elif len(cells) > 2:
                        header = cells[0].get_text(" ", strip=True)
                        values = [
                            c.get_text(" ", strip=True) for c in cells[1:]
                            if c.get_text(strip=True)
                        ]
                        if values and self._looks_like_attribute_pair(header, values[0]):
                            specs.setdefault(header, " / ".join(values))

            for dl in section.find_all("dl"):
                dts = dl.find_all("dt")
                dds = dl.find_all("dd")
                for dt, dd in zip(dts, dds):
                    key = dt.get_text(" ", strip=True)
                    val = dd.get_text(" ", strip=True)
                    if self._looks_like_attribute_pair(key, val):
                        specs.setdefault(key, val)

            # Allow bullets only inside a clearly-labelled spec section and
            # only when the separator is ":" - dashes/equals in prose are too
            # ambiguous. The label also has to look like an attribute label.
            for li in section.find_all("li"):
                text = li.get_text(" ", strip=True)
                if ":" not in text:
                    continue
                key, _, val = text.partition(":")
                if self._looks_like_attribute_pair(key, val):
                    specs.setdefault(key.strip(), val.strip())

        return specs

    def _plain_description_text(self, element: Tag) -> str:
        """Plain text for descriptions (no HTML); newlines between block content."""
        return element.get_text(separator="\n", strip=True)

    # ------------------------------------------------------------------
    # Full description — plain text from product DOM only (no LLM)
    # ------------------------------------------------------------------

    def _description_root(
        self, soup: BeautifulSoup, product_section: Optional[Tag]
    ) -> Optional[Tag]:
        if product_section:
            return product_section
        for sel in [
            "[itemtype*='schema.org/Product']",
            "div.product.type-product",
            "div.product",
            ".single-product",
            "main",
            "[role='main']",
        ]:
            el = soup.select_one(sel)
            if el:
                return el
        return soup.body

    def _extract_full_description(
        self, soup: BeautifulSoup, product_section: Optional[Tag]
    ) -> str:
        """All visible text under the product content root (includes tabs, copy, specs)."""
        root = self._description_root(soup, product_section)
        if not root:
            return ""
        text = self._plain_description_text(root)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    # ------------------------------------------------------------------
    # Title fallback
    # ------------------------------------------------------------------

    def _extract_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", ".product-title", ".product-name", "title"]:
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        return "Unknown Product"

    def _row_to_ref(self, row: Optional[CategoryRow]) -> Optional[ProductCategoryRef]:
        if row is None:
            return None
        return ProductCategoryRef(id=row.id, name=row.name, slug=row.slug)

    @staticmethod
    def _coerce_facet_value(
        v: object, value_type: Optional[str]
    ) -> Optional[Union[str, int, float]]:
        """
        Normalize LLM / raw facet output using ProductFacetDefinition.valueType:
        ``text`` / ``enum`` → str, ``number`` → int or float when parseable.
        """
        vt_raw = (value_type or "").strip().lower()
        if v is None:
            return None
        if vt_raw == "number":
            return ProductScraper._parse_facet_number(v)
        # text, enum, or unknown: keep as plain string
        if isinstance(v, bool):
            return None
        if isinstance(v, float) and not (v != v):  # not NaN
            return str(v).strip() or None
        if isinstance(v, int):
            return str(v)
        s = str(v).strip()
        return s if s else None

    @staticmethod
    def _parse_facet_number(v: object) -> Optional[Union[int, float]]:
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            if v != v:  # NaN
                return None
            return int(v) if v == int(v) else v
        s = str(v).strip()
        if not s:
            return None
        condensed = s.replace(",", "")
        m = re.search(
            r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?",
            condensed,
        )
        if not m:
            return None
        try:
            f = float(m.group(0))
            return int(f) if f == int(f) else f
        except ValueError:
            return None

    def _page_content_snippet(self, markdown: str, html: str) -> str:
        content = markdown[:20000] if markdown else ""
        if not content:
            soup = BeautifulSoup(html, "html.parser")
            content = soup.get_text(separator="\n", strip=True)[:20000]
        return content

    # ------------------------------------------------------------------
    # LLM extraction — product description
    # ------------------------------------------------------------------

    async def _extract_description_llm(
        self,
        markdown: str,
        html: str,
        url: str,
        fallback_description: str,
    ) -> str:
        """Use the LLM to extract only the real product description from page content."""
        try:
            content = self._page_content_snippet(markdown, html)
            if not content.strip():
                return fallback_description

            prompt = f"""You are extracting the product description from a product detail page.

URL: {url}

Page content (may include noise like buttons, menus, form labels, CTAs):
\"\"\"
{content}
\"\"\"

Extract ONLY the actual product description — the marketing copy, overview, and feature text that describes what this product is, what it does, its key features, benefits, and specifications prose.

Do NOT include:
- Button text (e.g. "Add to Cart", "Request Quote", "Download PDF", "Find a Dealer")
- Navigation or menu items
- Form labels or input placeholders
- Breadcrumb text
- Footer text
- Cookie consent text
- Social media share labels
- "Related products" or "You may also like" sections
- Price or availability text
- Login/signup prompts
- Tab labels (like "Description", "Specifications", "Reviews") — but DO include the content inside those tabs

Return ONLY the clean product description text as plain text (no JSON, no markdown formatting, no quotes around it). Preserve paragraph breaks with blank lines. If no product description can be found, return an empty string."""

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=4000,
                ),
                timeout=45,
            )

            result = response.choices[0].message.content.strip()
            # If LLM returns nothing useful, fall back
            if not result or len(result) < 10:
                return fallback_description
            return result

        except asyncio.TimeoutError:
            logging.error(f"Description LLM timeout for {url}")
            return fallback_description
        except Exception as e:
            logging.error(f"Description LLM error for {url}: {e}")
            return fallback_description

    # ------------------------------------------------------------------
    # LLM extraction — classification + generic attributes, then facets
    # ------------------------------------------------------------------

    async def _extract_classification_llm(
        self,
        markdown: str,
        html: str,
        url: str,
        html_specs: Dict[str, str],
        leaf_lines: List[str],
    ) -> Dict:
        try:
            content = self._page_content_snippet(markdown, html)
            specs_hint = ""
            if html_specs:
                specs_hint = "\n\nSpecs already extracted from HTML tables:\n"
                for k, v in list(html_specs.items())[:60]:
                    specs_hint += f"  {k}: {v}\n"

            catalog_blob = "\n".join(leaf_lines)
            if len(catalog_blob) > 28000:
                catalog_blob = catalog_blob[:28000] + "\n... (truncated)"

            prompt = f"""You are analysing a product detail page.

URL: {url}
{specs_hint}

Allowed product categories (pick exactly ONE leaf by copying its id).
Each line format: LEAF_CATEGORY_ID | Cluster > Category > Class > Sub-class (breadcrumb names).
The tree has up to four levels: cluster (root), category, class, sub_class.
Choose the single leaf row that best matches THIS product.

CATEGORY LEAVES:
{catalog_blob}

Page content (truncated):
\"\"\"
{content}
\"\"\"

Return a JSON object with:
- "title": product name (string)
- "leaf_category_id": the LEAF_CATEGORY_ID string from exactly one line above (before the first " | ")

Do not include a description field; description is taken only from the page HTML.
Do not include attributes; attributes are extracted only from HTML specification tables.

IMPORTANT:
- leaf_category_id must match a line in CATEGORY LEAVES exactly.
- Ignore related / recommended products.
- Return ONLY valid JSON, no extra text.
"""

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=6000,
                ),
                timeout=45,
            )

            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    return json.loads(match.group())
                logging.error(f"LLM returned unparseable response for {url}: {raw[:300]}")
                return {}

        except asyncio.TimeoutError:
            logging.error(f"LLM timeout for {url}")
            return {}
        except Exception as e:
            logging.error(f"LLM extraction error for {url}: {e}")
            return {}

    async def _extract_facets_llm(
        self,
        markdown: str,
        html: str,
        url: str,
        html_specs: Dict[str, str],
        facet_defs: List[Dict],
    ) -> Dict:
        try:
            content = self._page_content_snippet(markdown, html)
            specs_hint = ""
            if html_specs:
                specs_hint = "\n\nSpecs already extracted from HTML tables:\n"
                for k, v in list(html_specs.items())[:60]:
                    specs_hint += f"  {k}: {v}\n"

            compact = []
            for fd in facet_defs:
                entry = {
                    "facet_id": fd["facet_id"],
                    "key": fd["key"],
                    "label": fd["label"],
                    "value_type": fd["value_type"],
                    "unit": fd["unit"],
                    "description": fd["description"],
                    "options": fd["options"],
                }
                if fd.get("is_common"):
                    entry["is_global"] = True
                compact.append(entry)
            facets_json = json.dumps(compact, ensure_ascii=False, default=str)
            if len(facets_json) > 14000:
                facets_json = facets_json[:14000] + "..."

            prompt = f"""You are extracting structured facet values for ONE product from its page.

URL: {url}
{specs_hint}

Facet definitions (extract a value for each):
{facets_json}

Page content (truncated):
\"\"\"
{content}
\"\"\"

Return JSON with a single key "facet_values": an array of objects, one per facet_id listed above:
{{ "facet_id": "<exact id from definitions>", "value": <see value_type rules below> }}

Rules:
- Use facet_id values exactly as given.
- value_type determines how to format the value:
  - "number": value MUST be a JSON number (e.g. 210, 2.28) or null. Strip units.
  - "text": value MUST be a JSON string or null.
  - "enum": value MUST be a JSON string picked from the "options" array in the definition, or null if truly none apply.
- GLOBAL FACETS (marked "is_global": true) — you must try hard to determine a value:
  - "Country of Manufacture": determine the country where this product is manufactured. Use "Made in" labels, manufacturer location, brand headquarters country, or any geographic clue from the page. Return a country name string (e.g. "United States", "Germany", "Japan", "Canada"). Only return null if there is absolutely no way to infer the country.
  - "OEM / Aftermarket": determine from the product context. If the product is sold by the original manufacturer or is an original part, pick the OEM option. If it is a third-party or aftermarket replacement, pick accordingly. Look at the "options" array and select the best match.
  - "Condition": determine from the product page. If the product page shows it as a new product (no mention of refurbished/remanufactured/used), pick the "New" option. Look at the "options" array and select the best match.
  - "Service Region": determine from shipping info, dealer locations, or manufacturer service area mentioned on the page. Look at the "options" array and select the best match. Return null only if truly undeterminable.
  - For any other global facet: pick the best matching value from the "options" array. Use product context, brand info, and any page clues to determine the answer. Return null only as a last resort.
- SUB-CLASS FACETS (no "is_global" flag):
  - If "options" array is provided, pick the closest allowed value or null.
  - If no options, extract the value from the page content.
- Only values for THIS product; ignore related items.
- Return ONLY valid JSON.
"""

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=4000,
                ),
                timeout=45,
            )

            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    return json.loads(match.group())
                logging.error(f"Facet LLM unparseable for {url}: {raw[:300]}")
                return {}

        except asyncio.TimeoutError:
            logging.error(f"Facet LLM timeout for {url}")
            return {}
        except Exception as e:
            logging.error(f"Facet LLM error for {url}: {e}")
            return {}
