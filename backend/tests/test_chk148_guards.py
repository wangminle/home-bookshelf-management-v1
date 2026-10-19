import pytest
from sqlalchemy import update
from app.models import Book


def books(db_session):
    a, b = Book(title="封面书"), Book(title="条码书", isbn13="9787536692930")
    db_session.add_all([a, b])
    db_session.commit()
    return a.id, b.id


def test_patch_replace_clears_mismatched_isbn10(client, db_session):
    a, _ = books(db_session)
    row = db_session.get(Book, a)
    row.isbn13 = "9787020002207"
    row.isbn10 = "702000220X"
    db_session.commit()
    r = client.patch(f"/api/v1/books/{a}", json={
        "isbn13": "9787111128069", "isbn10": None, "expected_isbn13": "9787020002207"})
    assert r.status_code == 200, r.text
    db_session.refresh(row)
    assert row.isbn13 == "9787111128069" and row.isbn10 is None

    # 仅传 isbn13（不显式传 isbn10）替换时，残留的不匹配旧 isbn10 也应自动清空
    row.isbn13 = "9787020002207"
    row.isbn10 = "702000220X"
    db_session.commit()
    r2 = client.patch(f"/api/v1/books/{a}", json={
        "isbn13": "9787111128069", "expected_isbn13": "9787020002207"})
    assert r2.status_code == 200, r2.text
    db_session.refresh(row)
    assert row.isbn13 == "9787111128069" and row.isbn10 is None

    # 同一次请求同时显式传入互不匹配的 isbn13 与 isbn10 时，必须返回 409
    r3 = client.patch(f"/api/v1/books/{a}", json={
        "isbn13": "9787111128069", "isbn10": "702000220X", "expected_isbn13": "9787111128069"})
    assert r3.status_code == 409


def test_backfill_does_not_attach_inconsistent_isbn10(db_session):
    from app.services.books import _backfill_target_from_snapshot

    target = Book(title="目标", isbn13="9787536692930")
    db_session.add(target)
    db_session.commit()
    _backfill_target_from_snapshot(db_session, target, {
        "isbn13": None, "isbn10": "702000220X", "cover_path": None,
        "subtitle": None, "authors": None, "publisher": None, "publish_date": None,
        "page_count": None, "language": None, "category": None, "summary": None,
        "openlibrary_id": None, "google_books_id": None,
    })
    assert target.isbn13 == "9787536692930" and target.isbn10 is None


def test_patch_rejects_stale_isbn_precondition(client, db_session):
    a, _ = books(db_session)
    assert client.patch(f"/api/v1/books/{a}", json={"isbn13": "9787020002207"}).status_code == 200
    r = client.patch(f"/api/v1/books/{a}", json={"isbn13": "9787111128069", "expected_isbn13": None})
    assert r.status_code == 409
    assert db_session.get(Book, a).isbn13 == "9787020002207"


def test_merge_rechecks_isbn_at_server(client, db_session):
    a, b = books(db_session)
    db_session.execute(update(Book).where(Book.id == a).values(isbn13="9787020002207"))
    db_session.commit()
    r = client.post(f"/api/v1/books/{a}/merge", params={"source_id": b})
    assert r.status_code == 409
    assert db_session.get(Book, b) is not None


def test_merge_rejects_changed_preview_even_with_force(client, db_session):
    a, b = books(db_session)
    r = client.post(f"/api/v1/books/{a}/merge", params={
        "source_id": b, "force": "true", "expected_source_isbn13": "", "expected_target_isbn13": ""})
    assert r.status_code == 409
    assert db_session.get(Book, b) is not None


def test_merge_force_explicitly_allows_conflicting_isbn(client, db_session):
    a, b = books(db_session)
    db_session.execute(update(Book).where(Book.id == a).values(isbn13="9787020002207"))
    db_session.commit()
    assert client.post(f"/api/v1/books/{a}/merge", params={"source_id": b, "force": "true"}).status_code == 200


def test_pg_template_never_drops_preexisting_database(monkeypatch):
    from tests import conftest as fixtures
    sqls = []
    monkeypatch.setattr(fixtures, "_PG_ADMIN_URL", "postgresql://unused/postgres")
    monkeypatch.setattr(fixtures, "_pg_admin", sqls.append)
    monkeypatch.setattr(fixtures, "_run_migrations", lambda url: None)
    gen = fixtures._pg_template_db.__wrapped__()
    name = next(gen)
    assert name != "hbs_test_template"
    assert sqls == [f'CREATE DATABASE "{name}"']
    with pytest.raises(StopIteration):
        next(gen)
    assert sqls[-1] == f'DROP DATABASE IF EXISTS "{name}"'


@pytest.mark.parametrize("operation", ["set_isbn", "merge"])
def test_waiting_writer_rechecks_committed_isbn_under_lock(db_engine, db_session, monkeypatch, operation):
    """真实双连接：第二个请求持有旧 ORM 对象，在第一个写事务后必须拒绝。"""
    import threading
    from sqlalchemy.orm import Session
    from app.services import books as service
    from app.schemas.book import BookUpdate
    from app.utils.db_errors import ConflictError

    target, source = books(db_session)
    locked, attempting = threading.Event(), threading.Event()
    original = service._lock_book
    outcomes = []

    def controlled_lock(session, bid):
        if threading.current_thread().name == "isbn-first":
            result = original(session, bid)
            locked.set()
            assert attempting.wait(10)
            return result
        attempting.set()
        return original(session, bid)

    monkeypatch.setattr(service, "_lock_book", controlled_lock)

    def first():
        try:
            with Session(db_engine) as session:
                service.update_book(session, target, BookUpdate(isbn13="9787020002207"))
            outcomes.append("first_saved")
        except Exception as exc:
            outcomes.append(exc)
            locked.set()

    def second():
        try:
            assert locked.wait(10)
            with Session(db_engine) as session:
                stale = session.get(Book, target)
                assert stale.isbn13 is None  # 第一请求持有写锁，但还没有提交
                try:
                    if operation == "set_isbn":
                        service.update_book(session, target, BookUpdate(isbn13="9787111128069", expected_isbn13=None))
                    else:
                        service.merge_books(session, target_id=target, source_id=source, force=True,
                                            expected_target_isbn13="", expected_source_isbn13="9787536692930")
                except ConflictError:
                    outcomes.append("second_conflict")
                    session.rollback()
                else:
                    outcomes.append("unsafe_success")
        except Exception as exc:
            attempting.set()
            outcomes.append(exc)

    threads = [threading.Thread(target=first, name="isbn-first"), threading.Thread(target=second, name="isbn-second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes, key=str) == ["first_saved", "second_conflict"], outcomes
    db_session.expire_all()
    assert db_session.get(Book, target).isbn13 == "9787020002207"
    assert db_session.get(Book, source) is not None


@pytest.mark.parametrize("failure", ["create", "migrate"])
def test_pg_template_cleanup_only_after_successful_create(monkeypatch, failure):
    from tests import conftest as fixtures
    sqls = []
    def admin(sql):
        sqls.append(sql)
        if failure == "create" and sql.startswith("CREATE"):
            raise RuntimeError("already exists")
    def migrate(url):
        if failure == "migrate":
            raise RuntimeError("migration failed")
    monkeypatch.setattr(fixtures, "_PG_ADMIN_URL", "postgresql://unused/postgres")
    monkeypatch.setattr(fixtures, "_pg_admin", admin)
    monkeypatch.setattr(fixtures, "_run_migrations", migrate)
    with pytest.raises(RuntimeError):
        next(fixtures._pg_template_db.__wrapped__())
    assert len(sqls) == (1 if failure == "create" else 2)
