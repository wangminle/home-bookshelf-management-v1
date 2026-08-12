"""WBS-2：Agent 发现面 Pydantic Schema。

公开发现面的响应使用独立 Schema，不直接序列化 ORM 对象。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    id: str = "home-bookshelf"
    name: str
    version: str
    description: str


class ManifestLinks(BaseModel):
    human_entry: str
    agent_guide: str
    api_catalog: str
    openapi: str
    skills_index: str
    authorization_manage: str


class DataPolicy(BaseModel):
    discovery_contains_business_data: bool = False
    business_access_requires_user_authorization: bool = True
    credentials_in_urls: bool = False


class Capability(BaseModel):
    id: str
    description: str
    authorization_required: bool
    required_scopes: list[str]
    risk: str  # read | write | delete


class SkillsRef(BaseModel):
    bundle_version: str
    index: str


class Manifest(BaseModel):
    schema_version: str = "1.0"
    service: ServiceInfo
    links: ManifestLinks
    data_policy: DataPolicy = DataPolicy()
    capabilities: list[Capability]
    skills: SkillsRef


class LinksetEntry(BaseModel):
    href: str
    type: str


class LinksetAnchor(BaseModel):
    anchor: str
    service_desc: list[LinksetEntry]
    describedby: list[LinksetEntry]


class Linkset(BaseModel):
    linkset: list[LinksetAnchor]


class PublicHealthData(BaseModel):
    status: str = "available"
    service: str = "home-bookshelf"
    authorization_required: bool = True


class SkillIndexEntry(BaseModel):
    name: str
    version: str
    description: str
    archive_url: str
    sha256: str
    size_bytes: int
    signature_url: str | None = None
    signature_algorithm: str | None = None
    signing_key_id: str | None = None
    required_cli_version: str | None = None
    required_api_version: str | None = None
    requested_scopes: list[str] = Field(default_factory=list)
    has_scripts: bool = False
    has_network_access: bool = False
    writes_data: bool = False


class SkillIndex(BaseModel):
    bundle_version: str
    skills: list[SkillIndexEntry]
