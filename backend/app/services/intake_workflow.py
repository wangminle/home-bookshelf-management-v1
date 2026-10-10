"""拍照批量入库工作流服务（PLN-012 M3 / BI-13～15）。

链路：上传照片 →（识别/手工）候选书目（多图关联、版本化）→ 既有书匹配
预览（逐字段差异，无写入）→ Owner 确认（Decision 绑定候选版本）→ 幂等执行
（CommandExecution）→ 回执/重试。

不变量：
- 模型与候选永不直写图书库；书目写入只经领域服务 intake_book（prefer_confirmed）；
- 同 command_key 同参数 → 返回既有回执；同键不同参数 → 拒绝（BI-15）；
- 确认绑定候选版本与参数摘要（BUG-252）：确认后字段再修改生成新版本，
  旧命令执行/恢复时按 stale_confirmation 明确拒绝，须重新确认；
- 执行租约原子条件领取（BUG-253）：抢占只有一方成功；提交回执前按
  lease_owner+epoch 条件更新二次校验，被接管者不得落回执、不得清他人租约；
  崩溃后按回执/查重恢复，不盲目重发；
- ISBN 归属冲突（BUG-254）贯穿匹配预览、确认、执行三环节，须显式人工解决
  （force_link），不因 match_book_id 自动放行；
- 执行者授权（BUG-256）：API 执行路径逐条命令副作用前与提交时核验请求者仍为
  有效 Owner，批内停用/撤权立即阻断；
- photo_id 白名单 + 落盘路径 containment（BUG-265）：客户端可控 ID 不得写出
  照片目录；批量中途失败（含部分写入与提交失败）回滚并清理已写文件；
- 软删除候选（rejected，BUG-268 重建取代）不可再确认/编辑，其未执行命令
  一并 superseded 退出流程（BUG-282）；
- 执行汇总只统计当前有效版本命令（旧版本标记 superseded），且 completed 还需
  无遗留待核对候选（BUG-267/274）；单命令重试后按同一口径重算任务状态
  （BUG-283）；仍有 executing 命令（含租约未过期的中断执行）时任务保持
  executing 而非 failed（BUG-289），否则前端恢复入口消失。
- 上传落盘路径含随机后缀且失败只清理本批文件（BUG-284）：并发上传同一
  photo_id 时，失败方回滚不得删除成功方引用的文件；撞唯一约束转 409 语义；
- 重建候选跳过已确认/已执行候选（及识别重建时人工编辑过的待核对候选，
  version>1 或证据来源为 user，BUG-285/286）所引用的照片——同一照片不得同
  时属于保留候选与新候选；重建与确认先锁任务行，使归属读取和写入互斥
  （BUG-285）；确认时校验照片未被其他已确认/已执行候选或本批候选占用；
  识别全局能力故障（禁用/未配置/鉴权失败）如实报错且不触发重建；
- 已执行候选不可拆分（BUG-287，与"不可编辑"对齐），否则已完成命令因版本
  失格把任务永久锁成 failed。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Book,
    IntakeCandidate,
    IntakeChangeSet,
    IntakeCommandExecution,
    IntakeDecision,
    IntakePhoto,
    IntakeWorkItem,
    Member,
)
from app.services.intake import IntakeInput, IntakeResult, _cleanup_orphan_cover, intake_book
from app.services.storage import save_uploaded_image
from app.utils.book_helpers import (
    deserialize_json_list,
    is_valid_isbn,
    normalize_isbn,
    normalize_title,
)

LEASE_SECONDS = 60
COMMAND_CREATE_BOOK = "intake.create_book"
COMMAND_LINK_PHOTOS = "intake.link_photos"

# BUG-265：photo_id 为客户端可控（multipart photo_ids），必须白名单约束，
# 否则 "../../escaped" 之类会写出照片目录（路径穿越）。
_PHOTO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# BUG-269：自动 photo_id（p0001…）按既有最大序号续号。
_PHOTO_SEQ_RE = re.compile(r"^p(\d+)$")
_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


class WorkflowError(ValueError):
    """工作流业务错误（API 层映射 400/409）。"""


class WorkflowLeaseLostError(WorkflowError):
    """业务提交前发现租约已被接管，当前事务必须回滚。"""


class WorkflowAuthError(WorkflowError):
    """执行者授权失效（BUG-256：停用/撤权），API 层映射 403。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    """SQLite DateTime 读回 naive；统一按 UTC 补 tzinfo 后再比较。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


# ── WorkItem / Photo ──

def create_work_item(db: Session, *, title: str = "", created_by_member_id: int | None = None) -> IntakeWorkItem:
    item = IntakeWorkItem(title=(title or "").strip()[:200] or "批量入库任务",
                          status="draft", created_by_member_id=created_by_member_id)
    db.add(item)
    db.commit()
    return item


def get_work_item(db: Session, work_item_id: int) -> IntakeWorkItem:
    item = db.get(IntakeWorkItem, work_item_id)
    if item is None:
        raise WorkflowError(f"任务 {work_item_id} 不存在")
    return item


def photos_dir(work_item_id: int) -> Path:
    return settings.data_dir / "intake_photos" / str(work_item_id)


def _validate_photo_id(pid: str) -> str:
    """BUG-265：photo_id 白名单校验（仅字母/数字/下划线/连字符，≤64 字符）。"""
    if not _PHOTO_ID_RE.fullmatch(pid):
        raise WorkflowError(
            f"photo_id 不合法: {pid!r}（仅允许字母、数字、下划线、连字符，最长 64 字符）")
    return pid


def add_photos(
    db: Session,
    work_item_id: int,
    uploads: list[dict],
) -> list[IntakePhoto]:
    """保存照片（photo_id 稳定、内容 SHA-256、角色可指定）。

    uploads: [{photo_id, filename, content(bytes), role?}]
    同 work_item 内 photo_id 重复即 409 语义（WorkflowError）。
    BUG-265：photo_id 先全量白名单校验再落盘；解析后的最终路径必须仍在
    本任务照片目录内（纵深防御）；批量中途失败回滚并清理已写文件，不留孤儿。
    """
    item = get_work_item(db, work_item_id)
    existing = {
        p.photo_id for p in db.scalars(
            select(IntakePhoto).where(IntakePhoto.work_item_id == work_item_id))
    }
    target_dir = photos_dir(work_item_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    prepared: list[tuple[str, Path, bytes, str | None]] = []
    for up in uploads:
        pid = str(up.get("photo_id") or "").strip()
        if not pid:
            raise WorkflowError("照片缺少 photo_id")
        _validate_photo_id(pid)  # BUG-265：写路径前拒绝穿越型 photo_id
        if pid in existing:
            raise WorkflowError(f"photo_id 已存在: {pid}")
        content = up.get("content") or b""
        if not content:
            raise WorkflowError(f"照片 {pid} 内容为空")
        suffix = Path(str(up.get("filename") or "")).suffix or ".jpg"
        if not _SUFFIX_RE.fullmatch(suffix):
            suffix = ".jpg"
        # 解析后的最终路径必须留在本任务照片目录内（防御纵深）。
        # BUG-284：文件名加随机后缀，每次上传落盘路径独立——并发请求同时
        # 上传同一 photo_id 时不再共享路径，失败方的清理不会删除成功方的文件。
        abs_path = (root / f"{pid}.{uuid.uuid4().hex[:8]}{suffix}").resolve()
        if not abs_path.is_relative_to(root):
            raise WorkflowError(f"照片 {pid} 路径越界，已拒绝")
        role = up.get("role") if up.get("role") in ("cover", "barcode", "other") else "unknown"
        prepared.append((pid, abs_path, content, role))
        existing.add(pid)
    saved: list[IntakePhoto] = []
    written: list[Path] = []
    try:
        for pid, abs_path, content, role in prepared:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            # BUG-265：先登记再写——write_bytes 部分写入后抛错时，正在写的
            # 文件也必须落在清理范围内，否则留下半截孤儿文件
            written.append(abs_path)
            abs_path.write_bytes(content)
            rel_path = abs_path.relative_to(settings.data_dir.resolve()).as_posix()
            photo = IntakePhoto(
                work_item_id=work_item_id, photo_id=pid, file_path=rel_path,
                sha256=hashlib.sha256(content).hexdigest(),
                role=role,
                size_bytes=len(content),
            )
            db.add(photo)
            saved.append(photo)
        if item.status == "draft" and saved:
            item.status = "in_review"
        # BUG-265：提交也在清理保护范围内——提交失败时数据库无照片记录，
        # 已落盘文件必须全部回滚清理，不得留下无记录的孤儿文件
        db.commit()
    except IntegrityError as exc:
        # BUG-284：并发上传同一 photo_id 时两方都可能通过上面的存在性检查，
        # 后提交方在唯一约束 uq_intake_photo_pid 上失败。回滚并只清理本批自己
        # 写入的文件（路径含随机后缀，与他人文件不共享），成功方的记录与文件
        # 不受影响；转为 409 语义业务错误而非 500。
        for path in written:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        db.rollback()
        raise WorkflowError(
            "photo_id 已存在（并发上传冲突），请更换 photo_id 后重试") from exc
    except Exception:
        # 批量中途失败（含部分写入与提交失败）：回滚本批写入并清理已落盘
        # 文件。清理尽力而为：单个 unlink 失败不得跳过回滚，也不掩盖原始异常。
        for path in written:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        db.rollback()
        raise
    return saved


def next_photo_sequence(db: Session, work_item_id: int) -> int:
    """BUG-269：自动 photo_id 从既有最大 p<数字> 序号续号（第二轮上传不冲突）。"""
    pids = db.scalars(select(IntakePhoto.photo_id).where(
        IntakePhoto.work_item_id == work_item_id)).fetchall()
    highest = 0
    for pid in pids:
        match = _PHOTO_SEQ_RE.match(pid)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def photo_abs_path(photo: IntakePhoto) -> Path:
    """BUG-265：读取侧纵深防御——解析后必须仍位于 intake_photos 目录内。"""
    root = (settings.data_dir / "intake_photos").resolve()
    path = (settings.data_dir / photo.file_path).resolve()
    if not path.is_relative_to(root):
        raise WorkflowError(f"照片 {photo.photo_id} 路径越界，已拒绝")
    return path


# ── BI-13：候选构建、配对建议、拆分与重新关联 ──

@dataclass
class RecognitionInput:
    """单张照片的识别/手工结果（VisionCandidate 的可序列化投影）。"""

    title: str | None = None
    subtitle: str | None = None
    authors: list[str] | None = None
    isbn: str | None = None
    confidence: float | None = None
    readable: dict | None = None
    warnings: list[dict] | None = None
    source: str = "vision"  # vision | user


def _is_user_authored(cand: IntakeCandidate) -> bool:
    """候选是否含人工输入：任一字段证据来源为 user。

    BUG-286：仅看 version 不够——拆分出的新候选版本为 1，却继承了人工字段；
    手工构建接口直接保存的 source=user 候选也是 v1。证据来源随拆分继承，
    因此作为保护判据。
    BUG-292：version>1 也不能作为判据——拆分本身就会给原候选加版本，
    只拆分未改字段的候选（证据仍是 vision）不是人工候选，重新识别应正常取代。
    人工编辑必经 update_candidate，会把全部字段证据写成 source=user。
    """
    evidence = _json_loads(cand.evidence, {})
    return isinstance(evidence, dict) and any(
        isinstance(v, dict) and v.get("source") == "user" for v in evidence.values())


def _lock_work_item(db: Session, work_item_id: int) -> None:
    """对同任务的确认/重建加事务写锁，关闭归属检查之后的并发窗口。"""
    db.flush()
    result = db.execute(
        update(IntakeWorkItem).execution_options(synchronize_session=False)
        .where(IntakeWorkItem.id == work_item_id)
        .values(status=IntakeWorkItem.status))
    if result.rowcount != 1:
        db.rollback()
        raise WorkflowError(f"任务 {work_item_id} 不存在")
    # 清除锁前 ORM 缓存；默认 READ COMMITTED 下后续读取看到已提交内容。
    db.expire_all()


def _claimed_photo_ids(db: Session, work_item_id: int, *, exclude_candidate_id: int | None = None) -> set[str]:
    """已确认/已执行候选占用的 photo_id 集合（BUG-285 归属校验共用）。

    直查 photo_ids 列、不经 ORM 身份缓存；调用方在任务写锁内读取，
    尚未 flush 的本批归属由确认函数单独记录。
    """
    stmt = select(IntakeCandidate.photo_ids).where(
        IntakeCandidate.work_item_id == work_item_id,
        IntakeCandidate.status.in_(("confirmed", "executed")))
    if exclude_candidate_id is not None:
        stmt = stmt.where(IntakeCandidate.id != exclude_candidate_id)
    claimed: set[str] = set()
    for raw in db.scalars(stmt):
        claimed.update(_json_loads(raw, []))
    return claimed


def build_candidates(
    db: Session,
    work_item_id: int,
    recognized: dict[str, RecognitionInput | dict],
    *,
    protect_edited: bool = False,
) -> list[IntakeCandidate]:
    """按配对建议把照片组成候选：相同 SHA → 相同 ISBN → 相同归一化书名。

    建议仅供核对：内容哈希只证明照片相同；书名相似不自动合并版本/卷册，
    全部候选都进入 pending_review 由 Owner 处置。
    protect_edited（识别重建路径）：人工编辑过的待核对候选（任一业务字段
    证据来源为 user——编辑/拆分继承/手工构建都会产生）视为已被人工修正，
    保留不取代，其照片也不参与重新分组；只拆分未改字段的候选不算人工候选
    （BUG-292），仍参与重建。
    """
    _lock_work_item(db, work_item_id)
    all_photos = db.scalars(select(IntakePhoto).where(
        IntakePhoto.work_item_id == work_item_id)).fetchall()
    if not all_photos:
        raise WorkflowError("任务没有照片，先上传")

    # BUG-285：已确认/已执行候选保留其照片归属——重建不得把这些照片再分组
    # 进新待核对候选，否则同一照片同时属于旧确认候选与新候选，确认执行新书
    # 时同一照片会被建成两本不同的书。
    claimed_pids: set[str] = set()
    protected_ids: set[int] = set()
    for keep in db.scalars(select(IntakeCandidate).where(
            IntakeCandidate.work_item_id == work_item_id,
            IntakeCandidate.status.in_(("confirmed", "executed")))):
        claimed_pids.update(_json_loads(keep.photo_ids, []))
    if protect_edited:
        # BUG-286：识别重跑不得清空用户已保存的人工候选（证据来源为 user；
        # 拆分/手工构建产生的 v1 人工候选同样受保护）。
        # BUG-292：只拆分未改字段的候选证据仍是 vision，不在保护之列。
        for keep in db.scalars(select(IntakeCandidate).where(
                IntakeCandidate.work_item_id == work_item_id,
                IntakeCandidate.status == "pending_review")):
            if _is_user_authored(keep):
                protected_ids.add(keep.id)
                claimed_pids.update(_json_loads(keep.photo_ids, []))
    photos = [p for p in all_photos if p.photo_id not in claimed_pids]

    def _as_input(value) -> RecognitionInput:
        if isinstance(value, RecognitionInput):
            return value
        return RecognitionInput(**{
            k: value.get(k) for k in
            ("title", "subtitle", "authors", "isbn", "confidence", "readable", "warnings", "source")
        } if isinstance(value, dict) else {})

    groups: list[list[IntakePhoto]] = []
    by_sha: dict[str, list[IntakePhoto]] = {}
    # BUG-291：警告属于照片本身——对本轮识别的全部照片更新（失败写入、成功
    # 清除）；受保护/已归属照片只是不参与候选分组，不得连警告更新一并跳过。
    # BUG-295：recognized 未提及的照片（局部识别/公开构建接口的部分输入）
    # 不属于本轮识别范围，必须保留其既有警告，不得按空结果清空。
    for photo in all_photos:
        if photo.photo_id not in recognized:
            continue
        rec = _as_input(recognized[photo.photo_id])
        photo.warnings = json.dumps(rec.warnings or [], ensure_ascii=False)
    for photo in photos:
        by_sha.setdefault(photo.sha256, []).append(photo)

    # 1) 相同内容照片同组
    grouped: set[int] = set()
    for same in by_sha.values():
        if len(same) > 1:
            groups.append(same)
            grouped.update(id(p) for p in same)

    # 2) 相同 ISBN / 3) 相同归一化书名（在未分组照片中合并）
    remaining = [p for p in photos if id(p) not in grouped]

    def _merge(key_fn):
        buckets: dict[str, list[IntakePhoto]] = {}
        for photo in list(remaining):
            rec = _as_input(recognized.get(photo.photo_id, RecognitionInput()))
            key = key_fn(rec)
            if key:
                buckets.setdefault(key, []).append(photo)
        for members in buckets.values():
            if len(members) > 1:
                groups.append(members)
                for m in members:
                    remaining.remove(m)

    _merge(lambda r: normalize_isbn(r.isbn) or None)
    _merge(lambda r: normalize_title(r.title or "") or None)
    for photo in remaining:
        groups.append([photo])

    # 4) 条码照片（有 ISBN 无书名）建议并入相邻（先上传）且无 ISBN 的组：
    # "封面 + 封底条码"是同一本书的常见拍法（契约 §4.3：相邻配对须核对）
    for group in list(groups):
        if len(group) != 1 or group[0].role != "barcode":
            continue
        photo = group[0]
        rec = _as_input(recognized.get(photo.photo_id, RecognitionInput()))
        if not normalize_isbn(rec.isbn or ""):
            continue
        for target in groups:
            if target is group:
                continue
            has_isbn = any(normalize_isbn(_as_input(
                recognized.get(g.photo_id, RecognitionInput())).isbn or "") for g in target)
            if not has_isbn and max(g.id for g in target) < photo.id:
                target.append(photo)
                groups.remove(group)
                break

    # BUG-285：任务锁内保留写入前的照片归属复验；复验本身不能替代事务互斥。
    overlap = {p.photo_id for p in photos} & _claimed_photo_ids(db, work_item_id)
    if overlap:
        db.rollback()
        raise WorkflowError(
            f"候选归属已变更（并发确认/执行）：照片 {sorted(overlap)} 已存在于"
            "其他已确认/已执行候选，请刷新后重试")

    # 清掉该任务既有 pending_review 候选（重建建议；已确认/已执行候选保留）。
    # BUG-268：被历史 ChangeSet/Decision 引用的 pending 候选不得物理删除（FK 约束），
    # 标记 rejected 软删除，重建不再触发 FOREIGN KEY constraint failed。
    stale_pending = db.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == work_item_id,
        IntakeCandidate.status == "pending_review")).fetchall()
    for old in stale_pending:
        if old.id in protected_ids:
            continue  # BUG-286：人工编辑过的候选不被识别重建取代
        referenced = (
            db.scalar(select(IntakeChangeSet.id).where(
                IntakeChangeSet.candidate_id == old.id).limit(1)) is not None
            or db.scalar(select(IntakeDecision.id).where(
                IntakeDecision.candidate_id == old.id).limit(1)) is not None)
        if referenced:
            old.status = "rejected"
            # BUG-282：软删除候选的未执行命令一并退出流程（superseded）——
            # 否则命令版本仍等于候选当前版本，整批执行会把已被取代的旧识别
            # 结果真正入库，或以 stale_confirmation 失败污染任务汇总。
            for old_set in db.scalars(select(IntakeChangeSet).where(
                    IntakeChangeSet.candidate_id == old.id)):
                if old_set.status == "pending":
                    old_set.status = "superseded"
                for old_exec in db.scalars(select(IntakeCommandExecution).where(
                        IntakeCommandExecution.change_set_id == old_set.id,
                        IntakeCommandExecution.status.in_(("pending", "failed")))):
                    old_exec.status = "superseded"
        else:
            db.delete(old)

    candidates: list[IntakeCandidate] = []
    for members in groups:
        recs = {p.photo_id: _as_input(recognized.get(p.photo_id, RecognitionInput())) for p in members}
        # 代表识别：优先 cover 角色，其次置信度最高
        cover_recs = [(p, recs[p.photo_id]) for p in members if p.role == "cover"]
        pool = cover_recs or [(p, recs[p.photo_id]) for p in members]
        lead_photo, lead = max(pool, key=lambda pr: (pr[1].confidence or 0.0))
        isbn = next((normalize_isbn(r.isbn) for r in recs.values()
                     if r.isbn and is_valid_isbn(r.isbn)), None)
        authors = lead.authors or []
        evidence = {}
        for field in ("title", "subtitle", "authors", "isbn"):
            value = {"title": lead.title, "subtitle": lead.subtitle,
                     "authors": authors, "isbn": isbn}[field]
            evidence[field] = {
                "value": value,
                "source": lead.source,
                "confidence": lead.confidence if value else None,
                "readable": bool((lead.readable or {}).get(field)) if lead.readable else None,
            }
        cand = IntakeCandidate(
            work_item_id=work_item_id, version=1, status="pending_review",
            title=(lead.title or None), subtitle=(lead.subtitle or None),
            authors=json.dumps(authors, ensure_ascii=False) if authors else None,
            isbn=isbn,
            evidence=json.dumps(evidence, ensure_ascii=False),
            photo_ids=json.dumps(sorted(p.photo_id for p in members), ensure_ascii=False),
        )
        db.add(cand)
        candidates.append(cand)

    item = get_work_item(db, work_item_id)
    if item.status == "draft":
        item.status = "in_review"
    db.commit()
    return candidates


def update_candidate(
    db: Session,
    candidate_id: int,
    *,
    title=None, subtitle=None, authors=None, isbn=None,
    photo_ids=None,
) -> IntakeCandidate:
    """编辑候选字段或重新关联照片：版本 +1，失去既有确认资格（BI-15）。"""
    cand = db.get(IntakeCandidate, candidate_id)
    if cand is None:
        raise WorkflowError(f"候选 {candidate_id} 不存在")
    if cand.status == "executed":
        raise WorkflowError("已执行的候选不可编辑")
    if cand.status == "rejected":
        # BUG-282：软删除候选仅供追溯，编辑应针对重建后的新候选
        raise WorkflowError("已被重建取代的候选不可编辑；请编辑重建后的新候选")
    changed = False
    content_changed = False
    if title is not None:
        cand.title = (str(title).strip() or None)
        changed = True
        content_changed = True
    if subtitle is not None:
        # BUG-276：显式空字符串保留（清空语义），与 None（未提供）区分
        cand.subtitle = str(subtitle).strip()
        changed = True
        content_changed = True
    if authors is not None:
        cand.authors = json.dumps([str(a).strip() for a in authors if str(a).strip()],
                                  ensure_ascii=False) or None
        changed = True
        content_changed = True
    if isbn is not None:
        normalized = normalize_isbn(str(isbn)) if str(isbn).strip() else None
        if normalized and not is_valid_isbn(normalized):
            raise WorkflowError("ISBN 校验位不正确")
        cand.isbn = normalized
        changed = True
        content_changed = True
    if photo_ids is not None:
        # BUG-315：与拆分路径（BUG-293）同口径——不允许清空全部照片；
        # 空照片候选是永远无法完成的空壳，且因 _is_user_authored 保护
        # 不会被重建取代，任务会卡在非 completed 状态
        if not photo_ids:
            raise WorkflowError(
                "不能把候选照片全部清空（原候选将没有照片）；如整组都不需要，"
                "请拒绝该候选或拆分到其他候选")
        # 重新关联（拆分/合并的另一面）：照片必须属于同一任务
        item_photos = {
            p.photo_id for p in db.scalars(select(IntakePhoto).where(
                IntakePhoto.work_item_id == cand.work_item_id))
        }
        unknown = [pid for pid in photo_ids if pid not in item_photos]
        if unknown:
            raise WorkflowError(f"照片不属于本任务: {unknown}")
        cand.photo_ids = json.dumps(sorted(photo_ids), ensure_ascii=False)
        changed = True
    if not changed:
        return cand
    if content_changed:
        # BUG-272：内容变更使既有匹配与冲突判定失效——否则"匹配 A 后改成 B
        # 仍链 A"，且旧的 force_link 解决结果对新内容不再有效。
        cand.match_book_id = None
        cand.match_diff = None
        cand.conflicts = None
    if cand.status == "confirmed":
        cand.status = "pending_review"  # 确认后修改 → 失去原确认资格
    cand.version += 1
    evidence = _json_loads(cand.evidence, {})
    for field, value in (("title", cand.title), ("subtitle", cand.subtitle),
                         ("authors", _json_loads(cand.authors, [])),
                         ("isbn", cand.isbn)):
        # BUG-292：无条件写入——人工编辑必须把全部字段证据标为 user，
        # _is_user_authored 只认证据来源，不再看版本号
        evidence[field] = {"value": value, "source": "user", "confidence": None,
                           "readable": None}
    cand.evidence = json.dumps(evidence, ensure_ascii=False)
    db.commit()
    return cand


def split_candidate(db: Session, candidate_id: int, *, photo_ids_to_new: list[str]) -> tuple[IntakeCandidate, IntakeCandidate]:
    """拆分：把部分照片移到新候选（新候选继承字段、版本 1、待核对）。"""
    cand = db.get(IntakeCandidate, candidate_id)
    if cand is None:
        raise WorkflowError(f"候选 {candidate_id} 不存在")
    if cand.status == "executed":
        # BUG-287：与 update_candidate 对齐——已执行候选的确认回执已绑定
        # 当前版本与照片关联，拆分会使已完成命令永久失格（stale_confirmation），
        # 任务被重算为 failed 且无法恢复。
        raise WorkflowError("已执行的候选不可拆分")
    if cand.status == "confirmed":
        # BUG-294：已确认候选的命令绑定了当前版本与照片集合；拆分会加版本并
        # 退回待核对，旧命令随之失格——此时任务仍是 confirmed，页面继续提供
        # 执行入口，点击即 stale_confirmation、任务被打成 failed。
        # 调整分组的正确路径：先保存一次编辑（update_candidate 会把 confirmed
        # 退回 pending_review），再拆分；重新识别重建会保留已确认候选，
        # 不会把它们的照片拆出去，不能作为分组调整手段。
        raise WorkflowError(
            "已确认的候选不可拆分；如需调整照片分组，请先保存一次编辑"
            "（候选退回待核对）后再拆分")
    if cand.status == "rejected":
        # BUG-282：软删除候选不可拆分（照片归属重建后的新候选）
        raise WorkflowError("已被重建取代的候选不可拆分；请拆分重建后的新候选")
    current = _json_loads(cand.photo_ids, [])
    moving = [pid for pid in photo_ids_to_new if pid in current]
    if not moving:
        raise WorkflowError("没有可拆出的照片")
    staying = [pid for pid in current if pid not in moving]
    if not staying:
        # BUG-293：拆走全部照片会留下无照片的待核对空壳——重建不会清除它，
        # 真实候选执行后任务也永远凑不齐完成条件。
        raise WorkflowError(
            "不能把全部照片都拆出（原候选将没有照片）；如整组都不需要，"
            "请直接重新识别或调整候选")
    new = IntakeCandidate(
        work_item_id=cand.work_item_id, version=1, status="pending_review",
        title=cand.title, subtitle=cand.subtitle, authors=cand.authors, isbn=cand.isbn,
        evidence=cand.evidence, photo_ids=json.dumps(sorted(moving), ensure_ascii=False),
    )
    db.add(new)
    cand.photo_ids = json.dumps(sorted(staying), ensure_ascii=False)
    cand.version += 1
    db.commit()
    return cand, new


# ── 识别（模型只生成候选，不写书目）──

def recognize_photos(db: Session, work_item_id: int) -> dict:
    """对任务内全部照片跑统一视觉服务并重建候选（规划 §4.6/§4.8）。

    模型禁用/配置缺失时如实返回错误，不由工作台静默兜底；单张失败不阻断
    其余照片，逐张记录错误后仍构建候选（识别失败的照片成为待补全候选）。
    """
    from app.services import vision as vision_module

    # BUG-286：这类错误意味着"所有照片都不可能识别成功"（全局能力故障），
    # 与单张照片损坏/超时等局部失败区分开。
    global_errors = {vision_module.ERR_DISABLED, vision_module.ERR_NOT_CONFIGURED,
                     vision_module.ERR_UNAUTHORIZED}

    photos = db.scalars(select(IntakePhoto).where(
        IntakePhoto.work_item_id == work_item_id)).fetchall()
    if not photos:
        raise WorkflowError("任务没有照片，先上传")
    recognized: dict[str, RecognitionInput] = {}
    failures: list[dict] = []
    for photo in photos:
        result = vision_module.recognize_cover_fields(db, photo_abs_path(photo))
        if not result.ok or result.candidate is None:
            failures.append({"photo_id": photo.photo_id,
                             "error_code": result.error_code,
                             "message": result.message})
            recognized[photo.photo_id] = RecognitionInput(
                warnings=[{"code": "recognition_failed", "message": result.message}])
            continue
        c = result.candidate
        recognized[photo.photo_id] = RecognitionInput(
            title=c.title, subtitle=c.subtitle, authors=c.authors, isbn=c.isbn,
            confidence=c.confidence,
            readable={k: f.readable for k, f in c.fields.items()},
            warnings=c.warnings, source="vision",
        )
    if (len(failures) == len(photos)
            and all(f["error_code"] in global_errors for f in failures)):
        # 全局能力故障：如实报错且不触发候选重建——否则用户已保存的人工候选
        # 会被清掉而接口仍返回成功。
        first = failures[0]
        raise WorkflowError(
            f"视觉识别不可用（{first['error_code']}）：{first['message']}；"
            "既有候选已保留，请恢复模型配置后重试")
    # protect_edited：单张失败等局部场景仍重建候选，但不得覆盖人工编辑过的候选
    candidates = build_candidates(db, work_item_id, recognized, protect_edited=True)
    return {
        "photos_total": len(photos),
        "failures": failures,
        "candidates": [candidate_to_dict(c) for c in candidates],
    }


# ── BI-14：既有图书匹配预览与逐字段差异（只读）──

def preview_matches(db: Session, work_item_id: int) -> list[dict]:
    """对每个候选查既有书（ISBN 键 / normalized_title / 展示书名回退）并记差异。

    预览不写书目；命中不等于绑定，绑定发生在确认后的执行（link_photos）。
    BUG-254：ISBN 归属冲突（ISBN 命中他书且书名/作者明显不一致）在此识别并
    写入候选 conflicts，贯穿确认与执行两个后续环节；须经显式人工解决
    （确认时 resolutions={"候选ID": "force_link"}），不因 match_book_id 自动放行。
    BUG-266：只处理 pending_review 候选——已确认/已执行候选的 match_book_id 与
    conflicts 属于确认时快照，重算会改变命令参数摘要导致执行 stale_confirmation。
    """
    candidates = db.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == work_item_id,
        IntakeCandidate.status == "pending_review")).fetchall()
    results = []
    for cand in candidates:
        match = _find_existing_book(
            db, isbn=cand.isbn,
            title=cand.title,
            authors=_json_loads(cand.authors, []),
        )
        conflict = _conflict_record(db, cand, match)
        cand.conflicts = json.dumps(conflict, ensure_ascii=False) if conflict else None
        if match is None:
            cand.match_book_id = None
            cand.match_diff = None
            results.append({"candidate_id": cand.id, "match_book_id": None, "diff": None})
            continue
        diff = _field_diff(cand, match)
        cand.match_book_id = match.id
        cand.match_diff = json.dumps(diff, ensure_ascii=False)
        results.append({"candidate_id": cand.id, "match_book_id": match.id, "diff": diff})
    db.commit()
    return results


def _isbn_conflict_for_candidate(db: Session, cand: IntakeCandidate, match: Book | None) -> dict | None:
    """BUG-254：候选 ISBN 命中既有书但书名/作者与其明显不一致 → 冲突描述。

    判别口径与领域服务 intake._check_isbn_ownership 一致（书名/作者任一方
    吻合即视为同一书放行；仅 ISBN 无书名作者输入无从判别，不冲突）。
    """
    if match is None or not cand.isbn:
        return None
    cand_titles = {normalize_title(cand.title)} if cand.title and cand.title.strip() else set()
    book_titles = {
        normalize_title(t) for t in (match.title, match.normalized_title) if t and t.strip()
    }
    if cand_titles & book_titles:
        return None
    cand_authors = {a.strip().lower() for a in _json_loads(cand.authors, [])}
    book_authors = {a.strip().lower() for a in deserialize_json_list(match.authors)}
    if cand_authors and book_authors and cand_authors & book_authors:
        return None
    if not cand_titles and not cand_authors:
        return None  # 仅 ISBN 输入，无从判别归属
    return {
        "code": "isbn_ownership_conflict",
        "message": (f"ISBN {cand.isbn} 已绑定《{match.title}》，与候选"
                    f"《{cand.title or '（无书名）'}》明显不一致，须人工核对"),
        "book_id": match.id,
        "existing_title": match.title,
    }


def _conflict_record(db: Session, cand: IntakeCandidate, match: Book | None) -> dict | None:
    """计算冲突并保留已有人工解决标记（force_link 且目标书不变则延续）。"""
    conflict = _isbn_conflict_for_candidate(db, cand, match)
    if conflict is None:
        return None
    previous = _json_loads(cand.conflicts, None)
    if (isinstance(previous, dict)
            and previous.get("resolved") == "force_link"
            and previous.get("book_id") == conflict["book_id"]):
        conflict["resolved"] = "force_link"
        conflict["resolved_by_member_id"] = previous.get("resolved_by_member_id")
    return conflict


def _find_existing_book(db: Session, *, isbn, title, authors) -> Book | None:
    from app.services.intake import _find_existing

    normalized_isbn = normalize_isbn(isbn) if isbn else None
    return _find_existing(
        db, isbn13=normalized_isbn, isbn10=None,
        title=(title or "").strip(), authors=authors or None,
    )


def _field_diff(cand: IntakeCandidate, book: Book) -> dict:
    book_authors = deserialize_json_list(book.authors) or []
    cand_authors = _json_loads(cand.authors, [])
    diff: dict[str, Any] = {}
    if cand.title and normalize_title(cand.title) != normalize_title(book.title or ""):
        diff["title"] = {"candidate": cand.title, "existing": book.title}
    if cand_authors and {a.strip().lower() for a in cand_authors} != {a.strip().lower() for a in book_authors}:
        diff["authors"] = {"candidate": cand_authors, "existing": book_authors}
    if cand.isbn and normalize_isbn(cand.isbn) != normalize_isbn(book.isbn13 or book.isbn10 or ""):
        diff["isbn"] = {"candidate": cand.isbn, "existing": book.isbn13 or book.isbn10}
    return diff


# ── BI-15：确认与幂等执行 ──

def _command_params(cand: IntakeCandidate, command_type: str, photos: list[IntakePhoto]) -> dict:
    return {
        "command_type": command_type,
        "candidate_id": cand.id,
        "candidate_version": cand.version,
        "target_book_id": cand.match_book_id,
        "title": cand.title, "subtitle": cand.subtitle,
        "authors": _json_loads(cand.authors, []),
        "isbn": cand.isbn,
        "photo_sha256": sorted(p.sha256 for p in photos),
    }


def _hash_params(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def confirm_candidates(
    db: Session,
    work_item_id: int,
    candidate_ids: list[int],
    *,
    decided_by_member_id: int | None,
    note: str | None = None,
    resolutions: dict[int | str, str] | None = None,
) -> list[IntakeCommandExecution]:
    """确认选中候选：生成 ChangeSet + Decision（绑定版本）+ 幂等命令。

    确认已存在书（match_book_id）时命令为 link_photos（默认仅建立照片关联，
    更新字段需另行明确操作）；否则 create_book（经领域服务，prefer_confirmed）。
    BUG-254：候选带未解决的 ISBN 归属冲突时拒绝确认——须经明确人工解决
    （resolutions={candidate_id: "force_link"} 显式强制关联，或修改候选字段
    后重新预览确认），不因已有 match_book_id 自动放行。
    BUG-285 纵深防御：确认时校验所选候选的照片未被其他已确认/已执行候选
    占用（排除自身），并发归属冲突与同一确认批次内的照片重叠都拒绝（409）。
    """
    _lock_work_item(db, work_item_id)
    item = get_work_item(db, work_item_id)
    if item.status in ("executing", "completed"):
        raise WorkflowError(f"任务状态 {item.status} 不可确认")
    if not candidate_ids:
        # BUG-280：空列表确认会把任务置为 confirmed 却无命令可执行，须显式拒绝（400）
        raise WorkflowError("candidate_ids 为空，请选择要确认的候选")
    try:
        resolved_map = {int(k): v for k, v in (resolutions or {}).items()}
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"resolutions 的键必须是候选 ID（整数）: {exc}") from exc
    executions: list[IntakeCommandExecution] = []
    batch_claimed: set[str] = set()
    for cid in candidate_ids:
        cand = db.get(IntakeCandidate, cid)
        if cand is None or cand.work_item_id != work_item_id:
            raise WorkflowError(f"候选 {cid} 不属于本任务")
        if cand.status == "rejected":
            # BUG-282：软删除的历史候选（重建时被取代）仅供追溯，不得再次
            # 确认并实际入库——否则过时识别结果重新成为有效书目。
            raise WorkflowError(
                f"候选 {cid} 已被重建取代（已拒绝），不能确认；请选择重建后的新候选")
        if cand.status in ("confirmed", "executed"):
            executions.extend(_pending_executions(db, work_item_id, cand.id))
            continue
        if not (cand.title or cand.isbn):
            raise WorkflowError(f"候选 {cid} 缺书名且缺 ISBN，无法确认入库")

        # BUG-285：照片归属校验（与 build_candidates 的提交时复验互补）——
        # 所选候选的照片不得已被其他已确认/已执行候选占用（排除自身；同批
        # 先前刚确认的候选也计入，防同一确认请求内照片重叠）。与 BUG-254 的
        # ISBN 归属冲突不同：这里校验的是照片归属。
        photo_ids = _json_loads(cand.photo_ids, [])
        claimed = _claimed_photo_ids(db, work_item_id, exclude_candidate_id=cand.id)
        taken = set(photo_ids) & (claimed | batch_claimed)
        if taken:
            db.rollback()  # 拒绝整批，不能留下半批确认/决策/命令。
            raise WorkflowError(
                f"候选归属冲突（并发确认）：照片 {sorted(taken)} 已存在于其他"
                "已确认/已执行候选，请刷新后重试")
        batch_claimed.update(photo_ids)

        # BUG-254：确认现算 ISBN 归属冲突（用当前候选值，不轻信预览时的存储值）。
        # 已有人工解决标记仅对同一目标书生效；否则须显式 force_link。
        if cand.match_book_id:
            conflict = _isbn_conflict_for_candidate(db, cand, db.get(Book, cand.match_book_id))
            if conflict is not None:
                stored = _json_loads(cand.conflicts, None)
                resolved = (isinstance(stored, dict)
                            and stored.get("resolved") == "force_link"
                            and stored.get("book_id") == conflict["book_id"])
                if not resolved:
                    if resolved_map.get(cid) != "force_link":
                        raise WorkflowError(
                            f"候选 {cid} 存在 ISBN 归属冲突：《{conflict.get('existing_title')}》；"
                            "须人工解决（确认时显式 force_link 强制关联，或修改候选后重新预览确认）")
                    conflict["resolved"] = "force_link"
                    conflict["resolved_by_member_id"] = decided_by_member_id
                    cand.conflicts = json.dumps(conflict, ensure_ascii=False)

        change_set = db.scalar(select(IntakeChangeSet).where(
            IntakeChangeSet.candidate_id == cand.id,
            IntakeChangeSet.candidate_version == cand.version))
        if change_set is None:
            change_set = IntakeChangeSet(
                work_item_id=work_item_id, candidate_id=cand.id,
                candidate_version=cand.version, status="pending",
                created_by_member_id=decided_by_member_id,
            )
            db.add(change_set)
            db.flush()

        db.add(IntakeDecision(
            work_item_id=work_item_id, change_set_id=change_set.id,
            candidate_id=cand.id, candidate_version=cand.version,
            decided_by_member_id=decided_by_member_id, action="confirm", note=note,
        ))

        photo_ids = _json_loads(cand.photo_ids, [])
        photos = list(db.scalars(select(IntakePhoto).where(
            IntakePhoto.work_item_id == work_item_id,
            IntakePhoto.photo_id.in_(photo_ids))))
        command_type = COMMAND_LINK_PHOTOS if cand.match_book_id else COMMAND_CREATE_BOOK
        params = _command_params(cand, command_type, photos)
        key = _hash_params(params)
        execution = db.scalar(select(IntakeCommandExecution).where(
            IntakeCommandExecution.command_key == key))
        if execution is None:
            execution = IntakeCommandExecution(
                command_key=key, change_set_id=change_set.id,
                command_type=command_type, params_hash=_hash_params(params),
                status="pending", requested_by_member_id=decided_by_member_id,
            )
            db.add(execution)
        elif execution.params_hash != _hash_params(params):
            raise WorkflowError("同幂等键参数不一致，拒绝（请重新确认生成新命令）")
        cand.status = "confirmed"
        executions.append(execution)

        # BUG-267：新版本确认后，旧版本 ChangeSet/命令标记 superseded——
        # 旧命令（如 failed(stale_confirmation)）不再参与执行汇总统计，
        # 任务得以按当前有效版本判定 completed。
        for old_set in db.scalars(select(IntakeChangeSet).where(
                IntakeChangeSet.candidate_id == cand.id,
                IntakeChangeSet.candidate_version < cand.version)):
            if old_set.status == "pending":
                old_set.status = "superseded"
            for old_exec in db.scalars(select(IntakeCommandExecution).where(
                    IntakeCommandExecution.change_set_id == old_set.id,
                    IntakeCommandExecution.status.in_(("pending", "failed")))):
                old_exec.status = "superseded"
    item.status = "confirmed" if item.status != "confirmed" else item.status
    db.commit()
    return executions


def _pending_executions(db: Session, work_item_id: int, candidate_id: int) -> list[IntakeCommandExecution]:
    return list(db.scalars(select(IntakeCommandExecution).join(IntakeChangeSet).where(
        IntakeChangeSet.work_item_id == work_item_id,
        IntakeChangeSet.candidate_id == candidate_id)))


def execute_work_item(db: Session, work_item_id: int, *,
                      requester_member_id: int | None = None) -> dict:
    """执行任务：逐命令幂等执行，返回汇总。

    崩溃恢复：executing 且租约过期的命令先按回执/查重恢复（不盲目重发）。
    BUG-256：requester_member_id 提供时（API 层必传），每条命令副作用前与
    提交时核验执行者仍是有效 Owner；批内停用/撤权立即阻断并上抛 403 语义。
    """
    item = get_work_item(db, work_item_id)
    _assert_executor_authorized(db, requester_member_id)
    executions = list(db.scalars(select(IntakeCommandExecution).join(IntakeChangeSet).where(
        IntakeChangeSet.work_item_id == work_item_id)))
    if not executions:
        raise WorkflowError("任务没有可执行命令，先确认候选")
    item.status = "executing"
    db.commit()

    try:
        outcomes = []
        for execution in executions:
            _assert_executor_authorized(db, requester_member_id)  # 批内逐条核验
            outcomes.append(run_command_execution(db, execution,
                                                requester_member_id=requester_member_id))
    except WorkflowError:
        # 授权失效等：任务退回 confirmed，命令保持既有状态，交由重新授权后执行
        db.rollback()
        item = db.get(IntakeWorkItem, work_item_id)
        if item is not None and item.status == "executing":
            item.status = "confirmed"
            db.commit()
        raise

    # BUG-267/274：汇总按当前有效版本命令与遗留待核对候选判定（共用重算逻辑）。
    status = _recompute_work_item_status(db, work_item_id)
    executions = list(db.scalars(select(IntakeCommandExecution).join(IntakeChangeSet).where(
        IntakeChangeSet.work_item_id == work_item_id)))
    return {
        "work_item_id": work_item_id,
        "status": status,
        "executions": [execution_to_dict(e) for e in executions],
    }


def _recompute_work_item_status(db: Session, work_item_id: int) -> str:
    """按命令终态重算任务汇总状态并提交。

    BUG-267：汇总只统计当前有效版本的命令——确认后编辑会使旧版本命令失格
    （superseded），不得再计入。BUG-274：completed 还要求无遗留 pending_review
    候选，否则只确认执行了部分候选也会把任务锁成 completed，其余候选无法再确认。
    BUG-283：单命令重试同样改变命令/候选终态，必须走同一重算，不得沿用
    重试前留下的 failed/partial。
    """
    db.expire_all()
    item = get_work_item(db, work_item_id)
    executions = list(db.scalars(select(IntakeCommandExecution).join(IntakeChangeSet).where(
        IntakeChangeSet.work_item_id == work_item_id)))
    change_sets = {cs.id: cs for cs in db.scalars(select(IntakeChangeSet).where(
        IntakeChangeSet.work_item_id == work_item_id))}
    candidates = list(db.scalars(select(IntakeCandidate).where(
        IntakeCandidate.work_item_id == work_item_id)))

    def _is_current_version(e: IntakeCommandExecution) -> bool:
        cs = change_sets.get(e.change_set_id)
        cand = next((c for c in candidates if c.id == cs.candidate_id), None) if cs else None
        return cand is not None and cs.candidate_version == cand.version

    valid = [e for e in executions if _is_current_version(e)]
    completed = sum(1 for e in valid if e.status == "completed")
    pending_left = any(c.status == "pending_review" for c in candidates)
    if any(e.status == "executing" for e in valid):
        # BUG-289：仍有命令处于 executing（如租约未到期的中断执行，被执行器按设计
        # 跳过）时，任务必须保持 executing——判成 failed 会让前端“恢复执行”入口
        # 消失，而该命令又不是 failed、没有重试按钮；租约过期后无从恢复。
        item.status = "executing"
    elif valid and completed == len(valid) and not pending_left:
        item.status = "completed"
    elif completed == 0:
        item.status = "failed"
    else:
        item.status = "partial"
    db.commit()
    return item.status


def _assert_executor_authorized(db: Session, member_id: int | None) -> None:
    """BUG-256：核验执行者仍是有效 Owner（未停用、角色未撤）。member_id 为 None
    （内部/系统调用）不做额外约束，保持既有行为兼容。"""
    if member_id is None:
        return
    db.expire_all()  # 强制读最新快照：批内停用必须即时可见
    member = db.get(Member, member_id)
    if member is None or member.disabled_at is not None or member.role != "owner":
        raise WorkflowAuthError(
            "执行者已停用或失去 Owner 权限，命令执行被阻断；请重新授权后再执行")


def _claim_execution_lease(db: Session, execution: IntakeCommandExecution,
                           *, lease_owner: str, now: datetime) -> bool:
    """BUG-253①：原子条件领取——仅当命令可领取（pending/failed/outcome_unknown，
    或 executing 且租约已过期）时更新租约；并发抢占只有一方 rowcount=1。"""
    claimable = or_(
        IntakeCommandExecution.status.in_(("pending", "failed", "outcome_unknown")),
        (IntakeCommandExecution.status == "executing")
        & (IntakeCommandExecution.lease_expires_at < now),
    )
    result = db.execute(
        update(IntakeCommandExecution).execution_options(synchronize_session=False)
        .where(IntakeCommandExecution.id == execution.id, claimable)
        .values(
            lease_owner=lease_owner,
            lease_epoch=IntakeCommandExecution.lease_epoch + 1,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            status="executing",
            attempts=IntakeCommandExecution.attempts + 1,
        ))
    db.commit()
    return result.rowcount == 1


def _lease_conditions(execution_id: int, lease_owner: str | None,
                      lease_epoch: int, *, expired: bool = False) -> list:
    """所有租约状态写入共用归属条件，恢复也不得修改他人的新代次。"""
    conditions = [
        IntakeCommandExecution.id == execution_id,
        IntakeCommandExecution.lease_owner == lease_owner,
        IntakeCommandExecution.lease_epoch == lease_epoch,
        IntakeCommandExecution.status == "executing",
    ]
    if expired:
        conditions.append(IntakeCommandExecution.lease_expires_at <= _now())
    return conditions


def _release_lease_on_abort(db: Session, execution_id: int, lease_owner: str,
                            lease_epoch: int) -> None:
    """授权中止后仅释放自己的租约代次。"""
    db.rollback()
    db.execute(
        update(IntakeCommandExecution).execution_options(synchronize_session=False)
        .where(*_lease_conditions(execution_id, lease_owner, lease_epoch))
        .values(status="pending", lease_owner=None, lease_expires_at=None))
    db.commit()


def _fail_execution(db: Session, execution_id: int, change_set_id: int,
                    lease_owner: str | None, lease_epoch: int, *,
                    error_code: str, error: str, expired: bool = False) -> bool:
    """失败与 ChangeSet 一起提交，抢占失败不改变任何关联对象。"""
    db.rollback()
    changed = db.execute(
        update(IntakeCommandExecution).execution_options(synchronize_session=False)
        .where(*_lease_conditions(execution_id, lease_owner, lease_epoch, expired=expired))
        .values(status="failed", error_code=error_code, error=error,
                lease_owner=None, lease_expires_at=None))
    if changed.rowcount != 1:
        db.rollback()
        return False
    db.execute(
        update(IntakeChangeSet).execution_options(synchronize_session=False)
        .where(IntakeChangeSet.id == change_set_id, IntakeChangeSet.status == "pending")
        .values(status="failed"))
    db.commit()
    return True


def _confirmation_snapshot(db: Session, execution: IntakeCommandExecution):
    """正常执行与恢复共用版本、资源和参数摘要校验。"""
    change_set = db.get(IntakeChangeSet, execution.change_set_id)
    cand = db.get(IntakeCandidate, change_set.candidate_id)
    photo_ids = _json_loads(cand.photo_ids, [])
    photos = list(db.scalars(select(IntakePhoto).where(
        IntakePhoto.work_item_id == cand.work_item_id,
        IntakePhoto.photo_id.in_(photo_ids))))
    current = (cand.version == change_set.candidate_version
               and _hash_params(_command_params(cand, execution.command_type, photos))
               == execution.params_hash)
    return change_set, cand, current


def _bind_snapshot_or_fail(db: Session, execution: IntakeCommandExecution,
                           lease_owner: str) -> tuple[IntakeChangeSet, IntakeCandidate] | None:
    change_set, cand, current = _confirmation_snapshot(db, execution)
    if current:
        return change_set, cand
    _fail_execution(
        db, execution.id, change_set.id, lease_owner, execution.lease_epoch,
        error_code="stale_confirmation",
        error="确认已过期：候选在确认后被修改，旧命令拒绝执行；请重新确认生成新命令")
    return None


def _store_owned_receipt(db: Session, execution: IntakeCommandExecution, *,
                         lease_owner: str, lease_epoch: int, receipt: dict,
                         requester_member_id: int | None) -> None:
    """在业务事务内写回执；授权/确认/租约失败一起回滚业务副作用。"""
    db.flush()  # expire_all 不得丢掉已暂存的封面、副本、购买修改
    _assert_executor_authorized(db, requester_member_id)
    change_set, cand, current = _confirmation_snapshot(db, execution)
    if not current:
        raise WorkflowError("确认已过期：执行期间候选被修改，请重新确认")
    committed = db.execute(
        update(IntakeCommandExecution).execution_options(synchronize_session=False)
        .where(*_lease_conditions(execution.id, lease_owner, lease_epoch))
        .values(status="completed", book_id=receipt.get("book_id"),
                result=json.dumps(receipt, ensure_ascii=False), error_code=None, error=None,
                completed_at=_now(), lease_owner=None, lease_expires_at=None))
    if committed.rowcount != 1:
        raise WorkflowLeaseLostError("执行租约已被接管，当前业务写入已取消")
    cand.status = "executed"
    change_set.status = "executed"


def run_command_execution(db: Session, execution: IntakeCommandExecution, *,
                          requester_member_id: int | None = None) -> IntakeCommandExecution:
    """单命令执行（幂等 + 原子租约领取 + 崩溃恢复 + 授权核验）。"""
    _assert_executor_authorized(db, requester_member_id)
    db.refresh(execution)  # 旧快照不能把他人新租约当作已过期
    if execution.status == "completed":
        return execution  # 幂等重放：返回既有回执
    if execution.status == "superseded":
        return execution  # BUG-267：旧版本命令已被新版本取代，不重放、不重试
    if execution.status == "executing":
        _recover_if_lease_expired(db, execution, requester_member_id=requester_member_id)
        db.refresh(execution)
        if execution.status in ("completed", "failed"):
            return execution  # 恢复已完成或明确拒绝，不在同次调用再领取

    now = _now()
    lease_until = _as_aware(execution.lease_expires_at)
    if (lease_until is not None and lease_until > now and execution.status == "executing"):
        return execution  # 他人持有有效租约，跳过（不接管活跃租约）

    _assert_executor_authorized(db, requester_member_id)  # 副作用前核验（BUG-256）

    lease_owner = uuid.uuid4().hex
    if not _claim_execution_lease(db, execution, lease_owner=lease_owner, now=now):
        # 抢占失败：他人已领取/完成，不得继续执行（BUG-253①）
        db.rollback()
        return db.get(IntakeCommandExecution, execution.id)

    # 租约核验：强制刷新后确认租约仍属于本次执行者（旧租约不得提交）
    db.expire(execution)
    current = db.get(IntakeCommandExecution, execution.id)
    if current.lease_owner != lease_owner or current.status != "executing":
        db.rollback()
        return db.get(IntakeCommandExecution, execution.id)
    claimed_epoch = current.lease_epoch

    bound = _bind_snapshot_or_fail(db, execution, lease_owner)  # BUG-252
    if bound is None:
        return db.get(IntakeCommandExecution, execution.id)
    change_set, cand = bound

    def finalize_receipt(receipt: dict) -> None:
        _store_owned_receipt(db, execution, lease_owner=lease_owner,
                             lease_epoch=claimed_epoch, receipt=receipt,
                             requester_member_id=requester_member_id)

    try:
        if execution.command_type == COMMAND_CREATE_BOOK:
            _execute_create_book(db, cand, finalize=finalize_receipt)
        else:
            _execute_link_photos(db, cand, finalize=finalize_receipt)
        # 提交后停用不会撤销已完成命令，但必须阻断本次批次后续执行。
        _assert_executor_authorized(db, requester_member_id)
    except WorkflowAuthError:
        _release_lease_on_abort(db, execution.id, lease_owner, claimed_epoch)
        raise
    except WorkflowLeaseLostError:
        db.rollback()
        return db.get(IntakeCommandExecution, execution.id)
    except (WorkflowError, ValueError) as exc:
        _fail_execution(db, execution.id, change_set.id, lease_owner, claimed_epoch,
                        error_code=_classify_error(str(exc)), error=str(exc))
        return db.get(IntakeCommandExecution, execution.id)
    except Exception as exc:  # noqa: BLE001 —— 记录后由调用方重试/核对
        _fail_execution(db, execution.id, change_set.id, lease_owner, claimed_epoch,
                        error_code="unknown", error=f"{exc.__class__.__name__}: {exc}")
        return db.get(IntakeCommandExecution, execution.id)
    return db.get(IntakeCommandExecution, execution.id)


def _classify_error(message: str) -> str:
    if "ISBN 归属冲突" in message:
        return "isbn_ownership_conflict"
    if "确认已过期" in message:
        return "stale_confirmation"
    if "ISBN" in message:
        return "invalid_isbn"
    if "不存在" in message:
        return "not_found"
    return "bad_request"


def _cover_photo(db: Session, cand: IntakeCandidate) -> IntakePhoto | None:
    photo_ids = _json_loads(cand.photo_ids, [])
    photos = list(db.scalars(select(IntakePhoto).where(
        IntakePhoto.work_item_id == cand.work_item_id,
        IntakePhoto.photo_id.in_(photo_ids))))
    covers = [p for p in photos if p.role == "cover"]
    pool = covers or photos
    return pool[0] if pool else None


def _execute_create_book(db: Session, cand: IntakeCandidate, *,
                         finalize: Callable[[dict], None] | None = None) -> dict:
    """经领域服务创建书目（prefer_confirmed：确认字段不被元数据覆盖）。

    BUG-251：确认副题贯通——subtitle 随 IntakeInput 传递，并在确认字段集合中
    声明，无元数据/冲突元数据下均保留。"""
    photo = _cover_photo(db, cand)
    authors = _json_loads(cand.authors, [])
    # BUG-276：确认字段集合保留显式空值——空字符串/空列表是合法确认值（清空
    # 语义，契约：intake prefer_confirmed 侧据此不拿元数据回填）；None 表示
    # 未提供，不阻断元数据补全。authors 列存储 "[]" 与 NULL 区分清空/未提供。
    confirmed = [f for f, v in (("title", cand.title), ("subtitle", cand.subtitle),
                                ("authors", authors if cand.authors is not None else None),
                                ("isbn", cand.isbn)) if v is not None]
    photo_ids = _json_loads(cand.photo_ids, [])

    def receipt_for(result: IntakeResult) -> dict:
        return {"book_id": result.book.id, "action": result.action,
                "message": result.message, "matched_source": result.matched_source,
                "isbn_detected": result.isbn_detected,
                "warnings": [w.to_dict() for w in (result.warnings or [])],
                "photo_ids": photo_ids}

    result = intake_book(db, IntakeInput(
        isbn=cand.isbn, title=cand.title, subtitle=cand.subtitle,
        authors=(authors if cand.authors is not None else None),
        image_path=photo_abs_path(photo) if photo else None,
        field_policy="prefer_confirmed", confirmed_fields=confirmed,
    ), finalize=(lambda result: finalize(receipt_for(result))) if finalize is not None else None)
    return receipt_for(result)


def _execute_link_photos(db: Session, cand: IntakeCandidate, *,
                         finalize: Callable[[dict], None] | None = None) -> dict:
    """确认已存在书：默认仅建立照片关联；缺封面时回填首张封面照（不改正文书字段）。

    BUG-254：执行环节最终把关——ISBN 归属冲突未显式解决（force_link）一律阻断，
    不因 match_book_id 自动放行。"""
    book = db.get(Book, cand.match_book_id)
    if book is None:
        raise WorkflowError(f"目标书目 {cand.match_book_id} 不存在（可能已被删除），请重新核对")
    conflict = _isbn_conflict_for_candidate(db, cand, book)
    if conflict is not None:
        stored = _json_loads(cand.conflicts, None)
        resolved = (isinstance(stored, dict) and stored.get("resolved") == "force_link"
                    and stored.get("book_id") == book.id)
        if not resolved:
            raise WorkflowError(
                f"ISBN 归属冲突：《{book.title}》与候选《{cand.title or '（无书名）'}》明显不一致，"
                "禁止自动关联；须人工显式解决（force_link）或修改候选后重新确认")
    backfilled = False
    saved = None
    if not book.cover_path:
        photo = _cover_photo(db, cand)
        if photo:
            saved = save_uploaded_image(photo_abs_path(photo), target_name=photo.sha256[:16])
            if saved:
                book.cover_path = saved
                backfilled = True
    receipt = {
        "book_id": book.id, "action": "exists", "linked": True,
        "cover_backfilled": backfilled,
        "message": f"已关联《{book.title}》" + ("，已补充封面" if backfilled else ""),
        "warnings": [], "photo_ids": _json_loads(cand.photo_ids, []),
    }
    try:
        db.flush()
        if finalize is not None:
            finalize(receipt)
        db.commit()
    except Exception:
        db.rollback()
        _cleanup_orphan_cover(saved)
        raise
    return receipt


def _recover_if_lease_expired(db: Session, execution: IntakeCommandExecution, *,
                              requester_member_id: int | None = None) -> None:
    """恢复必须重验确认、ISBN 归属、授权，并按观察到的租约代次提交。"""
    _assert_executor_authorized(db, requester_member_id)
    db.refresh(execution)
    expires = _as_aware(execution.lease_expires_at)
    if execution.status != "executing" or expires is None or expires > _now():
        return
    execution_id, lease_owner, lease_epoch = execution.id, execution.lease_owner, execution.lease_epoch
    change_set, cand, current = _confirmation_snapshot(db, execution)
    change_set_id = change_set.id
    if not current:
        _fail_execution(db, execution_id, change_set_id, lease_owner, lease_epoch,
                        error_code="stale_confirmation", expired=True,
                        error="确认已过期：候选在确认后被修改，恢复拒绝补回执；请重新确认")
        return
    if execution.command_type == COMMAND_CREATE_BOOK:
        found = _find_existing_book(
            db, isbn=cand.isbn, title=cand.title, authors=_json_loads(cand.authors, []))
        if found is None:
            return  # 查无提交结果，之后仍需原子领取方可执行
        _assert_executor_authorized(db, requester_member_id)
        # 查重可能等待外部状态变化；提交前重新读取确认，不能用旧候选补回执。
        db.refresh(cand)
        change_set, cand, current = _confirmation_snapshot(db, execution)
        if not current:
            _fail_execution(db, execution_id, change_set_id, lease_owner, lease_epoch,
                            error_code="stale_confirmation", expired=True,
                            error="确认已过期：恢复期间候选被修改，请重新确认")
            return
        db.refresh(found)
        conflict = _isbn_conflict_for_candidate(db, cand, found)
        if conflict is not None:
            _fail_execution(db, execution_id, change_set_id, lease_owner, lease_epoch,
                            error_code="isbn_ownership_conflict", expired=True,
                            error=conflict["message"])
            return
        receipt = {"book_id": found.id, "action": "exists",
                   "message": f"恢复：书目在崩溃前已创建/已存在《{found.title}》",
                   "warnings": [], "recovered": True,
                   "photo_ids": _json_loads(cand.photo_ids, [])}
        recovered = db.execute(
            update(IntakeCommandExecution).execution_options(synchronize_session=False)
            .where(*_lease_conditions(execution_id, lease_owner, lease_epoch, expired=True))
            .values(status="completed", book_id=found.id,
                    result=json.dumps(receipt, ensure_ascii=False), error=None, error_code=None,
                    completed_at=_now(), lease_owner=None, lease_expires_at=None))
        if recovered.rowcount != 1:
            db.rollback()
            return
        cand.status = "executed"
        change_set.status = "executed"
        db.commit()
    else:
        # 关联命令仍须重新领取，并在正式执行中核对 force_link/目标资源。
        _assert_executor_authorized(db, requester_member_id)
        db.execute(
            update(IntakeCommandExecution).execution_options(synchronize_session=False)
            .where(*_lease_conditions(execution_id, lease_owner, lease_epoch, expired=True))
            .values(status="pending", lease_owner=None, lease_expires_at=None))
        db.commit()


def retry_execution(db: Session, execution_id: int, *,
                    requester_member_id: int | None = None) -> IntakeCommandExecution:
    """重试失败/未知的命令；completed 幂等返回既有回执（重放不产生副作用）。

    BUG-283：单命令重试改变了命令/候选终态，重试后按当前有效版本重算所属
    任务汇总状态——否则重试成功后任务仍停在 failed/partial，与命令 completed
    矛盾，且 completed 锁定前无法再整批执行纠正。
    """
    execution = db.get(IntakeCommandExecution, execution_id)
    if execution is None:
        raise WorkflowError(f"执行 {execution_id} 不存在")
    if execution.status in ("completed", "superseded"):
        return execution  # 幂等：重放/旧版本均返回既有状态，不产生副作用
    execution = run_command_execution(db, execution, requester_member_id=requester_member_id)
    change_set = db.get(IntakeChangeSet, execution.change_set_id)
    if change_set is not None:
        _recompute_work_item_status(db, change_set.work_item_id)
    return execution


# ── 序列化 ──

def execution_to_dict(e: IntakeCommandExecution) -> dict:
    return {
        "id": e.id, "command_type": e.command_type, "status": e.status,
        "attempts": e.attempts, "book_id": e.book_id,
        "result": _json_loads(e.result, None), "error_code": e.error_code,
        "error": e.error, "change_set_id": e.change_set_id,
        "requested_by_member_id": e.requested_by_member_id,
        "completed_at": e.completed_at.isoformat() if e.completed_at else None,
    }


def candidate_to_dict(c: IntakeCandidate) -> dict:
    return {
        "id": c.id, "work_item_id": c.work_item_id, "version": c.version,
        "status": c.status, "title": c.title, "subtitle": c.subtitle,
        "authors": _json_loads(c.authors, []),
        "isbn": c.isbn,
        "evidence": _json_loads(c.evidence, {}),
        "conflicts": _json_loads(c.conflicts, None),
        "photo_ids": _json_loads(c.photo_ids, []),
        "match_book_id": c.match_book_id,
        "match_diff": _json_loads(c.match_diff, None),
    }


def photo_to_dict(p: IntakePhoto) -> dict:
    return {
        "id": p.id, "photo_id": p.photo_id, "sha256": p.sha256, "role": p.role,
        "size_bytes": p.size_bytes, "warnings": _json_loads(p.warnings, []),
        "image_url": f"/intake-workflow/photos/{p.id}/image",
    }


def work_item_to_dict(item: IntakeWorkItem, *, photos, candidates, executions) -> dict:
    return {
        "id": item.id, "title": item.title, "status": item.status, "note": item.note,
        "created_by_member_id": item.created_by_member_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "photos": [photo_to_dict(p) for p in photos],
        "candidates": [candidate_to_dict(c) for c in candidates],
        "executions": [execution_to_dict(e) for e in executions],
    }
