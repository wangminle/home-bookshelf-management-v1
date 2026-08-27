"""MCP 测试默认契约版本钉住 v1（CHK-100 残余风险收口）。

pydantic Settings 会读取 backend/.env；若本机 .env 设置
MCP_CONTRACT_VERSION=v2，默认按 v1 断言的描述符/线缆用例会被意外击穿。
所有 MCP 测试默认运行在冻结的 v1 契约下；v2 专属用例在测试体内
自行 monkeypatch 覆盖为 v2（同函数级 monkeypatch 实例，后写者优先）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pin_mcp_contract_v1(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "mcp_contract_version", "v1")
