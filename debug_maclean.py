import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from bs4 import BeautifulSoup
from product_scraper import ProductScraper


async def main():
    url = "https://macleanengineering.com/product/maclean-grader-gr5/"
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, word_count_threshold=1, page_timeout=90000)
    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(url=url, config=config)
    print("crawl success", result.success, getattr(result, "error_message", None))
    html = result.html or ""
    print("html len", len(html))
    soup = BeautifulSoup(html, "html.parser")
    for sel in [
        "[itemtype*='schema.org/Product']",
        "div.product.type-product",
        "div.product",
        "main",
        "article",
    ]:
        el = soup.select_one(sel)
        print(
            sel,
            "->",
            el.name if el else None,
            (el.get("class") if el else None),
        )
    imgs = soup.find_all("img")
    print("total img tags", len(imgs))
    for im in imgs[:12]:
        src = im.get("src") or im.get("data-src") or ""
        print(" ", src[:90] if src else "(no src)", "classes", im.get("class"))

    sc = ProductScraper()
    sc._strip_boilerplate_tags(soup)
    sc._remove_unwanted_sections(soup)
    ps = sc._find_product_section(soup)
    print("product_section", ps.name if ps else None, ps.get("class") if ps else None)
    if ps:
        print("product_section text len", len(ps.get_text(separator="\n", strip=True)))
        print("sample:", ps.get_text(separator="\n", strip=True)[:400])
    desc = sc._extract_full_description(soup, ps)
    print("description len", len(desc))
    print("desc sample:", desc[:500] if desc else "(empty)")
    imgs2 = sc._extract_product_images(ps, soup, url)
    print("image_urls count", len(imgs2))
    for u in imgs2[:5]:
        print(" ", u[:100])


if __name__ == "__main__":
    asyncio.run(main())
