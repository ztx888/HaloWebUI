from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, Integer, Text

from open_webui.env import SRC_LOG_LEVELS
from open_webui.internal.db import Base, JSONField, get_db

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def hash_external_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_external_api_key() -> str:
    return f"hwg-{secrets.token_urlsafe(24)}"


class ExternalApiClient(Base):
    __tablename__ = "external_api_client"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    owner_user_id = Column(Text, nullable=False)
    api_key_hash = Column(Text, nullable=False, unique=True)
    key_prefix = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    allowed_protocols = Column(JSONField, nullable=False)
    allowed_model_ids = Column(JSONField, nullable=False)
    allow_tools = Column(Boolean, default=False)
    rpm_limit = Column(Integer, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    last_used_at = Column(BigInteger, nullable=True)


class ExternalApiAuditLog(Base):
    __tablename__ = "external_api_audit_log"

    id = Column(Text, primary_key=True)
    client_id = Column(Text, nullable=False)
    owner_user_id = Column(Text, nullable=False)
    protocol = Column(Text, nullable=False)
    endpoint = Column(Text, nullable=False)
    model = Column(Text, nullable=True)
    status_code = Column(Integer, nullable=False)
    tools_used = Column(Boolean, default=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    ip_address = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    meta = Column(JSONField, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class ExternalApiClientModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_user_id: str
    api_key_hash: str
    key_prefix: str
    enabled: bool
    allowed_protocols: list[str] = Field(default_factory=list)
    allowed_model_ids: list[str] = Field(default_factory=list)
    allow_tools: bool = False
    rpm_limit: Optional[int] = None
    note: Optional[str] = None
    created_at: int
    updated_at: int
    last_used_at: Optional[int] = None


class ExternalApiClientResponse(BaseModel):
    id: str
    name: str
    owner_user_id: str
    key_prefix: str
    enabled: bool
    allowed_protocols: list[str] = Field(default_factory=list)
    allowed_model_ids: list[str] = Field(default_factory=list)
    allow_tools: bool = False
    rpm_limit: Optional[int] = None
    note: Optional[str] = None
    created_at: int
    updated_at: int
    last_used_at: Optional[int] = None


class ExternalApiClientCreateForm(BaseModel):
    name: str
    owner_user_id: str
    allowed_protocols: list[str]
    allowed_model_ids: list[str] = Field(default_factory=list)
    allow_tools: bool = False
    rpm_limit: Optional[int] = None
    note: Optional[str] = None
    enabled: bool = True


class ExternalApiClientUpdateForm(BaseModel):
    name: str
    owner_user_id: str
    allowed_protocols: list[str]
    allowed_model_ids: list[str] = Field(default_factory=list)
    allow_tools: bool = False
    rpm_limit: Optional[int] = None
    note: Optional[str] = None
    enabled: bool = True


class ExternalApiAuditLogModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    owner_user_id: str
    protocol: str
    endpoint: str
    model: Optional[str] = None
    status_code: int
    tools_used: bool = False
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    ip_address: Optional[str] = None
    error: Optional[str] = None
    meta: Optional[dict] = None
    created_at: int


class ExternalApiClientsTable:
    def create(self, form_data: ExternalApiClientCreateForm) -> tuple[ExternalApiClientModel, str]:
        raw_key = create_external_api_key()
        now = _now_ms()
        client = ExternalApiClientModel(
            id=str(uuid.uuid4()),
            name=form_data.name.strip(),
            owner_user_id=form_data.owner_user_id,
            api_key_hash=hash_external_api_key(raw_key),
            key_prefix=raw_key[:12],
            enabled=form_data.enabled,
            allowed_protocols=sorted({str(item).lower() for item in form_data.allowed_protocols if str(item).strip()}),
            allowed_model_ids=sorted({str(item) for item in form_data.allowed_model_ids if str(item).strip()}),
            allow_tools=form_data.allow_tools,
            rpm_limit=form_data.rpm_limit,
            note=form_data.note,
            created_at=now,
            updated_at=now,
            last_used_at=None,
        )
        with get_db() as db:
            db.add(ExternalApiClient(**client.model_dump()))
            db.commit()
        return client, raw_key

    def list(self) -> list[ExternalApiClientModel]:
        with get_db() as db:
            rows = db.query(ExternalApiClient).order_by(ExternalApiClient.created_at.desc()).all()
            return [ExternalApiClientModel.model_validate(row) for row in rows]

    def get_by_id(self, client_id: str) -> Optional[ExternalApiClientModel]:
        with get_db() as db:
            row = db.query(ExternalApiClient).filter_by(id=client_id).first()
            return ExternalApiClientModel.model_validate(row) if row else None

    def get_by_api_key(self, api_key: str) -> Optional[ExternalApiClientModel]:
        if not api_key:
            return None
        digest = hash_external_api_key(api_key)
        with get_db() as db:
            row = db.query(ExternalApiClient).filter_by(api_key_hash=digest).first()
            return ExternalApiClientModel.model_validate(row) if row else None

    def update(self, client_id: str, form_data: ExternalApiClientUpdateForm) -> Optional[ExternalApiClientModel]:
        with get_db() as db:
            row = db.query(ExternalApiClient).filter_by(id=client_id).first()
            if not row:
                return None
            row.name = form_data.name.strip()
            row.owner_user_id = form_data.owner_user_id
            row.enabled = form_data.enabled
            row.allowed_protocols = sorted({str(item).lower() for item in form_data.allowed_protocols if str(item).strip()})
            row.allowed_model_ids = sorted({str(item) for item in form_data.allowed_model_ids if str(item).strip()})
            row.allow_tools = form_data.allow_tools
            row.rpm_limit = form_data.rpm_limit
            row.note = form_data.note
            row.updated_at = _now_ms()
            db.commit()
            db.refresh(row)
            return ExternalApiClientModel.model_validate(row)

    def touch_last_used(self, client_id: str) -> None:
        with get_db() as db:
            row = db.query(ExternalApiClient).filter_by(id=client_id).first()
            if not row:
                return
            row.last_used_at = _now_ms()
            row.updated_at = _now_ms()
            db.commit()

    def delete(self, client_id: str) -> bool:
        with get_db() as db:
            db.query(ExternalApiAuditLog).filter_by(client_id=client_id).delete()
            deleted = db.query(ExternalApiClient).filter_by(id=client_id).delete()
            db.commit()
            return bool(deleted)


class ExternalApiAuditLogsTable:
    def create(
        self,
        *,
        client_id: str,
        owner_user_id: str,
        protocol: str,
        endpoint: str,
        model: Optional[str],
        status_code: int,
        tools_used: bool,
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        latency_ms: Optional[int],
        ip_address: Optional[str],
        error: Optional[str],
        meta: Optional[dict] = None,
    ) -> ExternalApiAuditLogModel:
        entry = ExternalApiAuditLogModel(
            id=str(uuid.uuid4()),
            client_id=client_id,
            owner_user_id=owner_user_id,
            protocol=protocol,
            endpoint=endpoint,
            model=model,
            status_code=status_code,
            tools_used=tools_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            ip_address=ip_address,
            error=error,
            meta=meta,
            created_at=_now_ms(),
        )
        with get_db() as db:
            db.add(ExternalApiAuditLog(**entry.model_dump()))
            db.commit()
        return entry

    def list(self, limit: int = 100) -> list[ExternalApiAuditLogModel]:
        with get_db() as db:
            rows = (
                db.query(ExternalApiAuditLog)
                .order_by(ExternalApiAuditLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [ExternalApiAuditLogModel.model_validate(row) for row in rows]

    def list_by_client(self, client_id: str, limit: int = 100) -> list[ExternalApiAuditLogModel]:
        with get_db() as db:
            rows = (
                db.query(ExternalApiAuditLog)
                .filter_by(client_id=client_id)
                .order_by(ExternalApiAuditLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [ExternalApiAuditLogModel.model_validate(row) for row in rows]


ExternalApiClients = ExternalApiClientsTable()
ExternalApiAuditLogs = ExternalApiAuditLogsTable()
