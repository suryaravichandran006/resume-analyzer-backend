import io
import os
import logging
from typing import List

import pdfplumber
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..dependencies import get_db, get_current_admin, get_current_candidate
from ..models import JobDescription, JobApplication, Resume, User, Notification, ExternalCandidate
from ..services.gemini_service import (
    generate_jd_summary,
    generate_candidate_analysis,
    generate_interviewer_analysis,
)
from ..services.scoring_service import calculate_final_score
from ..services.notification_service import send_notification
from app.tasks import process_internal_resume

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["Jobs"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AttachRequest(BaseModel):
    user_ids: List[int]


class VaultAttachRequest(BaseModel):
    """Server paths returned by POST /admin/vault/upload."""
    server_paths: List[str]


# ── Shared PDF helper (UploadFile only — used by upload-cvs) ─────────────────

async def _extract_text_from_upload(file: UploadFile) -> str:
    """Extract text from an in-memory UploadFile."""
    try:
        await file.seek(0)
        contents = await file.read()
        if not contents:
            return ""
        text_parts = []
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
        return "\n".join(text_parts).strip()
    except Exception as e:
        logger.error(f"PDF extraction error (upload): {file.filename} | {e}")
        return ""


def _extract_text_from_path(file_path: str) -> str:
    """Extract text from a PDF already saved on disk."""
    try:
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
        return "\n".join(text_parts).strip()
    except Exception as e:
        logger.error(f"PDF extraction error (path): {file_path} | {e}")
        return ""


# ── Public: list jobs for candidates ──────────────────────────────────────────

@router.get("/public")
def get_public_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    """Return all jobs with the current candidate's application status."""
    jobs = db.query(JobDescription).order_by(JobDescription.id.desc()).all()

    results = []
    for job in jobs:
        existing_app = db.query(JobApplication).filter(
            JobApplication.job_id == job.id,
            JobApplication.user_id == user.id,
        ).first()
        results.append({
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "application_status": existing_app.status if existing_app else None,
        })

    return results


# ── Admin: list all jobs ───────────────────────────────────────────────────────

@router.get("/")
def get_all_jobs(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    jobs = db.query(JobDescription).order_by(JobDescription.id.desc()).all()
    return [
        {
            "id": j.id,
            "title": j.title,
            "company": j.company,
            "created_by_admin_id": j.admin_id,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]


# ── Upload JD (admin) ─────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_jd(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    try:
        contents = await file.read()
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            jd_text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read PDF: {e}")

    if not jd_text:
        raise HTTPException(status_code=400, detail="PDF contains no readable text")

    try:
        parsed_summary = generate_jd_summary(jd_text)
    except Exception as e:
        logger.error(f"JD summary generation failed: {e}")
        parsed_summary = {}

    title = (
        parsed_summary.get("job_title")
        or parsed_summary.get("job_metadata", {}).get("job_name", "Untitled Job")
    )
    company = (
        parsed_summary.get("company_name")
        or parsed_summary.get("job_metadata", {}).get("company_name", "Unknown Company")
    )

    job = JobDescription(
        admin_id=admin.id,
        title=title or "Untitled Job",
        company=company or "Unknown Company",
        raw_text=jd_text,
        parsed_summary=parsed_summary,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return {
        "message": "JD uploaded and parsed successfully",
        "job_id": job.id,
        "title": job.title,
        "company": job.company,
    }


# ── Request job (candidate) ───────────────────────────────────────────────────

@router.post("/{job_id}/request")
def request_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    resume = db.query(Resume).filter(Resume.user_id == user.id).first()
    if not resume:
        raise HTTPException(status_code=400, detail="Please upload your resume before applying")

    existing = db.query(JobApplication).filter(
        JobApplication.job_id == job_id,
        JobApplication.user_id == user.id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"You already have an application with status: {existing.status}")

    application = JobApplication(job_id=job_id, user_id=user.id, status="requested")
    db.add(application)
    db.commit()
    db.refresh(application)

    return {
        "message": "Application request submitted successfully",
        "application_id": application.id,
        "status": "requested",
    }


# ── Get pending requests (admin) ──────────────────────────────────────────────

@router.get("/{job_id}/requests")
def get_pending_requests(
    job_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    reqs = (
        db.query(JobApplication)
        .filter(JobApplication.job_id == job_id, JobApplication.status == "requested")
        .all()
    )

    results = []
    for req in reqs:
        candidate = db.query(User).filter(User.id == req.user_id).first()
        if not candidate:
            continue
        results.append({
            "application_id": req.id,
            "candidate_id": candidate.id,
            "candidate_name": candidate.name,
            "email": candidate.email,
            "requested_at": req.created_at.isoformat() if req.created_at else None,
        })

    return {"job_id": job_id, "job_title": job.title, "pending_requests": results}


# ── Approve request + auto AI scoring (admin) ─────────────────────────────────

@router.post("/{job_id}/approve/{user_id}")
def approve_request(
    job_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    application = db.query(JobApplication).filter(
        JobApplication.job_id == job_id,
        JobApplication.user_id == user_id,
        JobApplication.status == "requested",
    ).first()

    if not application:
        raise HTTPException(status_code=404, detail="Pending request not found")

    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    resume = db.query(Resume).filter(Resume.user_id == user_id).first()
    if not resume or not resume.parsed_text:
        raise HTTPException(status_code=400, detail="Candidate has no resume text available")

    try:
        candidate_analysis = generate_candidate_analysis(job.raw_text, resume.parsed_text)
        interviewer_analysis = generate_interviewer_analysis(job.raw_text, resume.parsed_text)
        final_score = calculate_final_score(interviewer_analysis)
    except Exception as e:
        logger.error(f"AI analysis failed for user {user_id} / job {job_id}: {e}")
        candidate_analysis = {}
        interviewer_analysis = {}
        final_score = 0.5

    application.status = "approved"
    application.final_score = final_score
    application.analysis_candidate = candidate_analysis
    application.analysis_interviewer = interviewer_analysis

    send_notification(
        db,
        user_id=user_id,
        message=f"Congratulations! You have been approved for '{job.title}'. Your score: {round(final_score * 100, 1)}%",
    )

    db.commit()

    return {
        "message": "Candidate approved and AI analysis complete",
        "final_score": final_score,
        "application_id": application.id,
    }


# ── Reject request (admin) ────────────────────────────────────────────────────

@router.post("/{job_id}/reject/{user_id}")
def reject_request(
    job_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    application = db.query(JobApplication).filter(
        JobApplication.job_id == job_id,
        JobApplication.user_id == user_id,
        JobApplication.status == "requested",
    ).first()

    if not application:
        raise HTTPException(status_code=404, detail="Pending request not found")

    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    application.status = "rejected"

    send_notification(
        db,
        user_id=user_id,
        message=f"Your application for '{job.title if job else 'this position'}' was not approved at this time.",
    )

    db.commit()
    return {"message": "Application rejected"}


# ── Get job candidates with scores (admin) ────────────────────────────────────

@router.get("/{job_id}/candidates")
def get_job_candidates(
    job_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    applications = (
        db.query(JobApplication)
        .filter(JobApplication.job_id == job_id)
        .order_by(JobApplication.final_score.desc().nullslast())
        .all()
    )

    results = []
    for app in applications:
        candidate = db.query(User).filter(User.id == app.user_id).first()
        analysis_c = app.analysis_candidate or {}
        results.append({
            "id": app.id,
            "source": "internal",
            "status": app.status,
            "final_score": app.final_score,
            "strengths": analysis_c.get("swot_analysis", {}).get("strengths", []),
            "weaknesses": analysis_c.get("swot_analysis", {}).get("weaknesses", []),
            "candidate_analysis": analysis_c,
            "interviewer_analysis": app.analysis_interviewer or {},
            "user": {
                "id": candidate.id if candidate else None,
                "username": candidate.name if candidate else "Unknown",
                "email": candidate.email if candidate else "",
            },
        })

    external = (
        db.query(ExternalCandidate)
        .filter(ExternalCandidate.job_id == job_id)
        .order_by(ExternalCandidate.final_score.desc().nullslast())
        .all()
    )

    for ext in external:
        analysis_c = ext.analysis_candidate or {}
        results.append({
            "id": ext.id,
            "source": "external",
            "status": ext.status,
            "final_score": ext.final_score,
            "strengths": analysis_c.get("swot_analysis", {}).get("strengths", []),
            "weaknesses": analysis_c.get("swot_analysis", {}).get("weaknesses", []),
            "candidate_analysis": analysis_c,
            "interviewer_analysis": ext.analysis_interviewer or {},
            "user": {
                "id": None,
                "username": ext.name,
                "email": ext.email or "",
            },
        })

    results.sort(key=lambda x: (x["final_score"] or 0), reverse=True)

    return {"job_id": job_id, "job_title": job.title, "candidates": results}


# ── Add candidate directly (admin) ────────────────────────────────────────────

@router.post("/{job_id}/add-candidate/{user_id}")
def add_candidate_to_job(
    job_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    candidate = db.query(User).filter(User.id == user_id, User.role == "candidate").first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    resume = db.query(Resume).filter(Resume.user_id == user_id).first()
    if not resume or not resume.parsed_text:
        raise HTTPException(status_code=400, detail="Candidate has no resume uploaded")

    existing = db.query(JobApplication).filter(
        JobApplication.job_id == job_id,
        JobApplication.user_id == user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Candidate already has application (status: {existing.status})")

    try:
        candidate_analysis = generate_candidate_analysis(job.raw_text, resume.parsed_text)
        interviewer_analysis = generate_interviewer_analysis(job.raw_text, resume.parsed_text)
        final_score = calculate_final_score(interviewer_analysis)
    except Exception as e:
        logger.error(f"AI analysis failed: {e}")
        candidate_analysis, interviewer_analysis, final_score = {}, {}, 0.5

    application = JobApplication(
        job_id=job_id,
        user_id=user_id,
        status="approved",
        final_score=final_score,
        analysis_candidate=candidate_analysis,
        analysis_interviewer=interviewer_analysis,
    )
    db.add(application)

    send_notification(
        db,
        user_id=user_id,
        message=f"You have been added to '{job.title}'. Score: {round(final_score * 100, 1)}%",
    )

    db.commit()
    return {"message": "Candidate added and analyzed", "final_score": final_score}


# ── Upload external CVs in bulk — raw multipart (admin) ──────────────────────

@router.post("/{job_id}/upload-cvs")
async def upload_multiple_cvs(
    job_id: int,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    results = []

    for file in files:
        try:
            resume_text = await _extract_text_from_upload(file)
            if not resume_text.strip():
                logger.warning(f"Empty text from CV: {file.filename}")
                continue

            candidate_analysis = generate_candidate_analysis(job.raw_text, resume_text)
            interviewer_analysis = generate_interviewer_analysis(job.raw_text, resume_text)
            final_score = calculate_final_score(interviewer_analysis)

            ext = ExternalCandidate(
                job_id=job_id,
                name=file.filename.replace(".pdf", "").replace("_", " "),
                email=None,
                raw_resume_text=resume_text,
                analysis_candidate=candidate_analysis,
                analysis_interviewer=interviewer_analysis,
                final_score=final_score,
                status="analyzed",
            )
            db.add(ext)
            db.commit()
            db.refresh(ext)
            results.append({"candidate_name": ext.name, "score": final_score})

        except Exception as e:
            logger.error(f"Failed to process CV '{file.filename}': {e}")
            results.append({"candidate_name": file.filename, "score": None, "error": str(e)})

    results.sort(key=lambda x: (x.get("score") or 0), reverse=True)

    valid = [r for r in results if r.get("score") is not None]
    shortlist_count = max(1, int(len(valid) * 0.2)) if valid else 0
    top_names = {r["candidate_name"] for r in valid[:shortlist_count]}

    if top_names:
        db.query(ExternalCandidate).filter(
            ExternalCandidate.job_id == job_id,
            ExternalCandidate.name.in_(top_names),
        ).update({"status": "shortlisted"}, synchronize_session="fetch")
        db.commit()

    return {
        "job_id": job_id,
        "total_processed": len(valid),
        "shortlisted_count": shortlist_count,
        "ranked_candidates": results,
    }


# ── Attach vault CVs by server path (admin) ───────────────────────────────────

VAULT_DIR = "uploaded_resumes"


@router.post("/{job_id}/attach-vault-files")
def attach_vault_files(
    job_id: int,
    payload: VaultAttachRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Process CVs that are already saved on the server (via POST /admin/vault/upload).
    Reads each file from disk, runs the AI pipeline, and creates ExternalCandidate records.
    Mirrors the behaviour of upload-cvs but takes server paths instead of multipart files.
    """
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    results = []

    for server_path in payload.server_paths:
        # ── Path-traversal guard ──────────────────────────────────────────────
        normalised = os.path.normpath(server_path)
        if not normalised.startswith(os.path.normpath(VAULT_DIR)):
            logger.warning(f"Blocked out-of-scope vault path: {server_path}")
            continue

        if not os.path.exists(normalised):
            logger.warning(f"Vault file not found on disk: {normalised}")
            results.append({"candidate_name": server_path, "score": None, "error": "File not found"})
            continue

        try:
            resume_text = _extract_text_from_path(normalised)
            if not resume_text.strip():
                logger.warning(f"No extractable text in vault file: {normalised}")
                continue

            candidate_analysis = generate_candidate_analysis(job.raw_text, resume_text)
            interviewer_analysis = generate_interviewer_analysis(job.raw_text, resume_text)
            final_score = calculate_final_score(interviewer_analysis)

            # Derive a human-readable display name from the filename:
            # stored as "{uuid}_{original_filename}.pdf"
            basename = os.path.basename(normalised)
            parts = basename.split("_", 1)
            display_name = (
                parts[1].removesuffix(".pdf").replace("_", " ")
                if len(parts) > 1
                else basename.removesuffix(".pdf")
            )

            ext = ExternalCandidate(
                job_id=job_id,
                name=display_name,
                email=None,
                raw_resume_text=resume_text,
                analysis_candidate=candidate_analysis,
                analysis_interviewer=interviewer_analysis,
                final_score=final_score,
                status="analyzed",
            )
            db.add(ext)
            db.commit()
            db.refresh(ext)
            results.append({"candidate_name": ext.name, "score": final_score})

        except Exception as e:
            logger.error(f"Failed to process vault file '{normalised}': {e}")
            results.append({"candidate_name": normalised, "score": None, "error": str(e)})

    results.sort(key=lambda x: (x.get("score") or 0), reverse=True)

    valid = [r for r in results if r.get("score") is not None]
    shortlist_count = max(1, int(len(valid) * 0.2)) if valid else 0
    top_names = {r["candidate_name"] for r in valid[:shortlist_count]}

    if top_names:
        db.query(ExternalCandidate).filter(
            ExternalCandidate.job_id == job_id,
            ExternalCandidate.name.in_(top_names),
        ).update({"status": "shortlisted"}, synchronize_session="fetch")
        db.commit()

    return {
        "job_id": job_id,
        "total_processed": len(valid),
        "shortlisted_count": shortlist_count,
        "ranked_candidates": results,
    }


# ── Delete job ────────────────────────────────────────────────────────────────

@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    db.delete(job)
    db.commit()
    return {"message": "Job deleted"}


# ── Attach internal candidates by user IDs ────────────────────────────────────

@router.post("/{job_id}/attach-internal")
def attach_internal(
    job_id: int,
    payload: AttachRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    queued_users = []

    for uid in payload.user_ids:
        resume = db.query(Resume).filter(Resume.user_id == uid).first()
        if not resume or not resume.parsed_text:
            continue

        exists = db.query(JobApplication).filter(
            JobApplication.job_id == job_id,
            JobApplication.user_id == uid,
        ).first()

        if exists:
            continue

        app = JobApplication(job_id=job_id, user_id=uid, status="processing")
        db.add(app)
        queued_users.append(uid)

    db.commit()

    for uid in queued_users:
        process_internal_resume.delay(job_id, uid)

    return {"message": "Candidates queued for analysis", "queued_users": queued_users}