#!/usr/bin/env python3
"""
Daily cron job for news scraping from NewsFeedConfig
Runs daily to scrape news from configured feeds and save to News table
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
import uuid

from database import SessionLocal, NewsFeedConfig, News, engine
from news_scraper import NewsScraper
from models import NewsDetails


class NewsCronJob:
    """Cron job for automated news scraping"""
    
    def __init__(self):
        self.db: Optional[Session] = None
        self.scraper: Optional[NewsScraper] = None
    
    async def __aenter__(self):
        self.db = SessionLocal()
        self.scraper = NewsScraper()
        await self.scraper.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.scraper:
            await self.scraper.__aexit__(exc_type, exc_val, exc_tb)
        if self.db:
            self.db.close()
    
    def get_all_news_feed_configs(self) -> List[NewsFeedConfig]:
        """Fetch all NewsFeedConfig records from database"""
        try:
            configs = self.db.query(NewsFeedConfig).all()
            print(f"Found {len(configs)} NewsFeedConfig records")
            return configs
        except Exception as e:
            print(f"Error fetching NewsFeedConfig records: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_existing_source_urls(self) -> set:
        """Get all existing source_urls from News table to avoid duplicates"""
        try:
            result = self.db.query(News.source_url).filter(News.source_url.isnot(None)).all()
            source_urls = {url[0] for url in result if url[0]}
            print(f"Found {len(source_urls)} existing news articles in database")
            return source_urls
        except Exception as e:
            print(f"Error fetching existing source URLs: {e}")
            import traceback
            traceback.print_exc()
            return set()
    
    def filter_geo_news(self, news_items: List[NewsDetails], filter_geo: bool) -> List[NewsDetails]:
        """Filter news by geographic location (Canada/US) if geo filter is enabled"""
        if not filter_geo:
            return news_items
        
        geo_keywords = ['canada', 'canadian', 'us', 'usa', 'united states', 'american', 
                       'ontario', 'quebec', 'alberta', 'british columbia', 'toronto', 
                       'vancouver', 'montreal', 'calgary', 'edmonton', 'ottawa',
                       'new york', 'california', 'texas', 'florida', 'washington']
        
        filtered_news = []
        for news in news_items:
            # Check title, content, and short_description for geo keywords
            search_text = ' '.join([
                news.title or '',
                news.content or '',
                news.short_description or ''
            ]).lower()
            
            if any(keyword in search_text for keyword in geo_keywords):
                filtered_news.append(news)
        
        print(f"Geo filter applied: {len(news_items)} -> {len(filtered_news)} news items")
        return filtered_news
    
    def save_news_to_database(self, news_item: NewsDetails, microsite_id: str) -> bool:
        """Save a single news item to the News table"""
        try:
            # Generate unique ID for the news item
            news_id = str(uuid.uuid4())
            
            # Convert content_blocks, links, and subheadings to JSON strings
            content_blocks_json = None
            if news_item.content_blocks:
                blocks_list = []
                for block in news_item.content_blocks:
                    if hasattr(block, 'dict'):
                        blocks_list.append(block.dict())
                    elif isinstance(block, dict):
                        blocks_list.append(block)
                    else:
                        blocks_list.append(str(block))
                content_blocks_json = json.dumps(blocks_list)
            
            links_json = None
            if news_item.links:
                links_list = []
                for link in news_item.links:
                    if hasattr(link, 'dict'):
                        links_list.append(link.dict())
                    elif isinstance(link, dict):
                        links_list.append(link)
                    else:
                        links_list.append(str(link))
                links_json = json.dumps(links_list)
            
            subheadings_json = None
            if news_item.subheadings:
                if isinstance(news_item.subheadings, list):
                    subheadings_json = json.dumps(news_item.subheadings)
                else:
                    subheadings_json = json.dumps([str(news_item.subheadings)])
            
            # Create News record
            news_record = News(
                id=news_id,
                micrositeId=microsite_id,
                title=news_item.title or 'Untitled',
                slug=news_item.title or 'Untitled',
                summary=None,  # Can be set from short_description if needed
                content=news_item.content,
                content_html=news_item.content_html,
                content_blocks=content_blocks_json,
                links=links_json,
                subheadings=subheadings_json,
                author=news_item.author,
                short_description=news_item.short_description,
                tagline=news_item.tagline,
                source_url=news_item.source_url,
                coverImage=news_item.image_url,
                videoUrl=news_item.video_url,
                thumbnail=news_item.thumbnail_url or news_item.image_url,  # Use thumbnail_url from list page, fallback to image_url
                publishDate=news_item.date_time,
                landingPublishAt=news_item.date_time,  # Use same date
                isFeatured=False,
                createdAt=datetime.utcnow(),
                updatedAt=datetime.utcnow()
            )
            
            self.db.add(news_record)
            self.db.commit()
            print(f"✅ Saved news: {news_item.title[:50]}...")
            return True
            
        except Exception as e:
            print(f"❌ Error saving news to database: {e}")
            import traceback
            traceback.print_exc()
            self.db.rollback()
            return False
    
    async def process_feed_config(self, config: NewsFeedConfig) -> int:
        """Process a single NewsFeedConfig record"""
        microsite_id = config.micrositeId
        config_data = config.config if isinstance(config.config, dict) else json.loads(config.config) if isinstance(config.config, str) else {}
        
        feeds = config_data.get('feeds', [])
        if not feeds:
            print(f"No feeds found in config for micrositeId: {microsite_id}")
            return 0
        
        print(f"\n{'='*60}")
        print(f"Processing micrositeId: {microsite_id}")
        print(f"Found {len(feeds)} feed(s)")
        print(f"{'='*60}")
        
        total_saved = 0
        
        # Get existing source URLs to avoid duplicates
        existing_urls = self.get_existing_source_urls()
        
        for feed in feeds:
            feed_url = feed.get('url')
            if not feed_url:
                print(f"⚠️ Skipping feed with no URL")
                continue
            
            fields = feed.get('fields', {})
            filter_geo = fields.get('geo', False)
            
            print(f"\n📰 Scraping feed: {feed_url}")
            print(f"   Geo filter: {'Enabled (Canada/US only)' if filter_geo else 'Disabled'}")
            
            try:
                # Scrape news from the feed URL
                scraping_response = await self.scraper.scrape_news(feed_url, max_news=5)
                
                if not scraping_response or not scraping_response.news:
                    print(f"   ⚠️ No news found from this feed")
                    continue
                
                # Filter out already scraped news
                new_news = [news for news in scraping_response.news 
                           if news.source_url and news.source_url not in existing_urls]
                
                if not new_news:
                    print(f"   ⚠️ All news from this feed already exists in database")
                    continue
                
                print(f"   Found {len(new_news)} new news articles")
                
                # Apply geo filter if enabled
                if filter_geo:
                    new_news = self.filter_geo_news(new_news, filter_geo=True)
                    if not new_news:
                        print(f"   ⚠️ No news matches geo filter (Canada/US)")
                        continue
                
                # Save each news item to database
                for news_item in new_news:
                    if self.save_news_to_database(news_item, microsite_id):
                        total_saved += 1
                        # Add to existing URLs to avoid duplicates within same run
                        if news_item.source_url:
                            existing_urls.add(news_item.source_url)
                
            except Exception as e:
                print(f"   ❌ Error scraping feed {feed_url}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        print(f"\n✅ Processed micrositeId {microsite_id}: Saved {total_saved} news articles")
        return total_saved
    
    async def run(self):
        """Main cron job execution"""
        print("\n" + "="*60)
        print("🚀 Starting News Cron Job")
        print(f"⏰ Time: {datetime.utcnow().isoformat()}")
        print("="*60 + "\n")
        
        try:
            # Get all NewsFeedConfig records
            configs = self.get_all_news_feed_configs()
            
            if not configs:
                print("⚠️ No NewsFeedConfig records found. Exiting.")
                return
            
            total_saved_all = 0
            
            # Process each config
            for config in configs:
                try:
                    saved_count = await self.process_feed_config(config)
                    total_saved_all += saved_count
                except Exception as e:
                    print(f"❌ Error processing config {config.id}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            print("\n" + "="*60)
            print(f"✅ Cron Job Completed Successfully")
            print(f"📊 Total news articles saved: {total_saved_all}")
            print(f"⏰ End Time: {datetime.utcnow().isoformat()}")
            print("="*60 + "\n")
            
        except Exception as e:
            print(f"\n❌ Fatal error in cron job: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


async def main():
    """Main entry point for cron job"""
    async with NewsCronJob() as cron_job:
        await cron_job.run()


if __name__ == "__main__":
    # Run the cron job
    asyncio.run(main())

