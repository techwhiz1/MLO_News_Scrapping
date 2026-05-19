#!/usr/bin/env python3
"""
Test database connection and dependencies
"""

def test_imports():
    """Test if all required modules can be imported"""
    print("🧪 Testing imports...")
    
    try:
        import psycopg2
        print("✅ psycopg2 imported successfully")
    except ImportError as e:
        print(f"❌ psycopg2 import failed: {e}")
        return False
    
    try:
        import sqlalchemy
        print("✅ sqlalchemy imported successfully")
    except ImportError as e:
        print(f"❌ sqlalchemy import failed: {e}")
        return False
    
    try:
        import PyPDF2
        print("✅ PyPDF2 imported successfully")
    except ImportError as e:
        print(f"❌ PyPDF2 import failed: {e}")
        return False
    
    try:
        import pdfplumber
        print("✅ pdfplumber imported successfully")
    except ImportError as e:
        print(f"❌ pdfplumber import failed: {e}")
        return False
    
    return True

def test_database_connection():
    """Test database connection"""
    print("\n🧪 Testing database connection...")
    
    try:
        from database import engine, SessionLocal
        print("✅ Database modules imported successfully")
        
        # Test connection
        with engine.connect() as connection:
            result = connection.execute("SELECT 1")
            print("✅ Database connection successful")
            return True
            
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

def main():
    """Main test function"""
    print("🚀 Testing News & Events Scraper dependencies...\n")
    
    # Test imports
    imports_ok = test_imports()
    
    if imports_ok:
        # Test database connection
        db_ok = test_database_connection()
        
        if db_ok:
            print("\n✅ All tests passed! Your setup is ready.")
            return True
        else:
            print("\n❌ Database connection failed. Please check your database configuration.")
            return False
    else:
        print("\n❌ Import tests failed. Please install missing dependencies:")
        print("pip install psycopg2-binary sqlalchemy PyPDF2 pdfplumber asyncpg")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
