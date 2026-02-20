import pdfplumber
import io
from fastapi import UploadFile

async def extract_text_from_pdf(file: UploadFile) -> str:
    contents = await file.read()

    with pdfplumber.open(io.BytesIO(contents)) as pdf:
        text = ""
        for page in pdf.pages:
            text += page.extract_text() or ""

    return text
