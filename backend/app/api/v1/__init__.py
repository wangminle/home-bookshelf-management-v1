from fastapi import APIRouter

from app.api.v1 import (
    attachments,
    books,
    copies,
    custom_fields,
    health,
    intake,
    members,
    notes,
    progress,
    purchases,
    reading_logs,
    recognize,
    stats,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(books.router)
api_router.include_router(intake.router)
api_router.include_router(progress.router)
api_router.include_router(purchases.router)
api_router.include_router(copies.router)
api_router.include_router(notes.router)
api_router.include_router(reading_logs.router)
api_router.include_router(attachments.router)
api_router.include_router(custom_fields.router)
api_router.include_router(stats.router)
api_router.include_router(members.router)
api_router.include_router(recognize.router)
