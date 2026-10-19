"""PG 建库后任一初始化/回收失败都要清理本次创建的库。"""
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from tests import conftest as fixtures


@pytest.mark.parametrize('failure', ['url','mkdir','engine','sessionmaker','dispose', 'none'])
def test_created_pg_case_database_always_cleaned(tmp_path, monkeypatch, failure):
    from app import db as db_module
    from app.config import settings
    # 被测夹具会改这些全局绑定；本用例使用替身，结束后必须还原真实配置。
    monkeypatch.setattr(settings, 'database_url', settings.database_url)
    monkeypatch.setattr(settings, 'data_dir', settings.data_dir)
    monkeypatch.setattr(db_module, 'engine', db_module.engine)
    monkeypatch.setattr(db_module, 'SessionLocal', db_module.SessionLocal)
    monkeypatch.setenv('DATABASE_URL', settings.database_url)
    sqls = []
    engine = MagicMock()
    monkeypatch.setattr(fixtures, '_pg_admin', sqls.append)
    def fail():
        raise RuntimeError('injected failure')
    monkeypatch.setattr(fixtures, '_pg_url', lambda name: fail() if failure == 'url' else 'postgresql://unused/'+name)
    mkdir = Path.mkdir
    def controlled_mkdir(self, *args, **kwargs):
        if failure == 'mkdir' and self == tmp_path/'data':
            fail()
        return mkdir(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', controlled_mkdir)
    monkeypatch.setattr(fixtures, 'create_engine_from_url', lambda url: fail() if failure == 'engine' else engine)
    monkeypatch.setattr(fixtures, 'sessionmaker', lambda **kw: fail() if failure == 'sessionmaker' else MagicMock())
    if failure == 'dispose':
        engine.dispose.side_effect = RuntimeError('injected failure')
    gen = fixtures._pg_db_engine(tmp_path, 'owned_template')
    if failure in ('url','mkdir','engine','sessionmaker'):
        with pytest.raises(RuntimeError, match='injected'):
            next(gen)
    else:
        assert next(gen) is engine
        with pytest.raises(RuntimeError if failure == 'dispose' else StopIteration):
            next(gen)
    assert sqls[0].startswith('CREATE DATABASE ')
    name = sqls[0].split('"')[1]
    assert sqls[-1] == f'DROP DATABASE IF EXISTS "{name}"'
    assert any('pg_terminate_backend' in sql for sql in sqls)
    assert all('WITH (FORCE)' not in sql for sql in sqls)


def test_failed_create_never_drops_any_database(tmp_path, monkeypatch):
    sqls = []
    def admin(sql):
        sqls.append(sql)
        raise RuntimeError('create failed')
    monkeypatch.setattr(fixtures, '_pg_admin', admin)
    with pytest.raises(RuntimeError, match='create failed'):
        next(fixtures._pg_db_engine(tmp_path, 'owned_template'))
    assert len(sqls) == 1 and sqls[0].startswith('CREATE DATABASE')
