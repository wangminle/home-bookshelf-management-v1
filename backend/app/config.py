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
    # 直接对端在列表内时，loopback 判定改用 X-Forwarded-For 首跳——用于 lwa/nginx
    # 反代场景（后端看到的对端是网关 IP）。默认空：不信任任何代理，XFF 视为不可信。
    trusted_proxies: str = ""

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
