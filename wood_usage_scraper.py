import asyncio
import json
import logging
import os
import re
from typing import Optional, Dict, List, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from openai import AsyncOpenAI

from models import WoodUsageResponse


class WoodUsageScraper:
    """Analyzes a company website to determine why and how they use wood,
    based on their products and services."""

    # Explicit wood/timber terms
    WOOD_KEYWORDS = [
        "wood", "timber", "lumber", "plywood", "hardwood", "softwood",
        "forestry", "sawmill", "pulp", "paper", "cellulose", "lignin",
        "veneer", "particleboard", "mdf", "chipboard", "fibreboard",
        "woodchip", "wood chip", "biomass", "pellet", "firewood",
        "log", "logging", "wooden", "carpentry", "joinery",
        "furniture", "cabinetry", "flooring", "decking", "cladding",
        "cross-laminated", "clt", "glulam", "oriented strand board", "osb",
        "kraft", "cardboard", "packaging", "pallet",
    ]

    # Services/products that *implicitly* require wood even if "wood" is
    # never mentioned — construction, renovation, and building trades
    IMPLICIT_WOOD_KEYWORDS = [
        # Construction & building
        "house extension", "home extension", "extension", "loft conversion",
        "garage conversion", "barn conversion", "new build", "new home",
        "house building", "home building", "housebuilding",
        "construction", "building contractor", "general contractor",
        # Renovation & remodelling
        "renovation", "remodel", "remodelling", "remodeling", "refurbishment",
        "restoration", "retrofit", "conversion",
        # Specific rooms / areas that need structural wood (framing, joists, etc.)
        "bathroom", "kitchen", "bedroom", "living room", "conservatory",
        "orangery", "porch", "sunroom", "basement",
        # Structural & framing
        "framing", "roof", "roofing", "truss", "rafter", "joist",
        "stud wall", "partition wall", "wall framing",
        "floor joist", "subfloor", "structural frame",
        # Finishing trades that use wood
        "door", "window", "staircase", "stair", "banister", "handrail",
        "skirting", "architrave", "dado rail", "coving", "moulding", "molding",
        "wardrobe", "shelving", "built-in", "bespoke furniture",
        "cabinet", "countertop", "worktop",
        "fencing", "fence", "pergola", "gazebo", "shed", "outbuilding",
        "timber frame", "timber-frame", "log cabin", "log home",
        "decking", "deck", "patio", "landscaping",
        # Trades
        "carpenter", "joiner", "builder", "fit-out", "fitout", "fit out",
        "interior design", "interior fit",
        "shop fitting", "shopfitting", "shop-fitting",
        "office fit", "office refurbishment",
    ]

    RELEVANT_PATH_KEYWORDS = [
        "product", "service", "solution", "about", "what-we-do",
        "offering", "capabilit", "industr", "material", "process",
        "manufactur", "operation", "portfolio", "business", "overview",
        "wood", "timber", "lumber", "forestry", "pulp", "paper",
        # Construction / renovation paths
        "extension", "renovation", "bathroom", "kitchen", "roofing",
        "construction", "building", "conversion", "loft", "garage",
        "bedroom", "conservatory", "door", "window", "staircase",
        "fencing", "decking", "landscaping", "refurbishment",
        "carpentry", "joinery", "fit-out", "fitout",
        "new-build", "residential", "commercial", "project",
    ]

    def __init__(self):
        api_key = os.getenv(
            "OPENAI_API_KEY",
            "",
        )
        self.openai_client = AsyncOpenAI(api_key=api_key)
        self.crawler: Optional[AsyncWebCrawler] = None
        self.max_pages = 20
        self.max_concurrent = 5

    async def __aenter__(self):
        self.crawler = AsyncWebCrawler(verbose=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.crawler:
            await self.crawler.close()

    async def analyze(self, company_url: str) -> WoodUsageResponse:
        if not self.crawler:
            self.crawler = AsyncWebCrawler(verbose=True)

        base_domain = urlparse(str(company_url)).netloc
        logging.info(f"Starting wood-usage analysis for {company_url} (domain={base_domain})")

        homepage_text, internal_links = await self._crawl_page(str(company_url), base_domain)

        relevant_urls = self._pick_relevant_urls(internal_links, str(company_url))
        logging.info(f"Selected {len(relevant_urls)} relevant sub-pages to crawl")

        all_page_texts: List[str] = []
        if homepage_text:
            all_page_texts.append(f"[Homepage]\n{homepage_text}")

        sub_texts = await self._crawl_pages_concurrently(relevant_urls, base_domain)
        all_page_texts.extend(sub_texts)

        combined_text = "\n\n---\n\n".join(all_page_texts)
        logging.info(f"Total scraped text length: {len(combined_text)} chars from {len(all_page_texts)} pages")

        evidence_snippets = self._extract_wood_evidence(combined_text)
        logging.info(f"Found {len(evidence_snippets)} wood-related text snippets")

        analysis = await self._analyze_with_openai(combined_text, evidence_snippets, str(company_url))

        company_name = analysis.get("company_name")
        if not company_name:
            company_name = self._guess_company_name(homepage_text or "", base_domain)

        return WoodUsageResponse(
            company_name=company_name,
            company_url=str(company_url),
            why_uses_wood=analysis.get("why_uses_wood"),
            how_uses_wood=analysis.get("how_uses_wood"),
            wood_related_products=analysis.get("wood_related_products"),
            wood_related_services=analysis.get("wood_related_services"),
            summary=analysis.get("summary"),
            confidence=analysis.get("confidence", "low"),
            raw_evidence=evidence_snippets[:20] if evidence_snippets else None,
        )

    # ------------------------------------------------------------------
    # Crawling helpers
    # ------------------------------------------------------------------

    async def _crawl_page(self, url: str, base_domain: str):
        """Crawl a single page. Returns (plain_text, list_of_internal_links)."""
        try:
            config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=1,
                page_timeout=30000,
                delay_before_return_html=3.0,
            )
            result = await self.crawler.arun(url=url, config=config)
            if not result.success:
                logging.warning(f"Failed to crawl {url}: {result.error_message}")
                return "", []

            soup = BeautifulSoup(result.html, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
                tag.decompose()

            body = soup.find("body")
            text = body.get_text(" ", strip=True) if body else soup.get_text(" ", strip=True)
            text = re.sub(r"\s{2,}", " ", text)

            links: List[str] = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = urljoin(url, href)
                parsed = urlparse(full)
                if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
                    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if clean.rstrip("/") != url.rstrip("/"):
                        links.append(clean)

            return text, list(set(links))

        except Exception as e:
            logging.error(f"Error crawling {url}: {e}")
            return "", []

    def _pick_relevant_urls(self, links: List[str], homepage_url: str) -> List[str]:
        """Score and pick the most relevant sub-pages to crawl."""
        scored: List[tuple] = []
        seen: Set[str] = set()

        for link in links:
            norm = link.rstrip("/").lower()
            if norm in seen:
                continue
            seen.add(norm)

            if link.lower().endswith((".pdf", ".jpg", ".png", ".gif", ".svg", ".zip", ".mp4")):
                continue

            path = urlparse(link).path.lower()
            score = 0
            for kw in self.RELEVANT_PATH_KEYWORDS:
                if kw in path:
                    score += 2
            for kw in self.WOOD_KEYWORDS:
                if kw in path:
                    score += 5
            for kw in self.IMPLICIT_WOOD_KEYWORDS:
                if kw.replace(" ", "-") in path or kw.replace(" ", "") in path:
                    score += 4

            depth = path.strip("/").count("/")
            if depth <= 1:
                score += 1
            if depth > 3:
                score -= 2

            # Include any page that scored, plus shallow pages (likely top-level
            # service/product pages) even if no keyword matched in the path
            if score > 0 or depth <= 1:
                scored.append((max(score, 0), link))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [url for _, url in scored[: self.max_pages]]

    async def _crawl_pages_concurrently(self, urls: List[str], base_domain: str) -> List[str]:
        """Crawl multiple pages concurrently, returning their text."""
        texts: List[str] = []
        sem = asyncio.Semaphore(self.max_concurrent)

        async def _fetch(url: str) -> Optional[str]:
            async with sem:
                page_text, _ = await self._crawl_page(url, base_domain)
                if page_text and len(page_text) > 50:
                    return f"[{url}]\n{page_text}"
                return None

        tasks = [_fetch(u) for u in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, str):
                texts.append(r)
            elif isinstance(r, Exception):
                logging.warning(f"Sub-page crawl error: {r}")
        return texts

    # ------------------------------------------------------------------
    # Evidence extraction
    # ------------------------------------------------------------------

    def _extract_wood_evidence(self, text: str) -> List[str]:
        """Pull sentences that mention explicit wood keywords OR implicit
        construction/building services that inherently require wood."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        evidence: List[str] = []
        seen: Set[str] = set()

        explicit_pattern = re.compile(
            "|".join(re.escape(k) for k in self.WOOD_KEYWORDS), re.IGNORECASE
        )
        implicit_pattern = re.compile(
            "|".join(re.escape(k) for k in self.IMPLICIT_WOOD_KEYWORDS), re.IGNORECASE
        )

        for sent in sentences:
            snippet = sent.strip()[:500]
            if len(snippet) <= 20:
                continue
            norm = snippet.lower()
            if norm in seen:
                continue

            is_explicit = bool(explicit_pattern.search(snippet))
            is_implicit = bool(implicit_pattern.search(snippet))

            if is_explicit or is_implicit:
                tag = "[explicit]" if is_explicit else "[implicit]"
                entry = f"{tag} {snippet}"
                seen.add(norm)
                evidence.append(entry)

        return evidence

    # ------------------------------------------------------------------
    # OpenAI analysis
    # ------------------------------------------------------------------

    async def _analyze_with_openai(
        self, full_text: str, evidence: List[str], company_url: str
    ) -> Dict:
        try:
            evidence_block = "\n".join(f"- {e}" for e in evidence[:30]) if evidence else "(none found)"
            text_snippet = full_text[:12000]

            prompt = f"""You are an expert construction & materials analyst. A company's website has been scraped.
Your task: determine **why** and **how** the company uses wood, based on its products and services.

CRITICAL — You must consider TWO types of wood usage:

1. **EXPLICIT wood usage**: The company directly mentions wood, timber, lumber, plywood, etc.

2. **IMPLICIT wood usage**: The company offers services or products that INHERENTLY REQUIRE WOOD as a building material, even if the word "wood" never appears on their website. Examples:
   - "House Extensions" → requires wood for framing, joists, roof structure, door frames, skirting boards
   - "Bathroom Renovations" → requires wood for floor joists, stud walls, door frames, cabinetry, shelving
   - "Kitchen Fitting" → requires wood for cabinetry, worktops, framing, flooring underlayment
   - "Loft Conversions" → requires wood for roof trusses, floor joists, stud walls, staircase
   - "Roofing" → requires wood for roof trusses, rafters, battens, fascia boards
   - "New Builds" → requires wood for structural framing, floor joists, roof trusses, internal walls, doors, stairs
   - "Fencing / Decking / Landscaping" → requires wood for fence panels, posts, deck boards
   - "Shop Fitting / Office Fit-out" → requires wood for shelving, counters, partitions, trim
   - "Staircase Installation" → wood is the primary material
   - "Door & Window Installation" → wood frames, sills, architraves
   - Any general construction, building, or renovation service → wood is a fundamental structural and finishing material

For implicit usage, explain WHY each service needs wood (what wood components are used).

Company URL: {company_url}

=== EVIDENCE SNIPPETS (tagged [explicit] or [implicit]) ===
{evidence_block}

=== WEBSITE TEXT (truncated) ===
{text_snippet}

Return a JSON object with these exact keys:
- "company_name": string — the company's name
- "why_uses_wood": string — explain WHY the company uses wood. For implicit cases, explain that their services (e.g., house extensions, bathroom renovations) inherently require wood as a structural and finishing material. Be specific about which wood components each service uses.
- "how_uses_wood": string — explain HOW the company uses wood. For construction/renovation companies, explain that wood is used for framing, joists, trusses, stud walls, door frames, skirting, staircases, cabinetry, etc. as part of their building process.
- "wood_related_products": list of strings — specific products that involve wood (both explicit AND implicit, e.g. "House Extensions (requires timber framing, joists, roof trusses)")
- "wood_related_services": list of strings — specific services that involve wood (both explicit AND implicit, e.g. "Bathroom Renovation (requires stud walls, floor joists, door frames, cabinetry)")
- "summary": string — a 2-4 sentence overview of the company's relationship with wood
- "confidence": string — one of "high", "medium", "low", "none"

Rules:
- A construction, building, or renovation company should almost ALWAYS have at least "medium" confidence because wood is fundamental to construction.
- For each product/service in the lists, include a parenthetical note explaining what wood is used for.
- If the website shows NO connection to wood AND no construction/building/renovation services, set confidence to "none".
- Be specific and cite evidence from the text.
- Return ONLY valid JSON, no markdown fences."""

            response = await self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a business analyst expert. Always return valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2000,
            )

            return json.loads(response.choices[0].message.content)

        except Exception as e:
            logging.error(f"OpenAI analysis failed: {e}")
            return {
                "company_name": None,
                "why_uses_wood": None,
                "how_uses_wood": None,
                "wood_related_products": [],
                "wood_related_services": [],
                "summary": f"Analysis failed: {e}",
                "confidence": "none",
            }

    # ------------------------------------------------------------------

    @staticmethod
    def _guess_company_name(text: str, domain: str) -> str:
        parts = domain.replace("www.", "").split(".")
        return parts[0].capitalize() if parts else domain
