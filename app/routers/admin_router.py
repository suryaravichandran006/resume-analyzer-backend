import csv
import os
import io
import logging
from typing import Optional, List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File as FastAPIFile
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..models import Resume, ExternalCandidate, JobApplication, JobDescription, User
from ..dependencies import get_db, get_current_admin

logger = logging.getLogger(__name__)

VAULT_DIR = "uploaded_resumes"

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Analytics Dashboard ───────────────────────────────────────────────────────

@router.get("/dashboard")
def admin_dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    total_jobs = db.query(JobDescription).filter(JobDescription.admin_id == admin.id).count()
    total_applications = db.query(JobApplication).join(
        JobDescription, JobApplication.job_id == JobDescription.id
    ).filter(JobDescription.admin_id == admin.id).count()
    pending_requests = db.query(JobApplication).join(
        JobDescription, JobApplication.job_id == JobDescription.id
    ).filter(
        JobDescription.admin_id == admin.id,
        JobApplication.status == "requested",
    ).count()
    total_external = db.query(ExternalCandidate).join(
        JobDescription, ExternalCandidate.job_id == JobDescription.id
    ).filter(JobDescription.admin_id == admin.id).count()

    return {
        "total_jobs": total_jobs,
        "total_applications": total_applications,
        "pending_requests": pending_requests,
        "total_external_candidates": total_external,
    }


# ── Candidate Ranking ─────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/rankings")
def get_candidate_rankings(
    job_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    internal = (
        db.query(JobApplication)
        .filter(JobApplication.job_id == job_id, JobApplication.final_score.isnot(None))
        .order_by(desc(JobApplication.final_score))
        .all()
    )

    results = []
    for app in internal:
        candidate = db.query(User).filter(User.id == app.user_id).first()
        results.append({
            "rank": 0,
            "source": "internal",
            "name": candidate.name if candidate else "Unknown",
            "email": candidate.email if candidate else "",
            "score": app.final_score,
            "score_pct": round((app.final_score or 0) * 100, 1),
            "status": app.status,
        })

    external = (
        db.query(ExternalCandidate)
        .filter(ExternalCandidate.job_id == job_id, ExternalCandidate.final_score.isnot(None))
        .order_by(desc(ExternalCandidate.final_score))
        .all()
    )

    for ext in external:
        results.append({
            "rank": 0,
            "source": "external",
            "name": ext.name,
            "email": ext.email or "",
            "score": ext.final_score,
            "score_pct": round((ext.final_score or 0) * 100, 1),
            "status": ext.status,
        })

    results.sort(key=lambda x: x["score"] or 0, reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    return {"job_id": job_id, "job_title": job.title, "rankings": results}


# ── Export External Candidates ────────────────────────────────────────────────

@router.get("/export/external/{job_id}")
def export_external_candidates(
    job_id: int,
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    query = db.query(ExternalCandidate).filter(ExternalCandidate.job_id == job_id)
    if status:
        query = query.filter(ExternalCandidate.status == status)

    candidates = query.order_by(desc(ExternalCandidate.final_score)).all()

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Name", "Email", "Final Score (%)", "Status", "Strengths", "Weaknesses"])
        yield buffer.getvalue()
        buffer.seek(0); buffer.truncate(0)

        for c in candidates:
            analysis = c.analysis_candidate or {}
            swot = analysis.get("swot_analysis", {})
            strengths = "; ".join(swot.get("strengths", []))
            weaknesses = "; ".join(swot.get("weaknesses", []))
            score_pct = round((c.final_score or 0) * 100, 1)
            writer.writerow([c.name or "", c.email or "", score_pct, c.status or "", strengths, weaknesses])
            yield buffer.getvalue()
            buffer.seek(0); buffer.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=job_{job_id}_external.csv"},
    )


# ── Export Internal Candidates ────────────────────────────────────────────────

@router.get("/export/internal/{job_id}")
def export_internal_candidates(
    job_id: int,
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    query = db.query(JobApplication).filter(JobApplication.job_id == job_id)
    if status:
        query = query.filter(JobApplication.status == status)

    applications = query.order_by(desc(JobApplication.final_score)).all()

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Candidate Name", "Email", "Final Score (%)", "Status"])
        yield buffer.getvalue()
        buffer.seek(0); buffer.truncate(0)

        for app in applications:
            candidate = db.query(User).filter(User.id == app.user_id).first()
            score_pct = round((app.final_score or 0) * 100, 1)
            writer.writerow([
                candidate.name if candidate else "",
                candidate.email if candidate else "",
                score_pct,
                app.status or "",
            ])
            yield buffer.getvalue()
            buffer.seek(0); buffer.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=job_{job_id}_internal.csv"},
    )


# ── Download candidate resume (admin) ─────────────────────────────────────────

@router.get("/resume/{user_id}")
def download_resume(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    resume = db.query(Resume).filter(Resume.user_id == user_id).first()

    if not resume or not resume.file_path or not os.path.exists(resume.file_path):
        raise HTTPException(status_code=404, detail="Resume not found")

    return FileResponse(
        resume.file_path,
        media_type="application/pdf",
        filename=os.path.basename(resume.file_path),
    )


@router.post("/jobs/{job_id}/attach-candidates")
def attach_candidates(
    job_id: int,
    user_ids: list[int],
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    for uid in user_ids:
        exists = db.query(JobApplication).filter(
            JobApplication.job_id == job_id,
            JobApplication.user_id == uid,
        ).first()
        if not exists:
            db.add(JobApplication(job_id=job_id, user_id=uid, status="approved"))
    db.commit()
    return {"message": "Candidates attached"}


# ── Vault: Upload CV files to server ──────────────────────────────────────────

@router.post("/vault/upload")
async def vault_upload_files(
    files: List[UploadFile] = FastAPIFile(...),
    admin: User = Depends(get_current_admin),
):
    """
    Save uploaded CV PDFs to the server's uploaded_resumes/ folder.
    No AI processing at this stage — files are staged for later attachment.
    Returns the server-side path for each saved file.
    """
    os.makedirs(VAULT_DIR, exist_ok=True)

    saved = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            logger.warning(f"Skipping non-PDF vault upload: {file.filename}")
            continue

        content = await file.read()
        if not content:
            logger.warning(f"Empty file skipped: {file.filename}")
            continue

        # Sanitise filename and prefix with UUID to prevent collisions
        safe_name = os.path.basename(file.filename)
        unique_name = f"{uuid4()}_{safe_name}"
        file_path = os.path.join(VAULT_DIR, unique_name)

        with open(file_path, "wb") as f:
            f.write(content)

        saved.append({
            "original_name": file.filename,
            "server_path": file_path,
            "size": len(content),
        })
        logger.info(f"Vault file saved: {file_path} ({len(content)} bytes)")

    return {"uploaded": saved, "count": len(saved)}


# ── Vault: Delete a staged CV file from server ────────────────────────────────

class VaultDeleteRequest(BaseModel):
    server_path: str


@router.delete("/vault/file")
def vault_delete_file(
    payload: VaultDeleteRequest,
    admin: User = Depends(get_current_admin),
):
    """
    Delete a staged vault CV from the server's uploaded_resumes/ folder.
    Only paths within uploaded_resumes/ are permitted (path-traversal guard).
    """
    path = payload.server_path

    # Security: reject any path that escapes the vault directory
    normalised = os.path.normpath(path)
    if not normalised.startswith(os.path.normpath(VAULT_DIR)):
        logger.warning(f"Blocked vault delete for out-of-scope path: {path}")
        raise HTTPException(status_code=400, detail="Invalid file path")

    if os.path.exists(normalised):
        os.remove(normalised)
        logger.info(f"Vault file deleted: {normalised}")

    return {"message": "File deleted", "server_path": path}