"""Catalog Read Model：L1 共享书目的安全读取模型（权限阶段 1）。

Public Catalog（匿名 C 模式）、REST 安全子集与后续 MCP 共用同一读取实现
（MCP 设计 §9.3）；字段白名单见权限基线 §9.3——只暴露脱敏书目字段，
成员、归属、精确位置、阅读、笔记、购买与文件路径永不进入响应。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CatalogBookSummary(BaseModel):
    """书目摘要（列表项）：严格字段白名单，禁止额外字段。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    subtitle: str | None = None
    authors: list[str] = []
    translators: list[str] = []
    publisher: str | None = None
    publish_date: str | None = None
    edition: str | None = None
    language: str | None = None
    page_count: int | None = None
    category: str | None = None
    summary: str | None = None
    cover_thumbnail_url: str | None = None
    public_tags: list[str] = []
    availability_status: str = "unknown"


class CatalogBookDetail(CatalogBookSummary):
    """书目详情：阶段 1 与摘要同字段，独立类型为后续扩展预留。"""


class CatalogSearchPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CatalogBookSummary]
    total: int
    page: int
    page_size: int
    has_more: bool
