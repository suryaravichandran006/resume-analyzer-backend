import pdfplumber
import io
import logging
import os
from fastapi import UploadFile
from typing import Union

logger = logging.getLogger(__name__)


async def extract_text_from_pdf(source: Union[UploadFile, str]) -> str:
    """
    Extract text from either:
    - UploadFile (during upload)
    - file path (for stored CV reuse / Celery)
    """
    try:
        text_parts = []

        # ── CASE 1: UploadFile (existing behaviour)
        if isinstance(source, UploadFile):
            contents = await source.read()
            if not contents:
                return ""

            with pdfplumber.open(io.BytesIO(contents)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

        # ── CASE 2: file path (new behaviour)
        elif isinstance(source, str) and os.path.exists(source):
            with pdfplumber.open(source) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

        else:
            logger.error(f"Invalid PDF source provided: {source}")
            return ""

        return "\n".join(text_parts)

    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return ""
