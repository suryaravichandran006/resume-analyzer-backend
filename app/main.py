import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
Path("uploaded_resumes").mkdir(exist_ok=True)
from app.database import Base, engine
from app import models  # ⭐ ensures models are registered
from app.routers import auth_router, job_router, candidate_router, admin_router

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Resume Analyzer API",
    version="2.0.0",
    description="Production-grade AI Resume Analysis Platform",
)

# ⭐ Create tables once (dev only)
try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified successfully")
except Exception as e:
    logger.error(f"Database initialization failed: {e}")
    raise


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router.router)
app.include_router(job_router.router)
app.include_router(candidate_router.router, prefix="/candidates", tags=["Candidates"])
app.include_router(admin_router.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "message": "AI Resume Analyzer API running"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}

import subprocess
import threading

def start_celery():
    subprocess.Popen(
        ["celery", "-A", "app.celery_worker", "worker", "--loglevel=info", "--pool=solo"]
    )

threading.Thread(target=start_celery, daemon=True).start()