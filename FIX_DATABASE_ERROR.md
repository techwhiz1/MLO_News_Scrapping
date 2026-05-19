# Fix Database Connection Error

## Problem
```
sqlalchemy.exc.NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:postgres
```

## Solution

### Step 1: Install Required Dependencies
```bash
# Activate virtual environment
source venv/bin/activate

# Install PostgreSQL driver
pip install psycopg2-binary

# Install other required packages
pip install sqlalchemy PyPDF2 pdfplumber

# Or install all at once
pip install -r requirements.txt
```

### Step 2: Test Database Connection
```bash
# Test the database connection
python test_database.py
```

### Step 3: Run the Fix Script
```bash
# Run the automated fix script
python fix_dependencies.py
```

### Step 4: Start the API
```bash
# Start the API directly
python main.py

# Or restart PM2
pm2 restart news-events-scraper
```

## Manual Fix (if automated script fails)

### 1. Check Virtual Environment
```bash
# Make sure you're in the right directory
cd /home/ubuntu/News_Events_Scraper

# Activate virtual environment
source venv/bin/activate

# Check if psycopg2 is installed
python -c "import psycopg2; print('psycopg2 is installed')"
```

### 2. Install Missing Packages
```bash
# Install PostgreSQL driver
pip install psycopg2-binary

# Install SQLAlchemy
pip install sqlalchemy

# Install PDF processing libraries
pip install PyPDF2 pdfplumber
```

### 3. Test Database Connection
```bash
# Test database connection
python test_database.py
```

### 4. Start the API
```bash
# Start the API
python main.py
```

## Troubleshooting

### If psycopg2 installation fails:
```bash
# Install system dependencies first
sudo apt-get update
sudo apt-get install libpq-dev python3-dev

# Then install psycopg2
pip install psycopg2-binary
```

### If database connection fails:
1. Check if the database URL is correct
2. Verify network connectivity to the database
3. Check if the database server is running
4. Verify credentials

### If SQLAlchemy still fails:
```bash
# Try installing specific version
pip install sqlalchemy==2.0.23
pip install psycopg2-binary==2.9.7
```

## Expected Output
After successful installation, you should see:
```
✅ psycopg2 imported successfully
✅ Database connection successful!
✅ EmployeeProfile table accessible - X records
✅ JobPost table accessible - Y records
✅ All database tests passed!
```

## Next Steps
1. Test the API endpoints
2. Check PM2 status
3. Monitor logs for any issues
