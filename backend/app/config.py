from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Home Bookshelf API"
    debug: bool = False
    database_url: str = "sqlite:///./data/bookshelf.db"
    data_dir: Path = Path("./data")
    google_books_api_key: str | None = None
    metadata_user_agent: str = "home-bookshelf/1.0"
    # 非空库时匿名 bind 的可选管理口令（请求头 X-Setup-Token）
    setup_token: str | None = None
    # BUG-132：可选的渠道头 HMAC 共享密钥。配置后，携带 X-Channel/X-External-User-Id
    # 的请求必须附带 X-Channel-Signature = HMAC-SHA256(secret, "{channel}:{external_user_id}")
    # 的十六进制摘要，防止局域网内伪造明文渠道头冒充已绑定成员。
    # 不配置则维持"可信局域网/网关代填头"的既有信任边界。
    channel_signing_secret: str | None = None
    # GitHub #8：可信反向代理列表（逗号分隔的 IP/CIDR，如 "172.18.0.0/16"）。
    # 直接对端在列表内时，loopback 判定按右值法解析 X-Forwarded-For（从右向左
    # 跳过可信代理取首个非可信地址）——用于 lwa/nginx 反代场景（后端看到的
    # 对端是网关 IP）。默认空：不信任任何代理，XFF 视为不可信。
    trusted_proxies: str = ""

    # ── 权限阶段 1：C 模式匿名书架（基线 §4.3/§11.3/§13） ──
    # 匿名目录系统策略：lan_shared（C 模式）/ explicit_public（B 模式，未实现，
    # 阶段 1 按 disabled 处理）/ disabled（只保留 L0 与登录入口）。
    # 代码默认 disabled：已有部署升级后不改变现状；新部署在 deploy/.env.example
    # 引导下配置 lan_shared（基线 §13：升级需 Owner 确认可信 CIDR 后再启用）。
    anonymous_catalog_mode: str = "disabled"
    # 可信家庭局域网 CIDR（逗号分隔，如 "192.168.1.0/24,10.0.0.0/8"）。
    # C 模式 L1 只对回环、该列表内的直连对端、或经可信代理还原后落在上述
    # 范围的客户端开放；无法确认来源时自动降级为 L0/登录入口。
    trusted_lan_cidrs: str = ""
    # Public Catalog 匿名限流（每客户端 IP 每分钟请求数）与最大页长（基线 §3.2/§9.3）
    public_catalog_rate_limit_per_minute: int = 60
    public_catalog_max_page_size: int = 50

    # ── MCP 只读试点（并行轨；MCP 设计 §13，默认关闭） ──
    # 显式配置启用；路径固定 /mcp，不做运行时自定义路径
    mcp_enabled: bool = False
    # 目标协议 allowlist（SDK 旧协议兼容不等于应用允许；首期仅 2026-07-28）
    mcp_allowed_protocol_versions: str = "2026-07-28"
    # 单页最大返回条数（1-20）
    mcp_max_page_size: int = 20
    # Agent Client + Tool 维度限流（每分钟）
    mcp_rate_limit_per_minute: int = 60
    # 游标完整性签名的独立高熵密钥——不得复用 Agent Token、Owner 密码或渠道签名密钥；
    # MCP_ENABLED=true 时必须配置，否则 /mcp 一律 500 拒绝服务
    mcp_cursor_signing_secret: str | None = None
    # MCP Host allowlist（逗号分隔；默认仅内置回环精确值，非回环部署必须显式配置）
    mcp_allowed_hosts: str = ""
    # MCP 可信 Origin（逗号分隔，含 scheme://host[:port]；非浏览器客户端可不带 Origin，
    # 带则必须精确匹配，不接受通配符）
    mcp_trusted_origins: str = ""
    # 部署网络门禁（MCP 设计 §13；BUG-214）：MCP 专用可信 CIDR（逗号分隔，
    # 如 "192.168.1.0/24,fd00::/8"）。默认空 = 仅回环；非回环部署必须显式配置；
    # 对端为可信代理（TRUSTED_PROXIES）时按 XFF 右值法还原真实客户端再判定。
    # 与匿名书架的 TRUSTED_LAN_CIDRS 相互独立（MCP 持 Bearer，边界更严）
    mcp_trusted_cidrs: str = ""
    # HTTPS 档（MCP 设计 §13）：默认 true--HTTP 仅限回环；家庭局域网 HTTP 试点
    # 须 Owner 显式设为 false 且同时配置 MCP_TRUSTED_CIDRS；反代/网关档必须 HTTPS
    mcp_require_https: bool = True
    # 请求/响应体硬上限（字节，MCP 设计 Task 3.3；BUG-212）：请求超限 413；
    # 响应超限防御性拒绝（分页上限保证正常响应远小于该值）
    mcp_max_request_body_bytes: int = 1_048_576
    mcp_max_response_body_bytes: int = 1_048_576

    @property
    def mcp_allowed_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.mcp_allowed_hosts.split(",") if h.strip()}

    @property
    def mcp_trusted_origin_set(self) -> set[str]:
        return {o.strip().lower() for o in self.mcp_trusted_origins.split(",") if o.strip()}

    @property
    def mcp_trusted_cidr_networks(self) -> list:
        import ipaddress

        networks = []
        for item in self.mcp_trusted_cidrs.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
        return networks

    @property
    def mcp_effective_max_page_size(self) -> int:
        """核心档冻结契约：单页 1-20；配置越界（含运行时改写）一律收敛（BUG-212）。"""
        return min(max(self.mcp_max_page_size, 1), 20)

    # ── 登录防爆破（BUG-193）──
    # /auth/login 每来源 IP 每分钟失败尝试上限；只计失败，成功登录不消耗配额
    # （与账号级 5 次锁定互补：这里挡跨密码的分布式爆破与高频试错）
    auth_login_rate_limit_per_minute: int = 10

    @property
    def mcp_allowed_protocol_version_list(self) -> list[str]:
        return [v.strip() for v in self.mcp_allowed_protocol_versions.split(",") if v.strip()]

    @field_validator("mcp_max_page_size")
    @classmethod
    def _clamp_mcp_max_page_size(cls, v: int) -> int:
        # BUG-212：单页上限是冻结契约（1-20），配置越界时收敛而不是放大服务端上限
        return min(max(int(v), 1), 20)

    @field_validator("anonymous_catalog_mode")
    @classmethod
    def _validate_anonymous_catalog_mode(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("lan_shared", "explicit_public", "disabled"):
            raise ValueError("anonymous_catalog_mode 必须是 lan_shared/explicit_public/disabled")
        return v

    @property
    def trusted_lan_networks(self) -> list:
        import ipaddress

        networks = []
        for item in self.trusted_lan_cidrs.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
        return networks

    @property
    def trusted_proxy_networks(self) -> list:
        import ipaddress

        networks = []
        for item in self.trusted_proxies.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
        return networks

    # WBS-1：公开发现面的规范 Base URL。
    # 用于生成 manifest/openapi/skills 等绝对链接，不得无条件信任请求 Host 头。
    # 格式：scheme://host[:port]，不带路径、查询串或凭证。
    public_base_url: str | None = None

    # WBS-1：可信代理 Host allowlist（逗号分隔）。
    # 仅当请求来自这些 Host 时才读取 X-Forwarded-Host / X-Forwarded-Proto。
    # 未配置时不信任任何转发头，使用 direct connection 的 Host。
    trusted_proxy_hosts: str | None = None

    # WBS-1：CORS 允许的 Origin 列表（逗号分隔）。
    # 生产同源部署留空即可；开发环境可配 http://localhost:5173 等。
    cors_origins: str = "*"

    # WBS-5：Agent Token 安全。
    # owner 密码 Argon2id 参数（时间成本、内存、并行度）
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 4

    @field_validator("public_base_url")
    @classmethod
    def _validate_public_base_url(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().rstrip("/")
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("public_base_url 必须以 http:// 或 https:// 开头")
        if not parsed.netloc:
            raise ValueError("public_base_url 必须包含主机名")
        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError("public_base_url 不得包含路径、查询串或 fragment")
        if parsed.username or parsed.password:
            raise ValueError("public_base_url 不得包含凭证")
        return v

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_host_list(self) -> list[str]:
        if not self.trusted_proxy_hosts:
            return []
        return [h.strip().lower() for h in self.trusted_proxy_hosts.split(",") if h.strip()]


settings = Settings()
