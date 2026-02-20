from app.celery_worker import celery
from app.database import SessionLocal
from app.models import ExternalCandidate, JobDescription, JobApplication, Resume
from app.services.gemini_service import generate_candidate_analysis, generate_interviewer_analysis
from app.services.scoring_service import calculate_final_score


# ─────────────────────────────────────────────
# External Candidate Processing
# ─────────────────────────────────────────────

@celery.task(name="app.tasks.process_external_resume")
def process_external_resume(candidate_id: int):
    db = SessionLocal()

    try:
        candidate = db.query(ExternalCandidate).filter(
            ExternalCandidate.id == candidate_id
        ).first()

        if not candidate:
            return

        job = db.query(JobDescription).filter(
            JobDescription.id == candidate.job_id
        ).first()

        if not job or not candidate.raw_resume_text:
            return

        # ⭐ AI pipeline
        candidate_analysis = generate_candidate_analysis(
            job.raw_text,
            candidate.raw_resume_text
        )

        interviewer_analysis = generate_interviewer_analysis(
            job.raw_text,
            candidate.raw_resume_text
        )

        final_score = calculate_final_score(interviewer_analysis)

        # ⭐ Update DB
        candidate.analysis_candidate = candidate_analysis
        candidate.analysis_interviewer = interviewer_analysis
        candidate.final_score = final_score
        candidate.status = "analyzed"

        db.commit()

    except Exception as e:
        db.rollback()
        raise e

    finally:
        db.close()


# ─────────────────────────────────────────────
# Internal Candidate Processing
# ─────────────────────────────────────────────

@celery.task(name="app.tasks.process_internal_resume")
def process_internal_resume(job_id: int, user_id: int):
    db = SessionLocal()

    try:
        app = db.query(JobApplication).filter(
            JobApplication.job_id == job_id,
            JobApplication.user_id == user_id,
        ).first()

        if not app:
            return

        job = db.query(JobDescription).filter(JobDescription.id == job_id).first()
        resume = db.query(Resume).filter(Resume.user_id == user_id).first()

        if not job or not resume or not resume.parsed_text:
            return

        # ⭐ AI pipeline
        candidate_analysis = generate_candidate_analysis(
            job.raw_text,
            resume.parsed_text
        )

        interviewer_analysis = generate_interviewer_analysis(
            job.raw_text,
            resume.parsed_text
        )

        final_score = calculate_final_score(interviewer_analysis)

        # ⭐ Update DB
        app.analysis_candidate = candidate_analysis
        app.analysis_interviewer = interviewer_analysis
        app.final_score = final_score
        app.status = "analyzed"

        db.commit()

    except Exception as e:
        db.rollback()
        raise e

    finally:
        db.close()
