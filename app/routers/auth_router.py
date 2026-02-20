import os
import logging
from dotenv import load_dotenv

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from ..database import SessionLocal
from ..models import User
from ..schemas import RegisterSchema, TokenResponse, UserResponse
from ..auth import hash_password, verify_password, create_access_token
from ..dependencies import get_db, get_current_user

load_dotenv()
logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", status_code=201)
def register(user_data: RegisterSchema, db: Session = Depends(get_db)):
    if user_data.role not in ("admin", "candidate"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'candidate'")

    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        name=user_data.name,
        email=user_data.email,
        password=hash_password(user_data.password),
        role=user_data.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {"message": "User registered successfully", "id": user.id}


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Standard email/password login via OAuth2PasswordRequestForm (email = username field)."""
    user = db.query(User).filter(User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token({"user_id": str(user.id)})
    return {"access_token": token, "token_type": "bearer", "role": user.role}


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    return user


@router.post("/google", response_model=TokenResponse)
def google_login(data: dict, db: Session = Depends(get_db)):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    google_token = data.get("token")
    if not google_token:
        raise HTTPException(status_code=400, detail="Missing token")

    try:
        idinfo = id_token.verify_oauth2_token(
            google_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except Exception as e:
        logger.warning(f"Google token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid Google token")

    email = idinfo.get("email")
    name = idinfo.get("name", email)

    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email")

    # 🔥 IMPORTANT: admin whitelist
    ADMIN_EMAILS = os.getenv("ADMIN_EMAILS", "")
    ADMIN_EMAILS = [e.strip() for e in ADMIN_EMAILS.split(",") if e.strip()]

    user = db.query(User).filter(User.email == email).first()

    if not user:
        role = "admin" if email in ADMIN_EMAILS else "candidate"

        user = User(
            name=name,
            email=email,
            password=None,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # 🚨 Never overwrite role if user exists
    access_token = create_access_token({"user_id": str(user.id)})

    logger.info(f"Google login success: {email} | role={user.role}")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
    }
