#!/usr/bin/env python3
"""
Simple dependency installer for News & Events Scraper API
"""

import subprocess
import sys
import os

def install_package(package):
    """Install a single package"""
    print(f"🔄 Installing {package}...")
    try:
        result = subprocess.run([
            sys.executable, "-m", "pip", "install", package
        ], check=True, capture_output=True, text=True)
        print(f"✅ {package} installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install {package}: {e.stderr}")
        return False

def main():
    """Install all required packages"""
    print("🚀 Installing dependencies for News & Events Scraper API...")
    
    packages = [
        "psycopg2-binary",
        "sqlalchemy", 
        "PyPDF2",
        "pdfplumber"
    ]
    
    success = True
    for package in packages:
        if not install_package(package):
            success = False
    
    if success:
        print("\n✅ All dependencies installed successfully!")
        print("\n📋 Next steps:")
        print("1. Test database connection: python test_database.py")
        print("2. Start the API: python main.py")
        print("3. Or restart PM2: pm2 restart news-events-scraper")
    else:
        print("\n❌ Some dependencies failed to install. Please check the errors above.")
        return False
    
    return True

if __name__ == "__main__":
    main()
