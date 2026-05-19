#!/usr/bin/env python3
"""
Simple test script to verify the news scraper works
"""

import asyncio
import sys
from news_scraper import NewsScraper


async def test_news_scraper():
    """Test the news scraper with a simple URL"""
    test_url = "https://www.agnicoeagle.com/English/news-and-media/news-releases/news-details/2025/AGNICO-EAGLE-ANNOUNCES-DISPOSITION-OF-ITS-INTEREST-IN-ORLA-MINING-LTD-/default.aspx"
    
    print(f"Testing news scraper with URL: {test_url}")
    
    try:
        async with NewsScraper() as scraper:
            result = await scraper.scrape_news(test_url)
            
            print("✅ Scraping successful!")
            print(f"Title: {result.title}")
            print(f"Content length: {len(result.content)} characters")
            print(f"Author: {result.author}")
            print(f"Date: {result.date_time}")
            print(f"Image URL: {result.image_url}")
            
            return True
            
    except Exception as e:
        print(f"❌ Scraping failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_news_scraper())
    sys.exit(0 if success else 1)
