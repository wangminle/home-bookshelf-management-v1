import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.schemas.book import ApiResponse
from app.schemas.intake import CoverRecognizeOut, IsbnRecognizeOut
from app.services.cover_recognition import recognize_cover
from app.services.recognition import recognize_isbn_from_image

router = APIRouter(prefix="/recognize", tags=["recognize"])


@router.post("/isbn", response_model=ApiResponse)
async def recognize_isbn(image: UploadFile = File(...)) -> ApiResponse:
    if not image.filename:
        raise HTTPException(status_code=400, detail="请上传图片文件")

    suffix = Path(image.filename).suffix or ".jpg"
    temp_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_file = Path(tmp.name)
            tmp.write(await image.read())

        isbn13 = await run_in_threadpool(recognize_isbn_from_image, temp_file)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    if isbn13:
        return ApiResponse(
            data=IsbnRecognizeOut(isbn13=isbn13, found=True, message=f"识别到 ISBN: {isbn13}").model_dump()
        )
    return ApiResponse(data=IsbnRecognizeOut(isbn13=None, found=False, message="未识别到 ISBN 条码").model_dump())


@router.post("/cover", response_model=ApiResponse)
async def recognize_cover_endpoint(
    image: UploadFile = File(...),
    title: str | None = Form(default=None),
    author: str | None = Form(default=None),
) -> ApiResponse:
    if not image.filename:
        raise HTTPException(status_code=400, detail="请上传图片文件")

    suffix = Path(image.filename).suffix or ".jpg"
    temp_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_file = Path(tmp.name)
            tmp.write(await image.read())

        result = await run_in_threadpool(recognize_cover, temp_file, title=title, author=author)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    out = CoverRecognizeOut(
        found=result.found,
        isbn13=result.isbn13,
        title=result.title,
        authors=result.authors,
        publisher=result.publisher,
        cover_path=result.cover_path,
        matched_source=result.matched_source,
        message=result.message,
    )
    return ApiResponse(data=out.model_dump())
