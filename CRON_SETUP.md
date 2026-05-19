# News Cron Job Setup Guide

This guide explains how to set up the daily news scraping cron job.

## Overview

The `news_cron_job.py` script automatically scrapes news from all configured feeds in the `NewsFeedConfig` table and saves them to the `News` table.

## Features

- ✅ Fetches all records from `NewsFeedConfig` table
- ✅ Processes each feed configuration
- ✅ Geo filtering (Canada/US) when `fields.geo` is true
- ✅ Duplicate detection using `source_url`
- ✅ Saves up to 5 latest news articles per feed
- ✅ Sets `slug` to 'untitled' and uses `micrositeId` from config

## Prerequisites

1. Python 3.11+ with all dependencies installed
2. Database connection configured (see `database.py`)
3. Virtual environment activated (if using one)

## Setup Cron Job

### Option 1: Using crontab (Recommended)

1. Open crontab editor:
   ```bash
   crontab -e
   ```

2. Add the following line to run daily at 2:00 AM:
   ```bash
   0 2 * * * cd /home/ubuntu/News_Events_Scraper && /home/ubuntu/News_Events_Scraper/venv/bin/python3 /home/ubuntu/News_Events_Scraper/news_cron_job.py >> /home/ubuntu/News_Events_Scraper/logs/news_cron.log 2>&1
   ```

   Or if not using virtual environment:
   ```bash
   0 2 * * * cd /home/ubuntu/News_Events_Scraper && python3 /home/ubuntu/News_Events_Scraper/news_cron_job.py >> /home/ubuntu/News_Events_Scraper/logs/news_cron.log 2>&1
   ```

3. To run at different times, adjust the schedule:
   - `0 2 * * *` - Daily at 2:00 AM
   - `0 */6 * * *` - Every 6 hours
   - `0 0 * * *` - Daily at midnight
   - `*/30 * * * *` - Every 30 minutes (for testing)

### Option 2: Manual Testing

Run the script manually to test:
```bash
cd /home/ubuntu/News_Events_Scraper
python3 news_cron_job.py
```

Or with virtual environment:
```bash
cd /home/ubuntu/News_Events_Scraper
source venv/bin/activate
python3 news_cron_job.py
```

## Database Configuration

The script uses the database URL from `database.py`. You can override it with an environment variable:

```bash
export DATABASE_URL="postgres://mlo_user:MLO@55w0rd2025@51.79.67.246:5432/MLOdb"
python3 news_cron_job.py
```

## How It Works

1. **Fetch Configs**: Retrieves all records from `NewsFeedConfig` table
2. **Process Each Config**:
   - Extracts `micrositeId` and `config.feeds` array
   - For each feed:
     - Scrapes news from the feed URL (max 5 articles)
     - Checks for duplicates using `source_url` in `News` table
     - Applies geo filter if `fields.geo` is true (filters for Canada/US news)
     - Saves new news articles to `News` table
3. **Save News**: Each news article is saved with:
   - Generated UUID as `id`
   - `micrositeId` from config
   - `slug` set to 'untitled'
   - All scraped fields mapped to News table columns

## Logging

Logs are written to:
- Console output (if run manually)
- Log file: `logs/news_cron.log` (if configured in crontab)

## Troubleshooting

### Import Errors
Make sure all dependencies are installed:
```bash
pip install -r requirements.txt
```

### Database Connection Errors
Check database connection in `database.py` and ensure:
- Database server is accessible
- Credentials are correct
- Network/firewall allows connection

### No News Scraped
- Check if `NewsFeedConfig` table has records
- Verify feed URLs are accessible
- Check logs for scraping errors
- Ensure news articles exist on the feed pages

### Duplicate News
The script automatically checks `source_url` to prevent duplicates. If duplicates still occur:
- Check if `source_url` is being set correctly
- Verify database indexes on `source_url` column

## Monitoring

To monitor cron job execution:
```bash
# View cron logs
tail -f /home/ubuntu/News_Events_Scraper/logs/news_cron.log

# Check cron job status
crontab -l

# View system cron logs
grep CRON /var/log/syslog
```

## Next Steps

After setting up the news cron job, you can also set up:
- Event scraping cron job (similar structure)
- Product scraping cron job (similar structure)

