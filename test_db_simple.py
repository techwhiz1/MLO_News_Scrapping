#!/usr/bin/env python3
"""
Simple database connection test
"""

import psycopg2
from urllib.parse import quote_plus

def test_direct_connection():
    """Test direct psycopg2 connection"""
    try:
        # Connection parameters
        host = "51.79.67.246"
        port = "5432"
        user = "mlo_user"
        password = "MLO@55w0rd2025"
        database = "MLOdb"
        
        print(f"🔗 Testing connection to {host}:{port}")
        print(f"📊 Database: {database}")
        print(f"👤 User: {user}")
        
        # Test connection
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        
        # Test query
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        
        print("✅ Direct connection successful!")
        print(f"📋 Test query result: {result}")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Direct connection failed: {e}")
        return False

def test_url_connection():
    """Test connection using URL format"""
    try:
        # URL encode the password
        password_encoded = quote_plus("MLO@55w0rd2025")
        url = f"postgresql://mlo_user:{password_encoded}@51.79.67.246:5432/MLOdb"
        
        print(f"🔗 Testing URL connection")
        print(f"📝 URL: postgresql://mlo_user:***@51.79.67.246:5432/MLOdb")
        
        # Test connection
        conn = psycopg2.connect(url)
        
        # Test query
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        
        print("✅ URL connection successful!")
        print(f"📋 Test query result: {result}")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ URL connection failed: {e}")
        return False

def test_sqlalchemy_connection():
    """Test SQLAlchemy connection with proper 2.0 syntax"""
    try:
        from sqlalchemy import create_engine, text
        from urllib.parse import quote_plus
        
        password_encoded = quote_plus("MLO@55w0rd2025")
        url = f"postgresql://mlo_user:{password_encoded}@51.79.67.246:5432/MLOdb"
        
        print(f"🔗 Testing SQLAlchemy connection")
        print(f"📝 URL: postgresql://mlo_user:***@51.79.67.246:5432/MLOdb")
        
        engine = create_engine(url, pool_pre_ping=True)
        
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("✅ SQLAlchemy connection successful!")
            print(f"📋 Test query result: {result.fetchone()}")
            return True
        
    except Exception as e:
        print(f"❌ SQLAlchemy connection failed: {e}")
        return False

def main():
    """Main test function"""
    print("🧪 Testing database connections...\n")
    
    # Test direct connection
    print("1️⃣ Testing direct connection:")
    direct_ok = test_direct_connection()
    
    print("\n2️⃣ Testing URL connection:")
    url_ok = test_url_connection()
    
    print("\n3️⃣ Testing SQLAlchemy connection:")
    sqlalchemy_ok = test_sqlalchemy_connection()
    
    if direct_ok and url_ok and sqlalchemy_ok:
        print("\n✅ All connection tests passed!")
        return True
    elif direct_ok and sqlalchemy_ok:
        print("\n✅ Direct and SQLAlchemy connections work!")
        print("⚠️ URL connection failed, but this is not critical.")
        return True
    else:
        print("\n❌ Connection tests failed.")
        print("Please check your database credentials and network connectivity.")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
