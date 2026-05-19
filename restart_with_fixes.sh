#!/bin/bash

echo "🔧 Applying SQLAlchemy 2.0 fixes and restarting service..."

# Activate virtual environment
source venv/bin/activate

echo "📦 Installing/updating dependencies..."
pip install psycopg2-binary sqlalchemy PyPDF2 pdfplumber asyncpg

echo "🧪 Testing database connection..."
python test_db_simple.py

if [ $? -eq 0 ]; then
    echo "✅ Database connection test passed!"
    
    echo "🔄 Restarting PM2 service..."
    pm2 restart news-events-scraper
    
    echo "📊 Checking PM2 status..."
    pm2 status
    
    echo "📋 Checking logs..."
    pm2 logs news-events-scraper --lines 10
    
    echo "✅ Service restarted successfully!"
    echo ""
    echo "🧪 Test your APIs:"
    echo "curl -X POST 'https://news.mininglifeserver.com/jobs/score' -H 'Content-Type: application/json' -d '{\"job_id\": \"test\"}'"
    echo "curl -X POST 'https://news.mininglifeserver.com/resumes/score' -H 'Content-Type: application/json' -d '{\"document_id\": \"test\", \"url\": \"https://example.com/resume.pdf\"}'"
else
    echo "❌ Database connection test failed!"
    echo "Please check your database configuration."
    exit 1
fi
