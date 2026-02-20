import logging
import os
from uuid import uuid4
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.orm import Session

from ..dependencies import get_db, get_current_candidate, get_current_user
from ..models import Resume, JobApplication, JobDescription, Notification, User, ExternalCandidate
from ..services.pdf_service import extract_text_from_pdf
from ..services.gemini_service import generate_resume_profile_analysis
from ..services.notification_service import send_notification

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Candidates"])


# ── Resume Upload ─────────────────────────────────────────────────────────────

@router.post("/profile/upload-resume")
async def upload_profile_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    """Upload and analyze a profile resume. Overwrites previous upload."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Save file to disk
    UPLOAD_DIR = "uploaded_resumes"
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    unique_name = f"{uuid4()}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    resume_text = await extract_text_from_pdf(file_path)
    if not resume_text or not resume_text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from PDF. Ensure the PDF is not scanned/image-based.")

    # AI analysis with graceful fallback
    try:
        analysis = generate_resume_profile_analysis(resume_text)
    except Exception as e:
        logger.error(f"Resume profile analysis failed for user {user.id}: {e}")
        analysis = {
            "summary": "Resume uploaded. AI analysis temporarily unavailable.",
            "strengths": [],
            "weaknesses": [],
            "suggested_roles": [],
        }

    summary = analysis.get("summary", "")
    strengths = analysis.get("strengths", [])
    weaknesses = analysis.get("weaknesses", [])
    suggested_roles = analysis.get("suggested_roles", [])

    # Ensure lists (Gemini may return comma-separated strings)
    if isinstance(strengths, str):
        strengths = [s.strip() for s in strengths.split(",") if s.strip()]
    if isinstance(weaknesses, str):
        weaknesses = [w.strip() for w in weaknesses.split(",") if w.strip()]
    if isinstance(suggested_roles, str):
        suggested_roles = [r.strip() for r in suggested_roles.split(",") if r.strip()]

    # Upsert resume record
    existing = db.query(Resume).filter(Resume.user_id == user.id).first()
    if existing:
        existing.file_path = file_path
        existing.parsed_text = resume_text
        existing.summary = summary
        existing.strengths = strengths
        existing.weaknesses = weaknesses
        existing.suggested_roles = suggested_roles
    else:
        db.add(Resume(
            user_id=user.id,
            file_path=file_path,
            parsed_text=resume_text,
            summary=summary,
            strengths=strengths,
            weaknesses=weaknesses,
            suggested_roles=suggested_roles,
        ))

    db.commit()
    return {
        "message": "Resume uploaded and analyzed successfully",
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "suggested_roles": suggested_roles,
    }


# ── Profile Status ────────────────────────────────────────────────────────────

@router.get("/profile/status")
def get_resume_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    resume = db.query(Resume).filter(Resume.user_id == user.id).first()
    return {"has_resume": resume is not None}


# ── Profile Analysis ──────────────────────────────────────────────────────────

@router.get("/profile/analysis")
def get_resume_analysis(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    resume = db.query(Resume).filter(Resume.user_id == user.id).first()

    if not resume:
        return {
            "summary": "",
            "strengths": [],
            "weaknesses": [],
            "suggested_roles": [],
        }

    return {
        "summary": resume.summary or "",
        "strengths": resume.strengths or [],
        "weaknesses": resume.weaknesses or [],
        "suggested_roles": resume.suggested_roles or [],
    }


# ── My Applications ───────────────────────────────────────────────────────────

@router.get("/my-applications")
def get_my_applications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    applications = (
        db.query(JobApplication)
        .filter(JobApplication.user_id == user.id)
        .order_by(JobApplication.created_at.desc())
        .all()
    )

    results = []
    for app in applications:
        job = db.query(JobDescription).filter(JobDescription.id == app.job_id).first()
        if not job:
            continue

        results.append({
            "id": app.id,
            "job_id": job.id,
            "job_title": job.title,
            "company": job.company,
            "status": app.status,
            "final_score": app.final_score,
            "analysis_candidate": app.analysis_candidate,
            "analysis_interviewer": app.analysis_interviewer,
            "created_at": app.created_at.isoformat() if app.created_at else None,
        })

    return results


# ── Application Detail ────────────────────────────────────────────────────────

@router.get("/application/{job_id}")
def get_application_detail(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    app = db.query(JobApplication).filter(
        JobApplication.job_id == job_id,
        JobApplication.user_id == user.id,
    ).first()

    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()

    return {
        "id": app.id,
        "job_id": job_id,
        "job_title": job.title if job else "Unknown",
        "company": job.company if job else "Unknown",
        "status": app.status,
        "final_score": app.final_score,
        "analysis_candidate": app.analysis_candidate,
        "analysis_interviewer": app.analysis_interviewer,
        "created_at": app.created_at.isoformat() if app.created_at else None,
    }


# ── Dashboard Stats ───────────────────────────────────────────────────────────

@router.get("/dashboard")
def get_dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_candidate),
):
    """Aggregated dashboard data for candidate."""
    apps = db.query(JobApplication).filter(JobApplication.user_id == user.id).all()
    resume = db.query(Resume).filter(Resume.user_id == user.id).first()

    total = len(apps)
    requested = sum(1 for a in apps if a.status == "requested")
    approved = sum(1 for a in apps if a.status == "approved")
    best_score = max((a.final_score or 0.0) for a in apps) if apps else None

    return {
        "has_resume": resume is not None,
        "total_applications": total,
        "requested": requested,
        "approved": approved,
        "best_score": round(best_score, 2) if best_score is not None else None,
    }


# ── Notifications ─────────────────────────────────────────────────────────────

@router.get("/notifications/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # allow both roles
):
    count = db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False,  # noqa: E712
    ).count()
    return {"unread_count": count}


@router.get("/notifications")
def get_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # allow both roles
):
    notifications = (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )

    return [
        {
            "id": n.id,
            "message": n.message,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifications
    ]


@router.put("/notifications/{notification_id}/mark-read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user.id,
    ).first()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.is_read = True
    db.commit()
    return {"message": "Notification marked as read"}


@router.put("/notifications/mark-all-read")
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return {"message": "All notifications marked as read"}

# ── DELETE INTERNAL CANDIDATE ─────────────────────────

@router.delete("/internal/{candidate_id}")
def delete_internal_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
):
    app = db.query(JobApplication).filter(JobApplication.id == candidate_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Candidate not found")

    db.delete(app)
    db.commit()
    return {"message": "Internal candidate deleted"}


# ── DELETE EXTERNAL CANDIDATE ─────────────────────────

@router.delete("/external/{candidate_id}")
def delete_external_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
):
    ext = db.query(ExternalCandidate).filter(ExternalCandidate.id == candidate_id).first()
    if not ext:
        raise HTTPException(status_code=404, detail="Candidate not found")

    db.delete(ext)
    db.commit()
    return {"message": "External candidate deleted"}