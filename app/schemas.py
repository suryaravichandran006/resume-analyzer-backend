from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterSchema(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str  # "admin" or "candidate"


class LoginSchema(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str

    class Config:
        from_attributes = True


# ── Resume ────────────────────────────────────────────────────────────────────

class ResumeAnalysisResponse(BaseModel):
    summary: str
    strengths: List[str]
    weaknesses: List[str]
    suggested_roles: List[str]

    class Config:
        from_attributes = True


# ── Job ───────────────────────────────────────────────────────────────────────

class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    created_by_admin_id: Optional[int] = None

    class Config:
        from_attributes = True


# ── Application ───────────────────────────────────────────────────────────────

class ApplicationResponse(BaseModel):
    id: int
    job_id: int
    job_title: str
    company: str
    status: str
    final_score: Optional[float] = None
    analysis_candidate: Optional[Dict[str, Any]] = None
    analysis_interviewer: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── Notifications ─────────────────────────────────────────────────────────────

class NotificationResponse(BaseModel):
    id: int
    message: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UnreadCountResponse(BaseModel):
    unread_count: int


# ── External Candidate ────────────────────────────────────────────────────────

class ExternalCandidateResponse(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    job_id: int
    final_score: Optional[float] = None
    status: str
    analysis_candidate: Optional[Dict[str, Any]] = None
    analysis_interviewer: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class ExternalCandidateDetailResponse(BaseModel):
    id: int
    name: str
    job_id: int
    final_score: Optional[float] = None
    status: str
    analysis_candidate: Optional[Dict[str, Any]] = None
    analysis_interviewer: Optional[Dict[str, Any]] = None
    raw_resume_text: Optional[str] = None

    class Config:
        from_attributes = True