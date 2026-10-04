"""拍照批量入库协作对象（PLN-012 M3 / BI-12，AI-native 规划最小切片）。

对象关系：WorkItem（一次批量任务）→ Photo（照片，稳定 photo_id + SHA-256）→
Candidate（候选书目，版本化）→ Decision（Owner 确认，绑定候选版本）→
ChangeSet（待执行变更）→ CommandExecution（幂等执行回执）。

执行主体（BI-12 冻结）：**后端进程内、由 Owner Web 会话发起的工作流执行器**。
不转借 Owner Cookie；每次执行经 require_owner 重新鉴权（停用/撤权即拒绝）；
租约（lease_owner/lease_expires_at）防并发接管，旧租约执行者不得提交。
书目写入只经领域服务 intake_book / 既有授权校验路径，模型与候选永不直写。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampUpdateMixin


class IntakeWorkItem(TimestampUpdateMixin, Base):
    __tablename__ = "intake_work_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="", server_default="", nullable=False)
    # draft（上传中）→ in_review（核对中）→ confirmed（已确认待执行）→
    # executing → completed / partial（部分失败）/ failed / cancelled
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=None, server_default=text("(CURRENT_TIMESTAMP)"), nullable=False
    )


class IntakePhoto(TimestampUpdateMixin, Base):
    __tablename__ = "intake_photos"
    __table_args__ = (UniqueConstraint("work_item_id", "photo_id", name="uq_intake_photo_pid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("intake_work_items.id"), nullable=False, index=True)
    # 客户端稳定照片 ID（清单 photo_id 语义）；照片不是图书计数单位
    photo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 相对 data_dir 的路径（intake_photos/<work_item_id>/<photo_id>.<ext>）
    file_path: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="")
    # cover | barcode | other | unknown（契约 §4.3）
    role: Mapped[str] = mapped_column(String(10), default="unknown", server_default="unknown", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # 识别阶段的结构化警告 JSON 数组（契约 §2 结构）
    warnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=None, server_default=text("(CURRENT_TIMESTAMP)"), nullable=False
    )


class IntakeCandidate(TimestampUpdateMixin, Base):
    __tablename__ = "intake_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("intake_work_items.id"), nullable=False, index=True)
    # 版本化确认（BI-15）：字段修改 +1；确认绑定版本，过期确认不得执行
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    # pending_review | confirmed | rejected | executed
    status: Mapped[str] = mapped_column(String(20), default="pending_review", server_default="pending_review", nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subtitle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    authors: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组
    isbn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 逐字段证据 JSON：{"title": {"source": "vision|user", "confidence": 0.9, "readable": true}, ...}
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 与外部元数据/既有书的冲突 JSON（不静默覆盖，核对可见）
    conflicts: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联照片（photo_id 列表 JSON；多图 → 一个候选 → 一个最终图书，契约 §4.3）
    photo_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    # BI-14 匹配预览：命中的既有书（只读预览，不写书目）
    match_book_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_diff: Mapped[str | None] = mapped_column(Text, nullable=True)  # 逐字段差异 JSON
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=None, server_default=text("(CURRENT_TIMESTAMP)"), nullable=False
    )


class IntakeChangeSet(TimestampUpdateMixin, Base):
    __tablename__ = "intake_change_sets"
    __table_args__ = (UniqueConstraint("candidate_id", "candidate_version", name="uq_intake_cs_candidate_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("intake_work_items.id"), nullable=False, index=True)
    candidate_id: Mapped[int] = mapped_column(Integer, ForeignKey("intake_candidates.id"), nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # pending → executed / failed / partial
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", nullable=False)
    created_by_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=None, server_default=text("(CURRENT_TIMESTAMP)"), nullable=False
    )


class IntakeDecision(TimestampUpdateMixin, Base):
    __tablename__ = "intake_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("intake_work_items.id"), nullable=False, index=True)
    change_set_id: Mapped[int] = mapped_column(Integer, ForeignKey("intake_change_sets.id"), nullable=False)
    candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_by_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # confirm | reject
    action: Mapped[str] = mapped_column(String(10), default="confirm", server_default="confirm", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=None, server_default=text("(CURRENT_TIMESTAMP)"), nullable=False
    )


class IntakeCommandExecution(TimestampUpdateMixin, Base):
    __tablename__ = "intake_command_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 幂等键（BI-15）：命令类型 + 候选版本 + 目标 + 图片哈希 + 业务参数的规范化摘要
    command_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    change_set_id: Mapped[int] = mapped_column(Integer, ForeignKey("intake_change_sets.id"), nullable=False, index=True)
    # intake.create_book | intake.link_photos
    command_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # 同键不同参数拒绝（契约 §4.5）
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # pending | executing | completed | failed | outcome_unknown
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # 租约（BI-12）：到期允许接管；旧租约执行者提交时被拒绝
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # 执行回执：book_id / action / warnings / outcome / 错误类别（契约 §4.3）
    book_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=None, server_default=text("(CURRENT_TIMESTAMP)"), nullable=False
    )
