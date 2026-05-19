#!/usr/bin/env python3
"""
Installation script for News & Events Scraper API
This script installs the required dependencies for the scoring APIs
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
    print("🚀 Starting News & Events Scraper API installation...")
    
    # Check if virtual environment exists
    if not os.path.exists("venv"):
        print("📦 Creating virtual environment...")
        if not run_command("python3 -m venv venv", "Creating virtual environment"):
            return False
    
    # Activate virtual environment and install dependencies
    print("📦 Installing dependencies...")
    
    # Install requirements
    if not run_command("source venv/bin/activate && pip install --upgrade pip", "Upgrading pip"):
        return False
    
    if not run_command("source venv/bin/activate && pip install -r requirements.txt", "Installing requirements"):
        return False
    
    print("📄 Note: Added PDF processing libraries (PyPDF2, pdfplumber) for resume text extraction")
    
    print("✅ Installation completed successfully!")
    print("\n📋 Next steps:")
    print("1. Activate virtual environment: source venv/bin/activate")
    print("2. Start the API: python main.py")
    print("3. Or use PM2: pm2 start ecosystem.config.js")

if __name__ == "__main__":
    main()

