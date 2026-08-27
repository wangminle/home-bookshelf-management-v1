# MCP 只读试点威胁模型评审记录（CHK-088 / WBS-MCP-9 Task 9.1）

> 评审日期：2026-08-26
> 评审范围：MCP 只读试点（`/mcp`，`bookshelf_search_books` / `bookshelf_get_book`，
> 专用试点 Grant + `books:read` + `data_scope=household_shared`）

## 评审人

- 代码实现与自动化验证：本项目开发代理（CHK-088 会话）
- 交叉核验：官方 mcp SDK 2.1.0 client 实机 conformance（18/18）
- 后续验证（2026-08-27）：Claude Code / Inspector 协议代际抓帧；
  OpenCode + 官方 SDK 在 Owner 授权的临时窗口访问真实部署数据。
- 说明：无独立外部评审人；本记录为内部技术评审留痕，不等于
  跨客户端通用发布签署（见末尾结论）。

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
   未来协议演进需人工跟进（`mcp==2.1.0` 已在
   `backend/requirements.txt` 作为运行时依赖锁定）；
2. **无 Session/SSE 语义**：仅支持无状态 POST；要求 Session 的客户端不兼容（判据 no-go 第 3 条）；
3. **进程内限流/抑制表**：多实例部署时按实例各自计数（与 rate_limit/security_audit 同边界，
   基线 §12.2 已注明）；生产多副本须改用共享存储或网关限流；
4. ~~**`structuredError` 不在 SDK 2.1.0 模型内**~~（2026-08-27 收口）：
   稳定 `code/retryable/request_id` 已加入
   `result._meta["io.homebookshelf/error"]`（`ResultMetaObject`
   extra="allow"，官方 SDK 原样可读并经 conformance 实机验证）；
   `structuredError` 顶层扩展保留作向后兼容，`isError` 语义不变；
5. **封面 Resource 未实机**：默认关闭，属可选扩展（第三期范围）。
6. ~~**工具输入未完全封闭**~~（2026-08-27 BUG-230 收口）：
   `inputSchema` 已 `additionalProperties=false` + `anyOf` 至少一个
   筛选条件；运行时在数据访问前拒绝 `member_id` 等一切未知键并返回
   `PARAM_INVALID` + 审计，不再静默忽略（测试 89 项 + conformance
   3c 用例覆盖）。
7. **跨客户端协议代际未完全收口**：Claude Code 官方升级至 2.1.247 后
   wiretap 首帧仍 Legacy `initialize`+`2025-11-25`（2026-08-27 P0A 复测）；
   Inspector 2.4.0 显式 `protocolEra=modern` 已实机全通过，其"默认
   Legacy"仅是配置问题。该风险现仅影响 Claude Code 单一客户端的兼容
   范围，是否启动 WBS-MCP-11 由 Owner 决策。

## 签署意见（发布门禁，2026-08-27 更新）

- Owner 授权的一次性真实数据验收已完成：OpenCode + 官方 SDK
  完成 9 项闭环，没有功能性异常或隐私哨兵命中；测后已撤销凭据、
  恢复 `MCP_ENABLED=false`，`/mcp` 再次 404。
- 当前可签署：**现代协议核心链路 go**，包括真实部署的 Bearer
  认证、试点 Grant、输出白名单、防枚举和撤销即失效。
- 当前仍不能签署：**标准跨客户端通用发布**。WBS-MCP-P0A 复测
  （2026-08-27）：Inspector 2.4.0 显式 modern 全通过，但 Claude Code
  官方升级至 2.1.247 后首帧仍 Legacy `initialize`，单一客户端兼容
  门禁未过；是否为其启动 WBS-MCP-11 属 Owner 决策。
- `MCP_ENABLED` 继续默认 `false`；WBS-MCP-P0A 已执行完毕，只在
  Owner 确认 Claude Code 为必需客户端且仍只能 Legacy 时，才启动
  条件式 WBS-MCP-11 双代际兼容。

## 回滚与发布演练结论（WBS-MCP-9 Task 9.4）

自动化已验证（`scripts/mcp-sdk-conformance.py` + `backend/tests/mcp/`）：
- 关闭 `MCP_ENABLED` → `/mcp` 404（默认关闭即已验证）；
- 撤销试点 Grant → 下一请求 401（握手即被拒）；
- REST 与两只读工具数据不受 MCP 开关影响（REST/MCP 一致性用例通过）；
- 回滚 = 关闭 `MCP_ENABLED`（无需数据迁移，`agent_grants`/`agent_tokens` 为独立表）。

2026-08-27 真实部署验收再次确认：撤销测试 Grant 后下一请求
立即失败；关闭 MCP 后端口 18009 的 `/mcp` 恢复 404。
