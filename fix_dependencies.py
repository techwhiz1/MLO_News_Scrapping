#!/usr/bin/env python3
"""
Fix dependencies for News & Events Scraper API
"""

import subprocess
import sys
import os

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed: {e}")
        print(f"Error output: {e.stderr}")
        return False

def main():
    """Main installation process"""
    print("🚀 Fixing dependencies for News & Events Scraper API...")
    
    # Check if virtual environment exists
    if not os.path.exists("venv"):
        print("❌ Virtual environment not found. Please create it first:")
        print("python3 -m venv venv")
        return False
    
    # Install missing dependencies
    dependencies = [
        "psycopg2-binary",
        "sqlalchemy", 
        "PyPDF2",
        "pdfplumber"
    ]
    
    for dep in dependencies:
        if not run_command(f"source venv/bin/activate && pip install {dep}", f"Installing {dep}"):
            print(f"❌ Failed to install {dep}")
            return False
    
    print("✅ All dependencies installed successfully!")
    
    # Test database connection
    print("\n🔄 Testing database connection...")
    if run_command("source venv/bin/activate && python test_database.py", "Testing database connection"):
        print("✅ Database connection test passed!")
    else:
        print("⚠️  Database connection test failed - check your database configuration")
    
    print("\n📋 Next steps:")
    print("1. Test the API: python main.py")
    print("2. Or restart PM2: pm2 restart news-events-scraper")

if __name__ == "__main__":
    main()
