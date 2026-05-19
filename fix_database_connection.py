#!/usr/bin/env python3
"""
Fix database connection issues
"""

import os
import sys
import subprocess

def install_dependencies():
    """Install required dependencies"""
    print("📦 Installing database dependencies...")
    
    try:
        subprocess.run([
            sys.executable, "-m", "pip", "install", 
            "psycopg2-binary", "sqlalchemy", "PyPDF2", "pdfplumber", "asyncpg"
        ], check=True, capture_output=True, text=True)
        print("✅ Dependencies installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        return False

def test_database_connection():
    """Test database connection with different methods"""
    print("\n🧪 Testing database connection...")
    
    # Method 1: Direct psycopg2 connection
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="51.79.67.246",
            port="5432",
            user="mlo_user",
            password="MLO@55w0rd2025",
            database="MLOdb"
        )
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        print("✅ Direct psycopg2 connection successful")
        return True
    except Exception as e:
        print(f"❌ Direct connection failed: {e}")
    
    # Method 2: URL connection
    try:
        from urllib.parse import quote_plus
        import psycopg2
        
        password_encoded = quote_plus("MLO@55w0rd2025")
        url = f"postgresql://mlo_user:{password_encoded}@51.79.67.246:5432/MLOdb"
        
        conn = psycopg2.connect(url)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        print("✅ URL connection successful")
        return True
    except Exception as e:
        print(f"❌ URL connection failed: {e}")
    
    return False

def test_sqlalchemy_connection():
    """Test SQLAlchemy connection"""
    print("\n🧪 Testing SQLAlchemy connection...")
    
    try:
        from sqlalchemy import create_engine, text
        from urllib.parse import quote_plus
        
        password_encoded = quote_plus("MLO@55w0rd2025")
        url = f"postgresql://mlo_user:{password_encoded}@51.79.67.246:5432/MLOdb"
        
        engine = create_engine(url, pool_pre_ping=True)
        
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("✅ SQLAlchemy connection successful")
            return True
    except Exception as e:
        print(f"❌ SQLAlchemy connection failed: {e}")
        return False

def main():
    """Main fix function"""
    print("🔧 Fixing database connection issues...\n")
    
    # Step 1: Install dependencies
    if not install_dependencies():
        print("❌ Failed to install dependencies")
        return False
    
    # Step 2: Test direct connection
    if not test_database_connection():
        print("❌ Database connection test failed")
        print("Please check:")
        print("1. Database server is running")
        print("2. Network connectivity to 51.79.67.246:5432")
        print("3. Credentials are correct")
        return False
    
    # Step 3: Test SQLAlchemy connection
    if not test_sqlalchemy_connection():
        print("❌ SQLAlchemy connection test failed")
        return False
    
    print("\n✅ All database connection tests passed!")
    print("\n📋 Next steps:")
    print("1. Restart PM2: pm2 restart news-events-scraper")
    print("2. Test APIs: curl -X POST 'https://news.mininglifeserver.com/jobs/score' -H 'Content-Type: application/json' -d '{\"job_id\": \"test\"}'")
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
