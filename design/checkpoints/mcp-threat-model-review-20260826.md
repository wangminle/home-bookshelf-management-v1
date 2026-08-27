# MCP 只读试点威胁模型评审记录（CHK-088 / WBS-MCP-9 Task 9.1）

> 评审日期：2026-08-26
> 评审范围：MCP 只读试点（`/mcp`，`bookshelf_search_books` / `bookshelf_get_book`，
> 专用试点 Grant + `books:read` + `data_scope=household_shared`）

## 评审人

- 代码实现与自动化验证：本项目开发代理（CHK-088 会话）
- 交叉核验：官方 mcp SDK 2.1.0 client 实机 conformance（18/18）
- 说明：无独立外部评审人；本记录为内部技术评审留痕，**不含"允许真实家庭数据试点"的
  签署意见**（见末尾结论）。

## 威胁面与防护结论

| 威胁 | 防护措施 | 结论 |
| --- | --- | --- |
| 未授权访问 | 默认 `MCP_ENABLED=false`（404）；仅 Bearer Token；专用试点 Grant 硬门禁 | 已落地，测试锁定 |
| DNS Rebinding | `Host` allowlist（默认仅回环精确值；非回环须显式配置，否则 421） | 已落地 |
| 跨站/恶意 Origin | `Origin` 精确匹配（非浏览器客户端可不带；带则须命中 `MCP_TRUSTED_ORIGINS`，无通配符） | 已落地 |
| 来源网络不可信 | `MCP_TRUSTED_CIDRS`（默认空=仅回环）+ 可信代理 XFF 右值法 + `MCP_REQUIRE_HTTPS`（回环豁免） | 已落地 |
| 协议版本降级/伪造 | `MCP-Protocol-Version` allowlist（`server/discover` 豁免必填=协商语义，其余方法必填） | 已落地 |
| 网关头路由绕过 | `Mcp-Method`/`Mcp-Name` 与请求体一致性校验（不符 400） | 已落地 |
| 帧畸形/放大 | `jsonrpc`/`params` 对象/`_meta` 对象逐项校验；method 长度 ≤128；请求体 1 MiB（413） | 已落地 |
| 空条件遍历全库 | `search_books` 至少一个 strip 后非空条件，否则 `QUERY_REQUIRED` | 已落地 |
| 分页滥用 | `limit` 夹取 1–20；游标 HMAC 绑定页码+条件摘要 | 已落地 |
| 限流绕过（未知 method 换 bucket） | 两层额度：全局 `mcp:req:{client}:{grant}` 先于方法 allowlist；工具级 `mcp:tool:{tool}:{client}:{grant}` | 已落地 |
| 越权（旧 Grant 祖父化） | Grant `version` + Token `grant_version` 绑定；范围变更递增版本并吊销旧 Token | 已落地 |
| 数据泄露（L3/路径/封面） | 输出白名单 13 字段；L3 哨兵零命中；无封面 URL/文件路径；结构化输出经 Schema 校验 | 已落地 |
| 审计缺失 | 全部调用进共享审计；真实数据 allow 逐次（suppress=0）；allow 审计失败 503 fail-closed | 已落地 |
| 数据库异常裸 500 | SQLAlchemyError → DB_BUSY（retryable）+ 审计；兜底 -32603 | 已落地 |
| 密钥弱/复用 | 游标签名密钥 ≥32 字符且禁复用渠道密钥/setup_token（否则启动 500） | 已落地 |

## 残余风险（已知、未消除）

1. **传输层为自建实现**：经官方 SDK client 验证 wire 兼容，但未使用官方 SDK server 端，
   未来协议演进需人工跟进（`mcp==2.1.0` 已作为 dev 依赖锁定）；
2. **无 Session/SSE 语义**：仅支持无状态 POST；要求 Session 的客户端不兼容（判据 no-go 第 3 条）；
3. **进程内限流/抑制表**：多实例部署时按实例各自计数（与 rate_limit/security_audit 同边界，
   基线 §12.2 已注明）；生产多副本须改用共享存储或网关限流；
4. **`structuredError` 不在 SDK 2.1.0 模型内**：官方客户端经 `is_error`+文本透出错误码，
   结构化错误字段对非 SDK 客户端可用但对 SDK client 不可见；
5. **封面 Resource 未实机**：默认关闭，属可选扩展（第三期范围）。

## 签署意见（发布门禁）

- **不允许开启真实家庭数据试点**。理由：MCP Inspector 与第二个目标客户端
  （Claude Code）实机验证未完成，不满足 WBS-MCP-P0 双客户端 go 判据；
- 当前可宣称：**核心只读代码级试点完成 + 官方 SDK client 协议兼容已验证**；
- `MCP_ENABLED` 保持默认 `false`；官方 SDK、Inspector、双客户端实机全部完成后，
  再启动正式签署流程。

## 回滚与发布演练结论（WBS-MCP-9 Task 9.4）

自动化已验证（`scripts/mcp-sdk-conformance.py` + `backend/tests/mcp/`）：
- 关闭 `MCP_ENABLED` → `/mcp` 404（默认关闭即已验证）；
- 撤销试点 Grant → 下一请求 401（握手即被拒）；
- REST 与两只读工具数据不受 MCP 开关影响（REST/MCP 一致性用例通过）；
- 回滚 = 关闭 `MCP_ENABLED`（无需数据迁移，`agent_grants`/`agent_tokens` 为独立表）。
