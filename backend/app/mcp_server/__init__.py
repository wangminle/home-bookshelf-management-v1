"""MCP 只读试点 server 包（并行轨，MCP 设计 §20-28 WBS-MCP-3/4/5/7 核心）。

默认关闭（MCP_ENABLED=false → /mcp 404）。无状态 JSON-RPC 2.0 over HTTP：
- 首期协议 allowlist 仅 2026-07-28；不依赖 Mcp-Session-Id；
- 只接受 Agent Bearer Token（Cookie/渠道头/匿名一律 401）；
- 复用 Agent Grant 校验（撤销/过期下一请求生效）、Catalog Read Model、
  共享限流与共享安全审计——不建立第二套权限/状态系统；
- 两个核心只读工具：bookshelf_search_books / bookshelf_get_book；
  输出不含封面 URL、文件路径或任何 L3/L4 数据（MCP 设计 §6.1/§9.2）。
"""
