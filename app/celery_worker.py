from celery import Celery
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery = Celery(
    "resume_analyzer",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery.conf.task_routes = {
    "app.tasks.process_external_resume": {"queue": "resumes"},
    "app.tasks.process_internal_resume": {"queue": "resumes"},   # ⭐ ADD
}