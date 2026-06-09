import asyncio
import json
import io
import os
from typing import List, Dict, Any, Optional
from openai import OpenAI
from database import get_db, EmployeeProfile, JobPost, ResumeJobScore
from models import ScoreResult
import httpx
from bs4 import BeautifulSoup
import PyPDF2
import pdfplumber


class ScoringService:
    """Service for scoring job-resume compatibility using OpenAI"""
    
    def __init__(self):
        self.openai_client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY", "")
        )
    
    async def get_resume_content(self, url: str) -> str:
        """Extract resume content from PDF URL"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                response.raise_for_status()
                
                # Check if it's a PDF file
                content_type = response.headers.get('content-type', '').lower()
                if 'pdf' in content_type or url.lower().endswith('.pdf'):
                    # Extract text from PDF content
                    try:
                        # Try pdfplumber first (better for complex layouts)
                        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
                            text_content = ""
                            for page in pdf.pages:
                                page_text = page.extract_text()
                                if page_text:
                                    text_content += page_text + "\n"
                            
                            if text_content.strip():
                                return text_content[:4000]  # Limit to 4000 characters
                        
                        # Fallback to PyPDF2 if pdfplumber fails
                        pdf_reader = PyPDF2.PdfReader(io.BytesIO(response.content))
                        text_content = ""
                        for page in pdf_reader.pages:
                            text_content += page.extract_text() + "\n"
                        
                        return text_content[:4000]  # Limit to 4000 characters
                        
                    except Exception as pdf_error:
                        print(f"Error extracting PDF text: {pdf_error}")
                        return f"PDF Resume Content from: {url} (Text extraction failed)"
                else:
                    # Parse HTML content for non-PDF files
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Remove unwanted elements
                    for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
                        element.decompose()
                    
                    # Extract text content
                    text_content = soup.get_text()
                    cleaned_text = ' '.join(text_content.split())
                    
                    return cleaned_text[:4000]  # Limit to 4000 characters
                
        except Exception as e:
            print(f"Error fetching resume content: {e}")
            return ""
    
    def create_job_description(self, job: JobPost) -> str:
        """Create comprehensive job description from job post fields"""
        job_description = f"""
Job Title: {job.jobTitle or ''}
Employer: {job.employerName or ''}
Location: {job.location or ''}
Salary Range: {job.salaryRange or ''}

Description:
{job.description or ''}

Key Responsibilities:
{job.keyResponsibilities or ''}

Qualifications:
{job.qualifications or ''}

Preferred Experience: {job.preferredExperience or 0} years

Education Level: {job.educationLevel or ''}
Certification Level: {job.certificationLevel or ''}

Perks & Benefits:
{job.perksBenefits or ''}

Interview Format: {job.interviewFormat or ''}
"""
        return job_description.strip()
    
    async def score_compatibility(self, job_description: str, resume_content: str) -> int:
        """Score compatibility between job and resume using OpenAI"""
        try:
            prompt = f"""
You are a recruitment expert. Please analyze the compatibility between this job posting and resume.

Job Description:
{job_description}

Resume Content:
{resume_content}

Rate the compatibility on a scale of 0-100 where:
- 0-20: Poor match (very few requirements met)
- 21-40: Below average match (some requirements met)
- 41-60: Average match (moderate requirements met)
- 61-80: Good match (most requirements met)
- 81-100: Excellent match (all or nearly all requirements met)

Consider:
- Skills and experience alignment
- Education level match
- Experience level appropriateness
- Location compatibility
- Industry relevance

Return only a number between 0 and 100.
"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a recruitment expert. Return only a number between 0-100."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=10
            )
            
            # Extract score from response
            score_text = response.choices[0].message.content.strip()
            
            # Try to extract number from response
            import re
            numbers = re.findall(r'\d+', score_text)
            if numbers:
                score = int(numbers[0])
                return max(0, min(100, score))  # Ensure score is between 0-100
            
            return 50  # Default score if parsing fails
            
        except Exception as e:
            print(f"Error in OpenAI scoring: {e}")
            return 50  # Default score on error
    
    def save_scores_to_database(self, scores: List[ScoreResult], db, total_jobs: Optional[int] = None) -> bool:
        """Save scores to ResumeJobScore table"""
        try:
            total_jobs = total_jobs if total_jobs is not None else len(scores)
            for score_result in scores:
                # Check if score already exists
                existing_score = db.query(ResumeJobScore).filter(
                    ResumeJobScore.document_id == score_result.document_id,
                    ResumeJobScore.job_id == score_result.job_id
                ).first()
                
                if existing_score:
                    # Update existing score
                    existing_score.score = score_result.score
                    existing_score.totalJobs = total_jobs
                    print(f"Updated score for document {score_result.document_id} and job {score_result.job_id}: {score_result.score}")
                else:
                    # Create new score record
                    new_score = ResumeJobScore(
                        document_id=score_result.document_id,
                        job_id=score_result.job_id,
                        score=score_result.score,
                        totalJobs=total_jobs
                    )
                    db.add(new_score)
                    print(f"Saved new score for document {score_result.document_id} and job {score_result.job_id}: {score_result.score}")
            
            # Commit all changes
            db.commit()
            print(f"Successfully saved {len(scores)} scores to database")
            return True
            
        except Exception as e:
            print(f"Error saving scores to database: {e}")
            db.rollback()
            return False
    
    async def score_job_against_resumes(self, job_id: str, db) -> List[ScoreResult]:
        """Score a specific job against all resumes"""
        try:
            # Test database connection first
            try:
                from sqlalchemy import text
                db.execute(text("SELECT 1"))
            except Exception as db_error:
                raise Exception(f"Database connection failed: {db_error}")
            
            # Get job details
            job = db.query(JobPost).filter(JobPost.jobId == job_id).first()
            if not job:
                raise Exception(f"Job with ID {job_id} not found")
            
            job_description = self.create_job_description(job)
            
            # Get all employee profiles with resume documents
            profiles = db.query(EmployeeProfile).all()
            
            scores = []
            
            for profile in profiles:
                if not profile.documents:
                    continue
                
                # Find resume documents
                for doc in profile.documents:
                    if doc.get('kind') == 'Resume':
                        document_id = doc.get('id')
                        resume_url = doc.get('url')
                        
                        if document_id and resume_url:
                            try:
                                # Get resume content
                                resume_content = await self.get_resume_content(resume_url)
                                
                                if resume_content:
                                    # Score compatibility
                                    score = await self.score_compatibility(job_description, resume_content)
                                    
                                    scores.append(ScoreResult(
                                        document_id=document_id,
                                        job_id=job_id,
                                        score=score
                                    ))
                                    
                                    print(f"Scored resume {document_id} for job {job_id}: {score}")
                                
                            except Exception as e:
                                print(f"Error processing resume {document_id}: {e}")
                                continue
            
            # Save scores to database
            if scores:
                self.save_scores_to_database(scores, db)
            
            return scores
            
        except Exception as e:
            print(f"Error in job scoring: {e}")
            raise
    
    async def score_resume_against_jobs(self, document_id: str, resume_url: str, job_ids: List[str], db) -> List[ScoreResult]:
        """Score a specific resume against selected jobs"""
        try:
            # Test database connection first
            try:
                from sqlalchemy import text
                db.execute(text("SELECT 1"))
            except Exception as db_error:
                raise Exception(f"Database connection failed: {db_error}")

            requested_job_ids = list(dict.fromkeys(job_id.strip() for job_id in job_ids if job_id and job_id.strip()))
            if not requested_job_ids:
                raise ValueError("At least one valid job_id is required")
            
            # Get resume content
            resume_content = await self.get_resume_content(resume_url)
            if not resume_content:
                raise Exception("Could not fetch resume content")
            
            # Get requested jobs only
            jobs = db.query(JobPost).filter(JobPost.jobId.in_(requested_job_ids)).all()
            jobs_by_id = {job.jobId: job for job in jobs}
            missing_job_ids = [job_id for job_id in requested_job_ids if job_id not in jobs_by_id]
            if missing_job_ids:
                raise ValueError(f"Jobs not found: {', '.join(missing_job_ids)}")
            
            scores = []
            
            for job_id in requested_job_ids:
                job = jobs_by_id[job_id]
                try:
                    job_description = self.create_job_description(job)
                    
                    # Score compatibility
                    score = await self.score_compatibility(job_description, resume_content)
                    
                    scores.append(ScoreResult(
                        document_id=document_id,
                        job_id=job.jobId,
                        score=score
                    ))
                    
                    print(f"Scored resume {document_id} against job {job.jobId}: {score}")
                    
                except Exception as e:
                    print(f"Error processing job {job.jobId}: {e}")
                    continue
            
            # Save scores to database
            if scores:
                self.save_scores_to_database(scores, db, total_jobs=len(requested_job_ids))
            
            return scores
            
        except Exception as e:
            print(f"Error in resume scoring: {e}")
            raise
