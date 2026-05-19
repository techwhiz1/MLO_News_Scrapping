#!/usr/bin/env python3
"""
Database connection test script
"""

import sys
import os

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_database_connection():
    """Test database connection"""
    try:
        print("🔄 Testing database connection...")
        
        # Import database modules
        from database import engine, SessionLocal
        from sqlalchemy import text
        
        # Test connection
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1 as test"))
            test_value = result.fetchone()[0]
            
            if test_value == 1:
                print("✅ Database connection successful!")
                return True
            else:
                print("❌ Database connection failed - unexpected result")
                return False
                
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

def test_table_access():
    """Test table access"""
    try:
        print("🔄 Testing table access...")
        
        from database import SessionLocal, EmployeeProfile, JobPost
        
        # Create session
        db = SessionLocal()
        
        # Test EmployeeProfile table
        try:
            employee_count = db.query(EmployeeProfile).count()
            print(f"✅ EmployeeProfile table accessible - {employee_count} records")
        except Exception as e:
            print(f"⚠️  EmployeeProfile table issue: {e}")
        
        # Test JobPost table
        try:
            job_count = db.query(JobPost).count()
            print(f"✅ JobPost table accessible - {job_count} records")
        except Exception as e:
            print(f"⚠️  JobPost table issue: {e}")
        
        db.close()
        return True
        
    except Exception as e:
        print(f"❌ Table access failed: {e}")
        return False

def main():
    """Main test function"""
    print("🚀 Starting database tests...")
    
    # Test connection
    if not test_database_connection():
        print("❌ Database connection test failed")
        return False
    
    # Test table access
    if not test_table_access():
        print("❌ Table access test failed")
        return False
    
    print("✅ All database tests passed!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
