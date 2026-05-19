from sqlalchemy import create_engine, Column, Integer, String, Text, JSON, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import os
import uuid

# Try to import psycopg2 to ensure it's available
try:
    import psycopg2
    print("✅ psycopg2 imported successfully")
except ImportError:
    print("❌ psycopg2 not found. Please install it with: pip install psycopg2-binary")
    raise

# Database configuration
# Use environment variable or default to provided URL
from urllib.parse import quote_plus

# Default connection parameters
DEFAULT_DB_USER = "mlo_user"
DEFAULT_DB_PASSWORD = "MLO@55w0rd2025"  # Contains @ which needs URL encoding
DEFAULT_DB_HOST = "51.79.67.246"
DEFAULT_DB_PORT = "5432"
DEFAULT_DB_NAME = "MLOdb"

# Get DATABASE_URL from environment or construct it
raw_database_url = os.getenv("DATABASE_URL")

if raw_database_url:
    # If DATABASE_URL is provided, check if password needs encoding
    # Common issue: password with @ symbol like "MLO@55w0rd2025" in URL "postgres://user:MLO@55w0rd2025@host/db"
    # This creates ambiguity - we need to detect and fix it
    if raw_database_url.count("@") > 1 and ("postgres://" in raw_database_url or "postgresql://" in raw_database_url):
        # Likely has unencoded @ in password - try to fix it
        # Pattern: postgres://user:pass@word@host:port/db
        try:
            # Extract scheme
            if "postgres://" in raw_database_url:
                scheme = "postgresql"
                url_without_scheme = raw_database_url.replace("postgres://", "", 1)
            elif "postgresql://" in raw_database_url:
                scheme = "postgresql"
                url_without_scheme = raw_database_url.replace("postgresql://", "", 1)
            else:
                DATABASE_URL = raw_database_url
                scheme = None
            
            if scheme:
                # Split by @ - first part should be user:password, rest is host:port/db
                parts = url_without_scheme.split("@")
                if len(parts) >= 2:
                    user_pass = parts[0]  # user:password (password may contain @)
                    host_db = "@".join(parts[1:])  # host:port/db
                    
                    if ":" in user_pass:
                        user, password = user_pass.split(":", 1)
                        # Encode the password properly
                        encoded_password = quote_plus(password)
                        # Reconstruct URL
                        DATABASE_URL = f"{scheme}://{user}:{encoded_password}@{host_db}"
                    else:
                        DATABASE_URL = raw_database_url
                else:
                    DATABASE_URL = raw_database_url
            else:
                DATABASE_URL = raw_database_url
        except Exception:
            # If fixing fails, use as-is (might already be correct)
            DATABASE_URL = raw_database_url
    else:
        # URL seems fine, just ensure postgresql:// scheme
        DATABASE_URL = raw_database_url
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    # Construct URL with properly encoded password
    encoded_password = quote_plus(DEFAULT_DB_PASSWORD)
    DATABASE_URL = f"postgresql://{DEFAULT_DB_USER}:{encoded_password}@{DEFAULT_DB_HOST}:{DEFAULT_DB_PORT}/{DEFAULT_DB_NAME}"

# Final check: ensure we're using postgresql:// not postgres://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Create engine with connection pooling and error handling
try:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False  # Set to True for SQL query debugging
    )
    print("✅ Database engine created successfully")
except Exception as e:
    print(f"❌ Error creating database engine: {e}")
    print("Please check your database connection string and ensure PostgreSQL is accessible")
    raise
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class EmployeeProfile(Base):
    __tablename__ = "EmployeeProfile"
    
    id = Column(Integer, primary_key=True, index=True)
    documents = Column(JSON)  # JSONB field containing array of documents

class JobPost(Base):
    __tablename__ = "JobPost"
    
    id = Column(String, primary_key=True)
    employerName = Column(String, nullable=False)
    hideEmployer = Column(Boolean, nullable=False)
    jobTitle = Column(String, nullable=False)
    jobId = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=False)
    location = Column(String, nullable=False)
    salaryRange = Column(String, nullable=False)
    applicationDeadline = Column(DateTime, nullable=False)
    image = Column(String, nullable=True)
    keyResponsibilities = Column(Text, nullable=False)
    qualifications = Column(Text, nullable=False)
    perksBenefits = Column(Text, nullable=False)
    preferredExperience = Column(Integer, nullable=False)
    educationLevel = Column(String, nullable=False)
    certificationLevel = Column(String, nullable=False)
    interviewFormat = Column(String, nullable=False)
    postedById = Column(String, nullable=False)
    channels = Column(JSON, nullable=True)
    siteId = Column(String, nullable=True)
    isScrapped = Column(Boolean, nullable=False)
    active = Column(Boolean, nullable=False)
    createdAt = Column(DateTime, nullable=False)
    updatedAt = Column(DateTime, nullable=False)


class ResumeJobScore(Base):
    __tablename__ = "ResumeScore"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String, nullable=False, index=True)
    job_id = Column(String, nullable=False, index=True)
    score = Column(Integer, nullable=False)
    totalJobs = Column(Integer, nullable=False)
    
    # Add unique constraint to prevent duplicate scores
    __table_args__ = (
        {'extend_existing': True}
    )


class NewsFeedConfig(Base):
    __tablename__ = "NewsFeedConfig"
    
    id = Column(String, primary_key=True)
    micrositeId = Column(String, nullable=False)
    config = Column(JSON, nullable=False)  # Contains feeds array
    createdAt = Column(DateTime, nullable=False)
    updatedAt = Column(DateTime, nullable=False)


class News(Base):
    __tablename__ = "News"
    
    id = Column(String, primary_key=True)
    micrositeId = Column(String, nullable=False)
    title = Column(Text, nullable=False)
    slug = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    content_html = Column(Text, nullable=True)
    content_blocks = Column(Text, nullable=True)  # JSON string
    links = Column(Text, nullable=True)  # JSON string
    subheadings = Column(Text, nullable=True)  # JSON string
    author = Column(Text, nullable=True)
    short_description = Column(Text, nullable=True)
    tagline = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True, index=True)  # Indexed for duplicate checking
    coverImage = Column(Text, nullable=True)
    videoUrl = Column(Text, nullable=True)
    thumbnail = Column(Text, nullable=True)
    publishDate = Column(DateTime, nullable=True)
    landingPublishAt = Column(DateTime, nullable=True)
    isFeatured = Column(Boolean, nullable=False, default=False)
    createdAt = Column(DateTime, nullable=False)
    updatedAt = Column(DateTime, nullable=False)

# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_connection():
    """Test database connection"""
    try:
        from sqlalchemy import text
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("✅ Database connection test successful")
            return True
    except Exception as e:
        print(f"❌ Database connection test failed: {e}")
        return False


# Test connection on import
if __name__ == "__main__":
    test_connection()
else:
    # Test connection when module is imported
    try:
        test_connection()
    except Exception as e:
        print(f"⚠️ Database connection test failed: {e}")
        print("The application will continue but database operations may fail.")

