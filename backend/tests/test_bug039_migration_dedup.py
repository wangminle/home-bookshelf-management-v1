"""BUG-039: 含历史重复 custom_fields 的库升级前应去重。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import settings
from app.models.base import create_engine_from_url


def test_upgrade_dedups_duplicate_custom_fields(tmp_path: Path):
    db_path = tmp_path / "dup.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    settings.database_url = db_url

    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    # env.py 会再用 settings.database_url 覆盖

    # 升到约束前一版
    command.upgrade(cfg, "c8d9e0f1a2b3")

    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO custom_fields (entity_type, entity_id, field_key, field_value, value_type, created_at) "
                "VALUES ('book', 1, 'shelf', 'A', 'string', datetime('now'))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO custom_fields (entity_type, entity_id, field_key, field_value, value_type, created_at) "
                "VALUES ('book', 1, 'shelf', 'B', 'string', datetime('now'))"
            )
        )
        n = conn.execute(text("SELECT COUNT(*) FROM custom_fields")).scalar()
        assert n == 2
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM custom_fields")).scalar()
        assert n == 1
        val = conn.execute(text("SELECT field_value FROM custom_fields")).scalar()
        assert val == "B"
    engine.dispose()
