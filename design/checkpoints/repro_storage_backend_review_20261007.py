"""2026-10-07 工作区复核诊断：隔离临时数据库与文件，不读写真实数据库。
输出旧迁移升级、启动恢复并发上传和同键并发转正的实际结果。
这是历史复核场景快照，不是宣称修复成功的正式回归套件。
修复后异常路径可能改变；需据新的业务契约改写验收断言。
"""
import os, shutil, tempfile, subprocess, sys, threading
from pathlib import Path
from io import BytesIO
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(tempfile.mkdtemp(prefix='storage-review-',dir='/tmp'))
os.environ['DATABASE_URL'] = f'sqlite:///{WORK}/init.db'
os.environ['DATA_DIR'] = str(WORK/'data')
sys.path.insert(0, str(ROOT/'backend'))
from alembic import command
from alembic.config import Config
from app.config import settings
from app.models.base import create_engine_from_url
from app.models import Member, StorageRoom, StorageShelf
from app.models.storage import ShelfPhoto
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text, select
from app.services import storage_photos as photos, storage_tx
from PIL import Image

buf=BytesIO(); Image.new('RGB', (8,8), (10,20,30)).save(buf, 'JPEG'); image=buf.getvalue()

def case(name, *, old=False):
    folder=WORK/name; folder.mkdir()
    mig=folder/'alembic'; shutil.copytree(ROOT/'backend/alembic', mig, ignore=shutil.ignore_patterns('__pycache__'))
    migration=mig/'versions/l3c4d5e6f7a8_storage_locations.py'
    if old:
        migration.write_bytes(subprocess.check_output(['git', 'show', '2b2767a:backend/alembic/versions/l3c4d5e6f7a8_storage_locations.py'], cwd=ROOT))
    settings.database_url=f'sqlite:///{folder}/case.db'; settings.data_dir=folder/'data'; settings.data_dir.mkdir()
    cfg=Config(str(ROOT/'backend/alembic.ini')); cfg.set_main_option('script_location', str(mig))
    command.upgrade(cfg, 'l3c4d5e6f7a8' if old else 'head')
    if old:
        migration.write_bytes((ROOT/'backend/alembic/versions/l3c4d5e6f7a8_storage_locations.py').read_bytes())
        command.upgrade(cfg, 'head')
    engine=create_engine_from_url(settings.database_url); sessions=sessionmaker(bind=engine, autoflush=False)
    session=sessions(); owner=Member(name='Owner',role='owner'); room=StorageRoom(code='R',name='Room'); session.add_all([owner,room]); session.flush()
    shelf=StorageShelf(room_id=room.id,code='S',name='Shelf'); session.add(shelf); session.commit()
    return session,sessions,shelf.id,owner.id

def api_client(session,sessions,oid):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_db
    from app import db as db_module
    db_module.SessionLocal = sessions
    from app.services import agent_access
    def override_db():
        with sessions() as current:
            yield current
    app.dependency_overrides[get_db] = override_db
    token,_ = agent_access.create_web_session(session,oid)
    client = TestClient(app,raise_server_exceptions=False)
    client.cookies.set('hbs_session',token,domain='testserver.local')
    client.headers.update({'Origin':'http://127.0.0.1'})
    return client

def upload(session,sid,oid,key):
    return photos.upload_photo(session,sid,data=image,caption=None,is_primary=False,idempotency_key=key,operator_member_id=oid)

session,sessions,sid,oid=case('existing-database',old=True)
print('OLD_UPGRADED_SCHEMA',session.scalar(text("SELECT sql FROM sqlite_master WHERE name='shelf_photos'")))
p1=upload(session,sid,oid,'old-upload')['photo']
photos.delete_photo(session,p1['id'],version=p1['version'],idempotency_key='old-delete',operator_member_id=oid)
p2=upload(session,sid,oid,'new-upload')['photo']
late=photos.delete_photo(session,p1['id'],version=p1['version'],idempotency_key='late-delete',operator_member_id=oid)
print('OLD_UPGRADED_RESULT',{'old_id':p1['id'],'new_id':p2['id'],'late_delete':late,'remaining':session.scalars(select(ShelfPhoto.id)).all()})
session.close()

session,sessions,sid,oid=case('recovery-v-upload')
original_begin=storage_tx.begin_operation
recovery=[]
def begin_after_recovery(*a,**kw):
    def worker_start():
        with sessions() as other:
            recovery.append(photos.promote_staged_uploads(other))
    thread=threading.Thread(target=worker_start); thread.start(); thread.join()
    return original_begin(*a,**kw)
storage_tx.begin_operation=begin_after_recovery
p=upload(session,sid,oid,'race-upload')['photo']
storage_tx.begin_operation=original_begin
row=session.get(ShelfPhoto,p['id']); final=settings.data_dir/row.relative_path
print('RECOVERY_V_UPLOAD_RESULT',{'recovery':recovery,'receipt_id':p['id'],'db_path':row.relative_path,'final_exists':final.exists(),'staged_exists':Path(str(final)+'.staged').exists(),'content_path':str(photos.resolve_photo_path(row.relative_path))})
print('RECOVERY_V_UPLOAD_CONTENT_HTTP',api_client(session,sessions,oid).get(f"/api/v1/storage/photos/{p['id']}/content").status_code)
r=upload(session,sid,oid,'race-upload')
with sessions() as other: after=photos.promote_staged_uploads(other)
print('RECOVERY_V_UPLOAD_RETRY',{'receipt_id':r['photo']['id'],'final_exists':final.exists(),'startup_recovery':after})
session.close()

session,sessions,sid,oid=case('interrupted-replay-concurrency')
p=upload(session,sid,oid,'same-key')['photo']; row=session.get(ShelfPhoto,p['id']); final=settings.data_dir/row.relative_path; staged=Path(str(final)+'.staged'); final.rename(staged)
clients=[api_client(session,sessions,oid) for _ in range(2)]
barrier=threading.Barrier(2); original_is_file=Path.is_file
# 调度点：两个重试都确认同一个 stage 存在，随后同时调用未加保护的 replace。
def staged_is_file(path):
    result=original_is_file(path)
    if path==staged and result:
        barrier.wait(timeout=10)
    return result
Path.is_file=staged_is_file
outcomes=[]
def retry(client):
    response=client.post(f'/api/v1/storage/shelves/{sid}/photos',files={'image':('test.jpg',image,'image/jpeg')},data={'idempotency_key':'same-key','is_primary':'false'})
    outcomes.append({'http_status':response.status_code,'response':response.text})
threads=[threading.Thread(target=retry,args=(client,)) for client in clients]
for thread in threads: thread.start()
for thread in threads: thread.join()
Path.is_file=original_is_file
print('INTERRUPTED_CONCURRENT_REPLAY_RESULT',outcomes,'final_exists',final.exists())
session.close()
session,sessions,sid,oid=case('deleted-photo-replay')
p1=upload(session,sid,oid,'deleted-upload')['photo']; final=settings.data_dir/session.get(ShelfPhoto,p1['id']).relative_path; final.rename(Path(str(final)+'.staged'))
photos.delete_photo(session,p1['id'],version=p1['version'],idempotency_key='delete-photo',operator_member_id=oid)
p2=upload(session,sid,oid,'replacement-upload')['photo']; replay=upload(session,sid,oid,'deleted-upload')['photo']
with sessions() as other: recovered=photos.promote_staged_uploads(other)
client=api_client(session,sessions,oid)
print('DELETED_REPLAY_RESULT',{'old_id':p1['id'],'new_id':p2['id'],'replayed_id':replay['id'],'old_content_http':client.get(f"/api/v1/storage/photos/{p1['id']}/content").status_code,'new_content_http':client.get(f"/api/v1/storage/photos/{p2['id']}/content").status_code,'photos':session.scalars(select(ShelfPhoto.id)).all(),'startup':recovered})
print('WORK',WORK)
