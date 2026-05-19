# News & Events Scraper API Documentation

## Overview
This API provides news scraping, event scraping, and job-resume scoring functionality using Crawl4AI and OpenAI.

## Base URL
- **Production**: `https://news.mininglifeserver.com`
- **Local**: `http://localhost:8889`

## Authentication
No authentication required for current endpoints.

## Endpoints

### 1. Health Check
- **GET** `/health`
- **Description**: Check API health status
- **Response**: `{"status": "healthy", "service": "News & Events Scraper API"}`

### 2. News Scraping
- **POST** `/scrape-news`
- **Description**: Scrape news article from URL
- **Request Body**:
```json
{
  "news_url": "https://example.com/news-article"
}
```
- **Response**: News article data with title, content, author, etc.

### 3. Event Scraping
- **POST** `/scrape-events`
- **Description**: Scrape events from event list page
- **Request Body**:
```json
{
  "event_list_url": "https://example.com/events"
}
```
- **Response**: Array of event details

### 4. Job Scoring (NEW)
- **POST** `/jobs/score`
- **Description**: Score a specific job against all resumes and save results to database
- **Request Body**:
```json
{
  "job_id": "job_123"
}
```
- **Response**:
```json
{
  "scores": [
    {
      "document_id": "doc_123",
      "job_id": "job_123",
      "score": 85
    }
  ],
  "total_resumes": 1
}
```
- **Database**: Scores are automatically saved to `ResumeJobScore` table

### 5. Resume Scoring (NEW)
- **POST** `/resumes/score`
- **Description**: Score a specific resume against all jobs and save results to database
- **Request Body**:
```json
{
  "document_id": "doc_123",
  "url": "https://example.com/resume.pdf"
}
```
- **Response**:
```json
{
  "scores": [
    {
      "document_id": "doc_123",
      "job_id": "job_456",
      "score": 78
    }
  ],
  "total_jobs": 1
}
```
- **Database**: Scores are automatically saved to `ResumeJobScore` table

## Database Schema

### EmployeeProfile Table
- `id`: Primary key
- `documents`: JSONB field containing array of documents
  - Each document has: `id`, `url`, `kind` (e.g., "Resume")

### JobPost Table
- `id`: Primary key
- `jobId`: Job identifier
- `employerName`: Company name
- `jobTitle`: Job title
- `description`: Job description
- `location`: Job location
- `salaryRange`: Salary information
- `keyResponsibilities`: Key responsibilities
- `qualifications`: Required qualifications
- `perksBenefits`: Benefits and perks
- `preferredExperience`: Experience requirements
- `educationLevel`: Education requirements
- `certificationLevel`: Certification requirements
- `interviewFormat`: Interview process

### ResumeJobScore Table
- `id`: Primary key (auto-increment)
- `document_id`: Document identifier (indexed)
- `job_id`: Job identifier (indexed)
- `score`: Compatibility score (0-100)
- `created_at`: Timestamp when score was created

## Scoring Algorithm

The scoring system uses OpenAI's GPT-4o-mini model to analyze compatibility between jobs and resumes:

### Scoring Criteria (0-100 scale):
- **0-20**: Poor match (very few requirements met)
- **21-40**: Below average match (some requirements met)
- **41-60**: Average match (moderate requirements met)
- **61-80**: Good match (most requirements met)
- **81-100**: Excellent match (all or nearly all requirements met)

### Factors Considered:
- Skills and experience alignment
- Education level match
- Experience level appropriateness
- Location compatibility
- Industry relevance

## Error Handling

All endpoints return appropriate HTTP status codes:
- `200`: Success
- `400`: Bad Request
- `404`: Not Found
- `500`: Internal Server Error

Error responses include:
```json
{
  "error": "Error Type",
  "message": "Detailed error message",
  "status_code": 500
}
```

## Rate Limits

- No explicit rate limits currently implemented
- OpenAI API has its own rate limits
- Consider implementing rate limiting for production use

## Installation

1. **Install Dependencies**:
```bash
python install_dependencies.py
```

2. **Manual Installation**:
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

3. **Start API**:
```bash
# Direct start
python main.py

# Using PM2
pm2 start ecosystem.config.js
```

## Environment Variables

- `OPENAI_API_KEY`: OpenAI API key for scoring functionality
- Database connection is configured in `database.py`

## Monitoring

- Check PM2 status: `pm2 status`
- View logs: `pm2 logs news-events-scraper`
- Restart service: `pm2 restart news-events-scraper`

