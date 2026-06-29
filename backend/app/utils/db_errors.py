from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class ConflictError(Exception):
    pass


def integrity_error_message(exc: IntegrityError) -> str:
    raw = str(exc.orig).lower()
    if "isbn13" in raw or "books.isbn13" in raw:
        return "书籍已存在（ISBN 冲突）"
    if "foreign key" in raw:
        return "关联的成员或副本 ID 不存在"
    if "unique" in raw:
        return "数据唯一性冲突"
    return "数据库约束冲突"


def rollback_on_integrity(db: Session, exc: IntegrityError) -> ConflictError:
    db.rollback()
    return ConflictError(integrity_error_message(exc))
