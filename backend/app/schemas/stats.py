from pydantic import BaseModel


class CategoryCount(BaseModel):
    category: str
    count: int


class MemberStats(BaseModel):
    id: int
    name: str
    books_reading: int
    books_finished: int
    reading_streak: int


class StatsOut(BaseModel):
    total_books: int
    by_status: dict[str, int]
    by_category: list[CategoryCount]
    total_spent: float
    purchase_count: int
    reading_logs_pages_total: int
    members: list[MemberStats]
