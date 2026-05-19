#!/usr/bin/env python3
"""
Test script for news cron job setup
Run this to verify database connection and configuration
"""

import sys
from database import SessionLocal, NewsFeedConfig, News, engine, test_connection

def test_database_connection():
    """Test database connection"""
    print("Testing database connection...")
    if test_connection():
        print("✅ Database connection successful\n")
        return True
    else:
        print("❌ Database connection failed\n")
        return False

def test_news_feed_configs():
    """Test fetching NewsFeedConfig records"""
    print("Testing NewsFeedConfig table...")
    try:
        db = SessionLocal()
        configs = db.query(NewsFeedConfig).all()
        print(f"✅ Found {len(configs)} NewsFeedConfig record(s)")
        
        if configs:
            print("\nSample config:")
            config = configs[0]
            print(f"  ID: {config.id}")
            print(f"  Microsite ID: {config.micrositeId}")
            print(f"  Feeds count: {len(config.config.get('feeds', [])) if isinstance(config.config, dict) else 0}")
        
        db.close()
        print()
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        print()
        return False

def test_news_table():
    """Test News table access"""
    print("Testing News table...")
    try:
        db = SessionLocal()
        news_count = db.query(News).count()
        print(f"✅ News table accessible. Current records: {news_count}")
        
        # Check for existing source URLs
        existing_urls = db.query(News.source_url).filter(News.source_url.isnot(None)).limit(5).all()
        if existing_urls:
            print(f"  Sample source URLs: {len(existing_urls)} found")
        
        db.close()
        print()
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        print()
        return False

def main():
    """Run all tests"""
    print("="*60)
    print("News Cron Job Setup Test")
    print("="*60 + "\n")
    
    tests_passed = 0
    tests_total = 3
    
    if test_database_connection():
        tests_passed += 1
    
    if test_news_feed_configs():
        tests_passed += 1
    
    if test_news_table():
        tests_passed += 1
    
    print("="*60)
    print(f"Tests passed: {tests_passed}/{tests_total}")
    if tests_passed == tests_total:
        print("✅ All tests passed! Cron job should work correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

