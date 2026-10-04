"""CHK-110 二次复核遗漏回归：真实工作流/入库服务与HTTP故障注入。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
import json
from datetime import timedelta
from unittest.mock import patch
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.models import Book, Member, IntakeCandidate, IntakeCommandExecution, IntakeChangeSet
from app.services import intake_workflow as wf
import batch_import_covers as bic

@pytest.fixture
def sessions(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False)

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
        # 合法API操作改变候选内容：version+1 → 命令参数摘要变化（BUG-266修复后
        # preview_matches 不再改写已确认候选，改用 update_candidate 制造摘要漂移）
        wf.update_candidate(db, cand_id, title='活着（修订版）')
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


def _pending_command(sessions):
    item_id, cand_id, ex_id, owner_id = prepare(sessions, matching=True)
    with sessions() as db:
        ex = db.get(IntakeCommandExecution, ex_id)
        ex.status = 'pending'
        ex.lease_owner = None
        ex.lease_expires_at = None
        db.commit()
    return item_id, cand_id, ex_id, owner_id


def test_receipt_store_failure_rolls_back_book(sessions):
    from sqlalchemy.sql.dml import Update
    from sqlalchemy import func
    _, _, ex_id, owner_id = _pending_command(sessions)
    with sessions() as db, patch('app.services.intake.fetch_metadata', return_value=None):
        real_execute = db.execute
        def failing_receipt(statement, *args, **kwargs):
            if (isinstance(statement, Update)
                    and statement.table.name == 'intake_command_executions'
                    and statement.compile().params.get('status') == 'completed'):
                raise RuntimeError('receipt-store-down')
            return real_execute(statement, *args, **kwargs)
        with patch.object(db, 'execute', side_effect=failing_receipt):
            try:
                wf.run_command_execution(db, db.get(IntakeCommandExecution, ex_id),
                                         requester_member_id=owner_id)
            except RuntimeError:
                db.rollback()
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Book)) == 0


def test_owner_revoked_during_metadata_rolls_back_book(sessions):
    from sqlalchemy import func
    _, _, ex_id, owner_id = _pending_command(sessions)
    def revoke(**kwargs):
        with sessions() as other:
            owner = other.get(Member, owner_id)
            owner.disabled_at = wf._now()
            other.commit()
        return None
    with sessions() as db, patch('app.services.intake.fetch_metadata', side_effect=revoke):
        with pytest.raises(wf.WorkflowAuthError):
            wf.run_command_execution(db, db.get(IntakeCommandExecution, ex_id),
                                     requester_member_id=owner_id)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Book)) == 0
        assert db.get(IntakeCommandExecution, ex_id).status != 'completed'


def test_book_and_receipt_commit_together(sessions):
    from sqlalchemy import func
    _, _, ex_id, owner_id = _pending_command(sessions)
    observations = []
    with sessions() as db, patch('app.services.intake.fetch_metadata', return_value=None):
        real_commit = db.commit
        def observe_commit():
            count = db.scalar(select(func.count()).select_from(Book))
            if count:
                state = db.scalar(select(IntakeCommandExecution.status).where(
                    IntakeCommandExecution.id == ex_id))
                observations.append(state)
            return real_commit()
        with patch.object(db, 'commit', side_effect=observe_commit):
            result = wf.run_command_execution(db, db.get(IntakeCommandExecution, ex_id),
                                               requester_member_id=owner_id)
        assert result.status == 'completed'
    assert observations
    assert set(observations) == {'completed'}


def test_takeover_during_recovery_keeps_new_lease_and_change_set(sessions):
    _, _, ex_id, owner_id = prepare(sessions, existing=True, matching=True)
    real_find = wf._find_existing_book
    def takeover(db, **kwargs):
        with sessions() as other:
            assert wf._claim_execution_lease(
                other, other.get(IntakeCommandExecution, ex_id),
                lease_owner='new-worker', now=wf._now())
        return real_find(db, **kwargs)
    with sessions() as db, patch.object(wf, '_find_existing_book', side_effect=takeover):
        wf.retry_execution(db, ex_id, requester_member_id=owner_id)
    with sessions() as db:
        ex = db.get(IntakeCommandExecution, ex_id)
        assert (ex.status, ex.lease_owner, ex.lease_epoch) == ('executing', 'new-worker', 2)
        assert db.get(IntakeChangeSet, ex.change_set_id).status == 'pending'
        assert ex.book_id is None


def test_takeover_during_metadata_rolls_back_old_business_write(sessions):
    from sqlalchemy import func
    _, _, ex_id, owner_id = _pending_command(sessions)
    def takeover(**kwargs):
        with sessions() as other:
            ex = other.get(IntakeCommandExecution, ex_id)
            ex.lease_expires_at = wf._now() - timedelta(seconds=1)
            other.commit()
            assert wf._claim_execution_lease(
                other, ex, lease_owner='new-worker', now=wf._now())
        return None
    with sessions() as db, patch('app.services.intake.fetch_metadata', side_effect=takeover):
        wf.retry_execution(db, ex_id, requester_member_id=owner_id)
    with sessions() as db:
        ex = db.get(IntakeCommandExecution, ex_id)
        assert ex.status == 'executing' and ex.lease_owner == 'new-worker'
        assert db.get(IntakeChangeSet, ex.change_set_id).status == 'pending'
        assert db.scalar(select(func.count()).select_from(Book)) == 0


def test_old_failure_after_takeover_does_not_fail_new_change_set(sessions):
    _, _, ex_id, owner_id = _pending_command(sessions)
    def takeover_then_fail(db, cand, **kwargs):
        with sessions() as other:
            ex = other.get(IntakeCommandExecution, ex_id)
            ex.lease_expires_at = wf._now() - timedelta(seconds=1)
            other.commit()
            assert wf._claim_execution_lease(
                other, ex, lease_owner='new-worker', now=wf._now())
        raise RuntimeError('旧执行者失败')
    with sessions() as db, patch.object(wf, '_execute_create_book', side_effect=takeover_then_fail):
        wf.retry_execution(db, ex_id, requester_member_id=owner_id)
    with sessions() as db:
        ex = db.get(IntakeCommandExecution, ex_id)
        assert ex.status == 'executing' and ex.lease_owner == 'new-worker'
        assert db.get(IntakeChangeSet, ex.change_set_id).status == 'pending'


@pytest.mark.parametrize('existing', [False, True])
def test_domain_finalizer_failure_rolls_back_purchase_and_copy(sessions, existing):
    from sqlalchemy import func
    from app.models import PurchaseRecord, BookCopy
    with sessions() as db:
        if existing:
            db.add(Book(title='事务边界书', normalized_title='事务边界书'))
            db.commit()
        def fail_receipt(result):
            assert result.created_copy and result.created_purchase
            raise RuntimeError('回执提交失败')
        with patch('app.services.intake.fetch_metadata', return_value=None), pytest.raises(RuntimeError):
            wf.intake_book(db, wf.IntakeInput(title='事务边界书', price=30, location='书架A'),
                           finalize=fail_receipt)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Book)) == int(existing)
        assert db.scalar(select(func.count()).select_from(PurchaseRecord)) == 0
        assert db.scalar(select(func.count()).select_from(BookCopy)) == 0
