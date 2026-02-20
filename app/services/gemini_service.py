import google.generativeai as genai
import os
import json
import time
import logging
from dotenv import load_dotenv

# 🔹 Import schemas from your schema file
from .ai_schemas import (
    candidate_schema,
    interviewer_schema,
    jd_summary_schema,
)

load_dotenv()
logger = logging.getLogger(__name__)

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found")

genai.configure(api_key=api_key)

# ✅ Using gemini-2.0-flash as requested
model = genai.GenerativeModel("gemini-2.0-flash")

MAX_RETRIES = 3


# ─────────────────────────────────────────────────────────
# CORE SCHEMA CALL
# ─────────────────────────────────────────────────────────
def _parse_with_retry(prompt: str, fallback: dict, schema: dict) -> dict:
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = model.generate_content(
                prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                    "temperature": 0.3,
                },
            )

            if not response.text:
                raise ValueError("Empty Gemini response")

            logger.info("Gemini RAW:\n%s", response.text)

            parsed = json.loads(response.text)

            logger.info("Gemini PARSED:\n%s", parsed)

            return parsed

        except Exception as e:
            logger.warning(f"Gemini retry {attempt}/{MAX_RETRIES}: {e}")
            last_error = e
            time.sleep(1)

    logger.error(f"Gemini failed after retries: {last_error}")
    return fallback


# ─────────────────────────────────────────────────────────
# JD SUMMARY
# ─────────────────────────────────────────────────────────
def generate_jd_summary(jd_text: str) -> dict:
    prompt = f"""
Analyze this Job Description and generate a structured summary.

Job Description:
{jd_text}
"""

    fallback = {
        "job_metadata": {"job_name": "Unknown", "company_name": "Unknown"},
        "job_requirements": {"must_have_skills": [], "experience": {"minimum_years": 0}},
        "job_responsibilities": {"primary_duties": []},
        "keywords": [],
        "job_summary": "Summary unavailable",
    }

    return _parse_with_retry(prompt, fallback, jd_summary_schema)


# ─────────────────────────────────────────────────────────
# RESUME PROFILE ANALYSIS
# ─────────────────────────────────────────────────────────
def generate_resume_profile_analysis(resume_text: str) -> dict:
    prompt = f"""
Analyze this resume and generate a professional profile summary.

Resume:
{resume_text}
"""

    fallback = {
        "summary": "Resume uploaded successfully.",
        "strengths": [],
        "weaknesses": [],
        "suggested_roles": [],
    }

    # Simple schema for profile analysis
    profile_schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "weaknesses": {"type": "array", "items": {"type": "string"}},
            "suggested_roles": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary"],
    }

    return _parse_with_retry(prompt, fallback, profile_schema)



# ─────────────────────────────────────────────────────────
# CANDIDATE ANALYSIS
# ─────────────────────────────────────────────────────────
def generate_candidate_analysis(jd_text: str, resume_text: str) -> dict:
    prompt = f"""
You are an expert technical interviewer.

Return ONLY valid JSON matching the schema EXACTLY.

IMPORTANT:
- QA must contain at least 10 interview questions with answers
- concepts_revision must contain at least 5 topics
- swot_analysis must contain all 4 lists
- company_insights must not be empty

Job Description:{jd_text}

Resume:{resume_text}
"""

    fallback = {
        "company": "Unknown",
        "role": "Unknown",
        "swot_analysis": {
            "strengths": [],
            "weaknesses": [],
            "opportunities": [],
            "threats": [],
        },
        "requiredskills": [],
        "concepts_revision": [],
        "QA": [],
        "company_insights": [],
    }

    return _parse_with_retry(prompt, fallback, candidate_schema)


# ─────────────────────────────────────────────────────────
# INTERVIEWER ANALYSIS
# ─────────────────────────────────────────────────────────
def generate_interviewer_analysis(jd_text: str, resume_text: str) -> dict:
    prompt = f"""
You are a senior recruiter generating a hiring screening report.

Return ONLY valid JSON matching schema EXACTLY.

IMPORTANT:
- screening_decision MUST be filled
- decision_reasoning must be detailed
- priority must be high/medium/low
- interviewer_recommendations must include at least 2 roles

Job Description:{jd_text}

Resume:{resume_text}
"""

    fallback = {
        "candidate_info": {
            "name": "Unknown",
            "contact": {"email": ""},
            "years_of_experience": 0,
        },
        "job_requirements": {
            "must_have_skills": [],
            "good_to_have_skills": [],
        },
        "resume_analysis": {
            "education_match": {
                "required_education": "",
                "candidate_education": "",
                "match_score": 0,
                "score_reasoning": "",
            },
            "experience_match": {
                "required_years": 0,
                "candidate_years": 0,
                "relevant_experience_score": 0,
                "score_reasoning": "",
            },
            "skill_gaps": [],
            "keyword_match_score": 0,
            "keyword_match_reasoning": "",
        },
        "preliminary_assessment": {
            "technical_fit_score": 0,
            "technical_fit_reasoning": "",
            "experience_fit_score": 0,
            "experience_fit_reasoning": "",
            "potential_culture_fit": 0,
            "culture_fit_reasoning": "",
        },
        "screening_decision": {
            "decision_reasoning": "",
            "interview_type": "technical",
            "priority": "medium",
            "priority_justification": "",
        },
    }

    return _parse_with_retry(prompt, fallback, interviewer_schema)