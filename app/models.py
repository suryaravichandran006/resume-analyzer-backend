from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Text,
    Float, Boolean, JSON
)
from sqlalchemy.orm import relationship
from datetime import datetime

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=True)  # nullable for Google OAuth users
    role = Column(String, nullable=False)  # "admin" or "candidate"
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    resume = relationship("Resume", back_populates="user", uselist=False, cascade="all, delete-orphan")
    applications = relationship("JobApplication", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")


class JobDescription(Base):
    __tablename__ = "job_descriptions"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False, default="Untitled Job")
    company = Column(String, nullable=False, default="Unknown Company")
    raw_text = Column(Text, nullable=False)
    parsed_summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    applications = relationship("JobApplication", back_populates="job", cascade="all, delete-orphan")
    external_candidates = relationship("ExternalCandidate", back_populates="job", cascade="all, delete-orphan")
    admin = relationship("User", foreign_keys=[admin_id])


class JobApplication(Base):
    __tablename__ = "job_applications"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("job_descriptions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # status lifecycle: "requested" -> "approved" -> "analyzed" | "rejected"
    status = Column(String, nullable=False, default="requested")
    final_score = Column(Float, nullable=True)
    analysis_candidate = Column(JSON, nullable=True)
    analysis_interviewer = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    job = relationship("JobDescription", back_populates="applications")
    user = relationship("User", back_populates="applications")


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    file_path = Column(String, nullable=True)   # ⭐ ADD THIS

    parsed_text = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    strengths = Column(JSON, nullable=True)
    weaknesses = Column(JSON, nullable=True)
    suggested_roles = Column(JSON, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="resume")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(String, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="notifications")


class ExternalCandidate(Base):
    __tablename__ = "external_candidates"

    id = Column(Integer, primary_key=True, index=True)

    # ⭐ THIS is what SQLAlchemy is missing in DB
    job_id = Column(Integer, ForeignKey("job_descriptions.id"), nullable=False)

    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    raw_resume_text = Column(Text, nullable=True)

    analysis_candidate = Column(JSON, nullable=True)
    analysis_interviewer = Column(JSON, nullable=True)
    final_score = Column(Float, nullable=True)
    status = Column(String, default="queued", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    # ⭐ Relationship
    job = relationship("JobDescription", back_populates="external_candidates")