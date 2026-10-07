"""LOC-06：位置结构（房间/书架/层格）的请求与响应模型。

契约：design/plans/实体书架与图书位置管理-功能分析和设计-20261005.md §6/§9、
design/checkpoints/实体书架位置管理-M0契约冻结-20261006.md（LOC-02）。
编号 code 只是可读标签；关联一律使用稳定 ID，ID 不随改名或排序改变。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── 请求 ──


class RoomCreate(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    sort_order: int = 0


class RoomUpdate(BaseModel):
    """修改/归档/恢复（archived=true 归档、false 恢复）；version 为期望版本。"""

    version: int
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    sort_order: int | None = None
    archived: bool | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)


class CellInput(BaseModel):
    code: str | None = Field(default=None, max_length=50)
    label: str | None = Field(default=None, max_length=50)
    sort_order: int | None = None


class LayerInput(BaseModel):
    label: str | None = Field(default=None, max_length=50)
    sort_order: int | None = None
    # 缺省按「每层先建 1 格」默认值建 1 格
    cells: list[CellInput] | None = None


class ShelfCreate(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=100)
    room_id: int
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    position_note: str | None = Field(default=None, max_length=200)
    sort_order: int = 0
    # 缺省建 1 层 1 格
    layers: list[LayerInput] | None = None


class ShelfUpdate(BaseModel):
    version: int
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    position_note: str | None = Field(default=None, max_length=200)
    sort_order: int | None = None
    archived: bool | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)


class LayoutCell(BaseModel):
    """布局中的格子条目：无 id=新建；有 id=改名/排序/归档/恢复。

    version 提供时按期望版本强校验；未提供时以锁定后重读的当前版本为准。
    """

    id: int | None = None
    version: int | None = None
    code: str | None = Field(default=None, min_length=1, max_length=50)
    label: str | None = Field(default=None, min_length=1, max_length=50)
    sort_order: int | None = None
    archived: bool | None = None


class LayoutLayer(BaseModel):
    """布局中的层条目。cells 缺省=不动该层格子；给出=该层格子的全量表达
    （未列出的现存活动格子将被归档，有副本引用则 409）。"""

    id: int | None = None
    version: int | None = None
    label: str | None = Field(default=None, min_length=1, max_length=50)
    sort_order: int | None = None
    archived: bool | None = None
    cells: list[LayoutCell] | None = None


class LayoutPut(BaseModel):
    """带稳定 ID 的布局变更：layers 是书架层结构的全量表达
    （未列出的现存活动层将被归档，有副本引用则 409）。不允许删行重建。"""

    idempotency_key: str = Field(min_length=1, max_length=100)
    shelf_version: int
    layers: list[LayoutLayer]


# ── 响应 ──


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None = None
    sort_order: int
    version: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ShelfOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    room_id: int
    code: str
    name: str
    position_note: str | None = None
    sort_order: int
    version: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shelf_id: int
    label: str
    sort_order: int
    version: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CellOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shelf_id: int
    layer_id: int
    code: str
    label: str
    sort_order: int
    version: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LayerDetailOut(LayerOut):
    cells: list[CellOut] = []


class ShelfDetailOut(ShelfOut):
    layers: list[LayerDetailOut] = []


# ── 照片（LOC-09） ──


class PhotoUpdate(BaseModel):
    """改标注 / 设主图；version 为期望版本。is_primary=true 时同事务清同架其他主图。"""

    version: int
    caption: str | None = Field(default=None, max_length=200)
    is_primary: bool | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)


class PhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shelf_id: int
    caption: str | None = None
    is_primary: bool
    width: int
    height: int
    mime_type: str
    version: int
    created_at: datetime
    updated_at: datetime


# ── 单册位置与批量移动（LOC-13，设计 §7.2/§9） ──


class PlacementTarget(BaseModel):
    """目标位置：仅到书架（cell_id 缺省）或到格子。"""

    shelf_id: int
    cell_id: int | None = None


class PlacementUpdate(BaseModel):
    """单册分配/移动/清除（PATCH copies placement）。

    - target 非 null：分配/移动到书架或格子；keep_legacy_location 不得出现；
    - target 为 null：清除结构化位置，此时必须显式 keep_legacy_location
      决定保留/清空旧 location 文字（契约修订 LOC-13）；
    - location 字段仅为旧客户端误写守卫：服务端一律拒绝（409），不写入。
    """

    placement_version: int
    target: PlacementTarget | None = None
    keep_legacy_location: bool | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_clear_semantics(self) -> PlacementUpdate:
        if self.target is None and self.keep_legacy_location is None:
            raise ValueError("清除位置（target=null）必须显式指定 keep_legacy_location")
        if self.target is not None and self.keep_legacy_location is not None:
            raise ValueError("设置/移动位置时不接受 keep_legacy_location")
        return self


class PlacementTargetVersioned(PlacementTarget):
    """带期望结构版本的批量移动目标（执行时重验，对应预览时刻）。"""

    shelf_version: int
    cell_version: int | None = None


class PlacementPreviewCopyIn(BaseModel):
    copy_id: int


class PlacementPreviewIn(BaseModel):
    """批量移动预览（dry-run 不落库）：目标 + 副本清单，版本由服务端按当前状态填充。"""

    target: PlacementTarget
    copies: list[PlacementPreviewCopyIn]


class PlacementExecuteCopyIn(BaseModel):
    copy_id: int
    placement_version: int


class PlacementExecuteIn(BaseModel):
    """原子批量移动：重验预览摘要与全部期望版本，任一失效整批拒绝零写入。"""

    idempotency_key: str = Field(min_length=1, max_length=100)
    preview_digest: str = Field(min_length=1, max_length=64)
    target: PlacementTargetVersioned
    copies: list[PlacementExecuteCopyIn]


# ── 存量副本补录（LOC-14，设计 §7.2/§10.1 + 契约修订 LOC-14） ──


class IntakeAssignIn(BaseModel):
    """为已有副本分配位置：副本必须属于条目 book_id；携带期望 placement_version。"""

    copy_id: int
    placement_version: int
    target: PlacementTarget


class IntakeNewCopiesIn(BaseModel):
    """显式确认新增实体册数：数量只来自本字段，绝不按书目/照片数推定（LOC-01 冻结）。

    新副本 copy_type 固定 physical、status 默认 in_shelf；其余属性后续编辑。
    """

    count: int = Field(ge=1)
    owner_member_id: int
    target: PlacementTarget


class IntakeItemIn(BaseModel):
    """单个书目的补录条目：为已有副本分配位置 与/或 确认新增册数，至少其一。"""

    book_id: int
    assign: list[IntakeAssignIn] | None = None
    new_copies: IntakeNewCopiesIn | None = None

    @model_validator(mode="after")
    def _check_nonempty(self) -> IntakeItemIn:
        if not self.assign and self.new_copies is None:
            raise ValueError("补录条目为空：assign 与 new_copies 至少提供其一")
        return self


class IntakeCommitIn(BaseModel):
    """批量补录提交：原子事务——副本创建 + 位置设置 + 幂等回执 + 审计同事务。"""

    idempotency_key: str = Field(min_length=1, max_length=100)
    items: list[IntakeItemIn] = Field(min_length=1)
