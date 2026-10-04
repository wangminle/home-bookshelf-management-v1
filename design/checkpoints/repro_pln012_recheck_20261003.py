"""PLN-012 修复后二次复核的隔离失败用例。

显式运行：
    PYTHONPATH=backend:cli:scripts backend/.venv/bin/python -m pytest \
        design/checkpoints/repro_pln012_recheck_20261003.py -q -s --tb=short

2026-10-03 二次复核时 8 failed，对应 7 项遗漏；修复后 8 passed。
本文件保留为独立复现入口；同样的断言已迁入 backend/tests/test_chk110_regression.py，
随正常后端全量回归执行，新增事务回滚和租约接管用例也在该测试文件。
所有数据库/文件均在临时目录，HTTP 使用 MockTransport，不写生产数据、不调真实模型。
"""
import os
import tempfile
from pathlib import Path
_bootstrap = tempfile.TemporaryDirectory(prefix='bookshelf-recheck-bootstrap-')
os.environ['DATABASE_URL'] = f"sqlite:///{Path(_bootstrap.name) / 'bootstrap.db'}"
os.environ['DATA_DIR'] = str(Path(_bootstrap.name) / 'data')
import json
from datetime import timedelta
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models.base import Base
from app.models import Book, Member, IntakeCandidate, IntakeCommandExecution
from app.services import intake_workflow as wf
import batch_import_covers as bic

@pytest.fixture
def sessions(tmp_path):
    settings.data_dir = tmp_path / 'data'
    settings.data_dir.mkdir()
    settings.database_url = f'sqlite:///{tmp_path}/case.db'
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    yield factory
    engine.dispose()

def prepare(sessions, *, existing=False, matching=False):
    with sessions() as db:
        owner = Member(name='审查Owner', role='owner')
        db.add(owner)
        db.commit()
        owner_id = owner.id
        if existing:
            db.add(Book(title='活着', normalized_title='活着', authors=json.dumps(['余华']), isbn13='9787506365437'))
            db.commit()
        item = wf.create_work_item(db, created_by_member_id=owner_id)
        item_id = item.id
        wf.add_photos(db, item_id, [{'photo_id': 'p1', 'filename': 'x.png', 'content': b'fake'}])
        cand = wf.build_candidates(db, item_id, {'p1': wf.RecognitionInput(
            title='活着' if matching else '三体', authors=['余华'] if matching else ['刘慈欣'], isbn='9787506365437')})[0]
        cand_id = cand.id
        ex = wf.confirm_candidates(db, item_id, [cand_id], decided_by_member_id=owner_id)[0]
        ex.status = 'executing'
        ex.lease_owner = 'crashed-worker'
        ex.lease_epoch = 1
        ex.attempts = 1
        ex.lease_expires_at = wf._now() - timedelta(seconds=1)
        db.commit()
        return item_id, cand_id, ex.id, owner_id

def test_inferred_confirmation_survives_first_failure(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'version': 1, 'source_dir': str(tmp_path), 'entries': [
        {'file': None, 'title': '人工确认书名', 'author': '人工确认作者', 'status': 'confirmed'}]}))
    class Client:
        def __init__(self): self.calls = []
        def health(self): return {'ok': True}
        def add(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1: raise RuntimeError('[HTTP 400] simulated-not-submitted')
            return {'ok': True, 'data': {'book': {'id': 1}, 'action': 'created_new'}}
    fake = Client()
    def args(*extra):
        return bic.build_parser().parse_args(['run', '--manifest', str(path), '--report', str(tmp_path/'report.json'), *extra])
    bic.cmd_run(args(), client=fake)
    saved = json.loads(path.read_text())['entries'][0]
    bic.cmd_run(args('--retry-failed'), client=fake)
    print('首次参数=',fake.calls[0], '失败落盘确认字段=',saved['confirmed_fields'], '重试参数=',fake.calls[1])
    assert fake.calls[1].get('field_policy') == 'prefer_confirmed'

def test_expired_recovery_preserves_isbn_conflict_guard(sessions):
    item_id, cand_id, ex_id, owner_id = prepare(sessions, existing=True)
    with sessions() as db:
        result = wf.retry_execution(db, ex_id, requester_member_id=owner_id)
        print('ISBN冲突恢复=', wf.execution_to_dict(result), '候选=',wf.candidate_to_dict(db.get(IntakeCandidate,cand_id)))
        assert result.status != 'completed'

def test_expired_recovery_checks_parameter_digest(sessions):
    item_id, cand_id, ex_id, owner_id = prepare(sessions, existing=True, matching=True)
    with sessions() as db:
        wf.preview_matches(db,item_id)  # 合法API操作改变 target_book_id，version 没有变化
    with sessions() as db:
        ex = db.get(IntakeCommandExecution,ex_id)
        cand = db.get(IntakeCandidate,cand_id)
        photos = []
        from app.models import IntakePhoto
        photos = list(db.scalars(select(IntakePhoto).where(IntakePhoto.work_item_id==item_id)))
        assert wf._hash_params(wf._command_params(cand,ex.command_type,photos)) != ex.params_hash
        result = wf.retry_execution(db,ex_id,requester_member_id=owner_id)
        print('摘要已变化但恢复=',wf.execution_to_dict(result))
        assert result.error_code == 'stale_confirmation'

def test_expired_recovery_checks_authorization_at_commit(sessions):
    item_id, cand_id, ex_id, owner_id = prepare(sessions, existing=True, matching=True)
    real_find = wf._find_existing_book
    def disable_then_find(db, **kw):
        with sessions() as other:
            owner = other.get(Member,owner_id)
            owner.disabled_at = wf._now()
            other.commit()
        return real_find(db,**kw)
    with sessions() as db, patch.object(wf,'_find_existing_book',side_effect=disable_then_find):
        rejected = False
        try:
            result = wf.retry_execution(db,ex_id,requester_member_id=owner_id)
            print('恢复期间Owner停用仍提交=',wf.execution_to_dict(result))
        except wf.WorkflowAuthError:
            rejected = True
        assert rejected

def test_stale_recovery_does_not_clear_new_active_lease(sessions):
    item_id, cand_id, ex_id, owner_id = prepare(sessions)
    with sessions() as other:
        wf.update_candidate(other,cand_id,title='三体（重新核对）')
    with sessions() as stale_session:
        stale_execution = stale_session.get(IntakeCommandExecution,ex_id)
        assert stale_execution.lease_owner == 'crashed-worker'
        with sessions() as new_session:
            ex = new_session.get(IntakeCommandExecution,ex_id)
            assert wf._claim_execution_lease(new_session,ex,lease_owner='new-active-worker',now=wf._now())
            assert new_session.get(IntakeCommandExecution,ex_id).lease_expires_at > wf._now().replace(tzinfo=None)
        wf.run_command_execution(stale_session,stale_execution,requester_member_id=owner_id)
    with sessions() as db:
        ex = db.get(IntakeCommandExecution,ex_id)
        print('旧快照恢复后新租约=',ex.lease_owner,'epoch=',ex.lease_epoch,'status=',ex.status)
        assert ex.lease_owner == 'new-active-worker'

def test_damaged_encoded_success_is_not_retried(tmp_path):
    import httpx
    from bookshelf.client import BookshelfClient
    path = tmp_path/'encoded-manifest.json'
    path.write_text(json.dumps({'version': 2,'source_dir':str(tmp_path),'entries':[
        {'file':None,'title':'已提交但压缩回执损坏','status':'confirmed','price':20.0}]}))
    requests = []
    def handler(request):
        requests.append(request)
        # 模拟服务端已提交购买记录，返回的gzip响应损坏
        return httpx.Response(201,headers={'Content-Encoding':'gzip'},content=b'invalid-gzip-receipt')
    real_client = httpx.Client
    def factory(**kw): return real_client(transport=httpx.MockTransport(handler),**kw)
    def args(*extra):
        return bic.build_parser().parse_args(['run','--manifest',str(path),'--report',str(tmp_path/'encoded-report.json'),*extra])
    client = BookshelfClient('http://review.invalid')
    with patch.object(client,'health',return_value={'ok':True}), patch('bookshelf.client.httpx.Client',side_effect=factory):
        bic.cmd_run(args(),client=client)
        saved = json.loads(path.read_text())['entries'][0]
        bic.cmd_run(args('--retry-failed'),client=client)
    print('坏gzip状态=',saved['status'],'error=',saved['result']['error'],'请求次数=',len(requests))
    assert len(requests) == 1
    assert saved['status'] == 'outcome_unknown'

def test_reconcile_counts_surviving_original_creation(tmp_path):
    path = tmp_path/'reconcile-manifest.json'
    path.write_text(json.dumps({'version':2,'source_dir':str(tmp_path),'entries':[
        {'file':None,'title':'本批新书','status':'imported','result':{'outcome':'created','book_id':10}}]}))
    class Client:
        def __init__(self): self.calls=[]
        def show(self,book_id):
            self.calls.append(book_id)
            return {'ok':True,'data':{'id':book_id,'title':f'书{book_id}','isbn13':'9787506365437' if book_id == 18 else None,'cover_path':'covers/existing.jpg'}}
    client = Client()
    out = tmp_path/'reconcile-report.json'
    args = bic.build_parser().parse_args(['reconcile','--manifest',str(path),'--out',str(out),'--apply','--map','10=18:9787506365437'])
    bic.cmd_reconcile(args,client=client)
    summary = json.loads(out.read_text())['summary']
    print('原创建10仍存活，照片关联18后=',summary,'核对ID=',client.calls)
    assert summary['net_created'] == 1

def test_gateway_error_after_commit_does_not_duplicate_purchase(sessions,tmp_path):
    import httpx
    from sqlalchemy import func
    from app.models import PurchaseRecord, BookCopy
    from bookshelf.client import BookshelfClient
    path = tmp_path/'gateway-manifest.json'
    path.write_text(json.dumps({'version':2,'source_dir':str(tmp_path),'entries':[
        {'file':None,'title':'网关回执丢失书','isbn':'9787506365437','price':30.0,'location':'书架A','status':'confirmed'}]}))
    requests = []
    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        with sessions() as db, patch('app.services.intake.fetch_metadata',return_value=None):
            wf.intake_book(db,wf.IntakeInput(**payload))
        # 入库、副本、购买均已提交；网关丢失上游回执后返回502
        return httpx.Response(502,content=b'<html>upstream response lost</html>')
    real_client = httpx.Client
    def factory(**kw): return real_client(transport=httpx.MockTransport(handler),**kw)
    def args(*extra):
        return bic.build_parser().parse_args(['run','--manifest',str(path),'--report',str(tmp_path/'gateway-report.json'),*extra])
    client = BookshelfClient('http://review.invalid')
    with patch.object(client,'health',return_value={'ok':True}), patch('bookshelf.client.httpx.Client',side_effect=factory):
        bic.cmd_run(args(),client=client)
        saved = json.loads(path.read_text())['entries'][0]
        bic.cmd_run(args('--retry-failed'),client=client)
    with sessions() as db:
        purchases = db.scalar(select(func.count()).select_from(PurchaseRecord))
        copies = db.scalar(select(func.count()).select_from(BookCopy))
        print('已提交后502状态=',saved['status'],'请求次数=',len(requests),'购买记录=',purchases,'副本=',copies)
        assert purchases == 1
        assert copies == 1
