from __future__ import annotations

import base64
import io
import logging
import os
from dataclasses import dataclass
from typing import Any

import boto3
import fitz
import httpx
from botocore.config import Config
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from openpyxl import load_workbook

WORKBOOK_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(8 * 1024 * 1024)))
R2_TEMPLATE_KEY = os.getenv("R2_TEMPLATE_KEY", "templates/pmi-member-analysis-template.xlsx")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("pmi-analysis")

app = FastAPI(
    title="PMI Member Analysis Processor",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)


@dataclass(frozen=True)
class PdfSummary:
    page_count: int
    text_chars: int


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/pmi/member-analysis")
async def member_analysis(request: Request) -> JSONResponse:
    if request.headers.get("x-action-authenticated") != "true":
        raise HTTPException(status_code=401, detail="unauthorized")

    pdf_bytes = await read_pdf_bytes(request)

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty_pdf")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="pdf_too_large")

    try:
        summary = summarize_pdf(pdf_bytes)
        workbook_bytes = create_workbook(summary)
    finally:
        pdf_bytes = b""

    encoded = base64.b64encode(workbook_bytes).decode("ascii")
    return JSONResponse(
        {
            "openaiFileResponse": [
                {
                    "name": "pmi-member-analysis.xlsx",
                    "mime_type": WORKBOOK_MIME,
                    "content": encoded,
                }
            ]
        },
        headers={"Cache-Control": "no-store"},
    )


async def read_pdf_bytes(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        pdf = form.get("pdf")
        if not isinstance(pdf, UploadFile):
            raise HTTPException(status_code=400, detail="pdf_required")
        if pdf.content_type not in {"application/pdf", "application/octet-stream"}:
            raise HTTPException(status_code=400, detail="pdf_required")
        data = await pdf.read(MAX_PDF_BYTES + 1)
        await pdf.close()
        return data

    if "application/json" not in content_type:
        raise HTTPException(status_code=400, detail="pdf_required")

    payload = await request.json()
    file_ref = select_pdf_file_ref(payload.get("openaiFileIdRefs", []))
    return await download_pdf_ref(file_ref)


def select_pdf_file_ref(file_refs: list[dict[str, Any]]) -> dict[str, Any]:
    for file_ref in file_refs:
        mime_type = str(file_ref.get("mime_type", ""))
        name = str(file_ref.get("name", ""))
        if mime_type == "application/pdf" or name.lower().endswith(".pdf"):
            if not file_ref.get("download_link"):
                break
            return file_ref
    raise HTTPException(status_code=400, detail="pdf_required")


async def download_pdf_ref(file_ref: dict[str, Any]) -> bytes:
    download_link = str(file_ref["download_link"])
    total = 0
    chunks: list[bytes] = []

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            async with client.stream("GET", download_link) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise HTTPException(status_code=413, detail="pdf_too_large")
                    chunks.append(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="pdf_download_failed") from exc

    return b"".join(chunks)


def summarize_pdf(pdf_bytes: bytes) -> PdfSummary:
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            text_chars = 0
            for page in doc:
                text_chars += len(page.get_text("text"))
            return PdfSummary(page_count=doc.page_count, text_chars=text_chars)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="pdf_parse_failed") from exc


def create_workbook(summary: PdfSummary) -> bytes:
    template_bytes = load_template_from_r2()
    workbook = load_workbook(io.BytesIO(template_bytes))
    sheet = workbook.active

    sheet["A1"] = "PMI Member Analysis"
    sheet["A3"] = "PDF pages"
    sheet["B3"] = summary.page_count
    sheet["A4"] = "Extracted text characters"
    sheet["B4"] = summary.text_chars

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def load_template_from_r2() -> bytes:
    missing = [
        name
        for name, value in {
            "R2_BUCKET": R2_BUCKET,
            "R2_ACCOUNT_ID": R2_ACCOUNT_ID,
            "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
            "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
        }.items()
        if not value
    ]
    if missing:
        raise HTTPException(status_code=500, detail="r2_template_not_configured")

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    try:
        obj = client.get_object(Bucket=R2_BUCKET, Key=R2_TEMPLATE_KEY)
        return obj["Body"].read()
    except Exception as exc:
        logger.warning("Failed to load Excel template from R2")
        raise HTTPException(status_code=500, detail="r2_template_load_failed") from exc
