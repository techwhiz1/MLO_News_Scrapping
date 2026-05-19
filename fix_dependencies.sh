#!/bin/bash

echo "🔧 Fixing database dependencies..."

# Activate virtual environment
source venv/bin/activate

# Install missing dependencies
echo "📦 Installing psycopg2-binary..."
pip install psycopg2-binary

echo "📦 Installing sqlalchemy..."
pip install sqlalchemy

echo "📦 Installing PyPDF2..."
pip install PyPDF2

echo "📦 Installing pdfplumber..."
pip install pdfplumber

echo "📦 Installing asyncpg..."
pip install asyncpg

echo "✅ Dependencies installed successfully!"
echo "🚀 You can now restart your PM2 service:"
echo "pm2 restart news-events-scraper"
