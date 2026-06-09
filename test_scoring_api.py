#!/usr/bin/env python3
"""
Test script for scoring APIs
"""

import requests
import json
import sys

def test_job_scoring_api():
    """Test job scoring API"""
    print("🔄 Testing job scoring API...")
    
    url = "http://localhost:8889/jobs/score"
    data = {
        "job_id": "test_job_123"
    }
    
    try:
        response = requests.post(url, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Job scoring API working - {result['total_resumes']} resumes scored")
            return True
        else:
            print(f"❌ Job scoring API failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Job scoring API error: {e}")
        return False

def test_resume_scoring_api():
    """Test resume scoring API"""
    print("🔄 Testing resume scoring API...")
    
    url = "http://localhost:8889/resumes/score"
    data = {
        "document_id": "test_doc_123",
        "url": "https://example.com/sample_resume.pdf",
        "job_ids": ["test_job_123"]
    }
    
    try:
        response = requests.post(url, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Resume scoring API working - {result['total_jobs']} jobs scored")
            return True
        else:
            print(f"❌ Resume scoring API failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Resume scoring API error: {e}")
        return False

def test_health_check():
    """Test health check endpoint"""
    print("🔄 Testing health check...")
    
    try:
        response = requests.get("http://localhost:8889/health", timeout=10)
        if response.status_code == 200:
            print("✅ Health check passed")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Health check error: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Testing News & Events Scraper API...")
    
    # Test health check first
    if not test_health_check():
        print("❌ API is not running. Please start it first:")
        print("python main.py")
        return False
    
    # Test scoring APIs
    job_test = test_job_scoring_api()
    resume_test = test_resume_scoring_api()
    
    if job_test and resume_test:
        print("\n✅ All API tests passed!")
        print("\n📋 Database scores should be saved to ResumeJobScore table")
        return True
    else:
        print("\n❌ Some API tests failed")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
