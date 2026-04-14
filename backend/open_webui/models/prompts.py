import time
import uuid
from typing import Optional
import logging

from open_webui.internal.db import Base, get_db
from open_webui.models.users import Users, UserResponse

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, String, Text, JSON, inspect, text

from open_webui.utils.access_control import has_access

log = logging.getLogger(__name__)

####################
# Prompts DB Schema
####################


class Prompt(Base):
    __tablename__ = "prompt"

    id = Column(Text, primary_key=True)
    command = Column(String, unique=True, index=True)
    user_id = Column(String)
    name = Column(Text)
    content = Column(Text)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)

    is_active = Column(Boolean, server_default="1", nullable=False)
    version_id = Column(Text, nullable=True)

    access_control = Column(JSON, nullable=True)

    created_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=True)

    # Legacy — kept for migration compat only
    title = Column(Text, nullable=True)
    timestamp = Column(BigInteger, nullable=True)


class PromptModel(BaseModel):
    id: str
    command: str
    user_id: str
    name: str
    content: str

    data: Optional[dict] = None
    meta: Optional[dict] = None
    tags: Optional[list] = None

    is_active: bool = True
    version_id: Optional[str] = None

    access_control: Optional[dict] = None

    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


####################
# Version History
####################


class PromptVersion(BaseModel):
    """Snapshot of a prompt at a point in time."""

    id: str
    prompt_id: str
    name: str
    command: str
    content: str
    data: Optional[dict] = None
    meta: Optional[dict] = None
    tags: Optional[list] = None
    access_control: Optional[dict] = None
    commit_message: str = ""
    created_at: int = 0

    model_config = ConfigDict(from_attributes=True)


####################
# Forms & Responses
####################


class PromptUserResponse(PromptModel):
    user: Optional[UserResponse] = None


class PromptListResponse(BaseModel):
    items: list[PromptUserResponse]
    total: int


class PromptForm(BaseModel):
    command: str
    name: str
    content: str
    data: Optional[dict] = None
    meta: Optional[dict] = None
    tags: Optional[list] = None
    is_active: Optional[bool] = None
    access_control: Optional[dict] = None
    commit_message: Optional[str] = None


class PromptMetaForm(BaseModel):
    """For partial meta/tags-only updates."""

    meta: Optional[dict] = None
    tags: Optional[list] = None


####################
# Table Operations
####################


class PromptsTable:
    @staticmethod
    def _normalize_command(command: str) -> str:
        command = (command or "").strip()
        return command[1:] if command.startswith("/") else command

    def _command_candidates(self, command: str) -> list[str]:
        normalized = self._normalize_command(command)
        if not normalized:
            return []
        return list(dict.fromkeys([normalized, f"/{normalized}"]))

    def _to_prompt_model(self, prompt: Prompt) -> PromptModel:
        payload = PromptModel.model_validate(prompt).model_dump()
        payload["command"] = self._normalize_command(payload.get("command", ""))
        return PromptModel.model_validate(payload)

    def _uses_legacy_integer_id(self, db) -> bool:
        try:
            column_info = next(
                (
                    column
                    for column in inspect(db.bind).get_columns("prompt")
                    if column["name"] == "id"
                ),
                None,
            )
            if column_info is None:
                return False

            try:
                return column_info["type"].python_type is int
            except Exception:
                return "INT" in str(column_info["type"]).upper()
        except Exception:
            return False

    def _coerce_prompt_id(self, db, prompt_id: str):
        if not self._uses_legacy_integer_id(db):
            return prompt_id

        if isinstance(prompt_id, str) and prompt_id.strip().isdigit():
            return int(prompt_id.strip())

        return prompt_id

    def _next_legacy_prompt_id(self, db) -> int:
        result = db.execute(text('SELECT COALESCE(MAX(id), 0) + 1 FROM "prompt"'))
        return int(result.scalar() or 1)

    def insert_new_prompt(
        self, user_id: str, form_data: PromptForm
    ) -> Optional[PromptModel]:
        now = int(time.time())

        try:
            with get_db() as db:
                prompt_id = (
                    self._next_legacy_prompt_id(db)
                    if self._uses_legacy_integer_id(db)
                    else str(uuid.uuid4())
                )

                result = Prompt(
                    id=prompt_id,
                    user_id=user_id,
                    command=self._normalize_command(form_data.command),
                    name=form_data.name,
                    content=form_data.content,
                    data=form_data.data,
                    meta=form_data.meta,
                    tags=form_data.tags,
                    is_active=form_data.is_active if form_data.is_active is not None else True,
                    access_control=form_data.access_control,
                    created_at=now,
                    updated_at=now,
                )
                # Legacy compat fields
                result.title = form_data.name
                result.timestamp = now
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return self._to_prompt_model(result)
                return None
        except Exception:
            log.exception("Failed to insert prompt %s", form_data.command)
            return None

    def get_prompt_by_id(self, prompt_id: str) -> Optional[PromptModel]:
        try:
            with get_db() as db:
                prompt = (
                    db.query(Prompt)
                    .filter_by(id=self._coerce_prompt_id(db, prompt_id))
                    .first()
                )
                if prompt:
                    return self._to_prompt_model(prompt)
                return None
        except Exception:
            return None

    def get_prompt_by_command(self, command: str) -> Optional[PromptModel]:
        try:
            with get_db() as db:
                candidates = self._command_candidates(command)
                if not candidates:
                    return None
                prompt = db.query(Prompt).filter(Prompt.command.in_(candidates)).first()
                if prompt:
                    return self._to_prompt_model(prompt)
                return None
        except Exception:
            return None

    def get_prompts(self) -> list[PromptUserResponse]:
        with get_db() as db:
            prompts = []
            for prompt in db.query(Prompt).order_by(Prompt.updated_at.desc()).all():
                user = Users.get_user_by_id(prompt.user_id)
                prompts.append(
                    PromptUserResponse.model_validate(
                        {
                            **self._to_prompt_model(prompt).model_dump(),
                            "user": user.model_dump() if user else None,
                        }
                    )
                )
            return prompts

    def get_prompts_paginated(
        self,
        page: int = 1,
        limit: int = 30,
        order_by: str = "updated_at",
    ) -> PromptListResponse:
        with get_db() as db:
            query = db.query(Prompt)

            if order_by == "name":
                query = query.order_by(Prompt.name)
            elif order_by == "created_at":
                query = query.order_by(Prompt.created_at.desc())
            else:
                query = query.order_by(Prompt.updated_at.desc())

            total = query.count()
            offset = (page - 1) * limit
            items_raw = query.offset(offset).limit(limit).all()

            items = []
            for prompt in items_raw:
                user = Users.get_user_by_id(prompt.user_id)
                items.append(
                    PromptUserResponse.model_validate(
                        {
                            **self._to_prompt_model(prompt).model_dump(),
                            "user": user.model_dump() if user else None,
                        }
                    )
                )
            return PromptListResponse(items=items, total=total)

    def get_prompts_by_user_id(
        self, user_id: str, permission: str = "write"
    ) -> list[PromptUserResponse]:
        prompts = self.get_prompts()
        return [
            prompt
            for prompt in prompts
            if prompt.user_id == user_id
            or has_access(user_id, permission, prompt.access_control)
        ]

    def update_prompt_by_id(
        self, prompt_id: str, form_data: PromptForm
    ) -> Optional[PromptModel]:
        try:
            with get_db() as db:
                prompt = (
                    db.query(Prompt)
                    .filter_by(id=self._coerce_prompt_id(db, prompt_id))
                    .first()
                )
                if not prompt:
                    return None

                prompt.name = form_data.name
                prompt.command = self._normalize_command(form_data.command)
                prompt.content = form_data.content
                prompt.access_control = form_data.access_control

                if form_data.data is not None:
                    prompt.data = form_data.data
                if form_data.meta is not None:
                    prompt.meta = form_data.meta
                if form_data.tags is not None:
                    prompt.tags = form_data.tags
                if form_data.is_active is not None:
                    prompt.is_active = form_data.is_active

                now = int(time.time())
                prompt.updated_at = now
                prompt.title = form_data.name
                prompt.timestamp = now

                db.commit()
                return self._to_prompt_model(prompt)
        except Exception:
            return None

    def update_prompt_by_command(
        self, command: str, form_data: PromptForm
    ) -> Optional[PromptModel]:
        """Legacy command-based update for backward compatibility."""
        try:
            with get_db() as db:
                candidates = self._command_candidates(command)
                if not candidates:
                    return None
                prompt = db.query(Prompt).filter(Prompt.command.in_(candidates)).first()
                if not prompt:
                    return None

                prompt.name = form_data.name
                prompt.command = self._normalize_command(form_data.command)
                prompt.content = form_data.content
                prompt.access_control = form_data.access_control

                if form_data.data is not None:
                    prompt.data = form_data.data
                if form_data.meta is not None:
                    prompt.meta = form_data.meta
                if form_data.tags is not None:
                    prompt.tags = form_data.tags
                if form_data.is_active is not None:
                    prompt.is_active = form_data.is_active

                now = int(time.time())
                prompt.updated_at = now
                prompt.title = form_data.name
                prompt.timestamp = now

                db.commit()
                return self._to_prompt_model(prompt)
        except Exception:
            return None

    def update_prompt_meta_by_id(
        self, prompt_id: str, form_data: PromptMetaForm
    ) -> Optional[PromptModel]:
        try:
            with get_db() as db:
                prompt = (
                    db.query(Prompt)
                    .filter_by(id=self._coerce_prompt_id(db, prompt_id))
                    .first()
                )
                if not prompt:
                    return None

                if form_data.meta is not None:
                    prompt.meta = form_data.meta
                if form_data.tags is not None:
                    prompt.tags = form_data.tags

                prompt.updated_at = int(time.time())
                db.commit()
                return self._to_prompt_model(prompt)
        except Exception:
            return None

    def toggle_prompt_by_id(
        self, prompt_id: str, is_active: bool
    ) -> Optional[PromptModel]:
        try:
            with get_db() as db:
                prompt = (
                    db.query(Prompt)
                    .filter_by(id=self._coerce_prompt_id(db, prompt_id))
                    .first()
                )
                if not prompt:
                    return None
                prompt.is_active = is_active
                prompt.updated_at = int(time.time())
                db.commit()
                return self._to_prompt_model(prompt)
        except Exception:
            return None

    def toggle_prompt_by_command(
        self, command: str, is_active: bool
    ) -> Optional[PromptModel]:
        """Legacy command-based toggle."""
        try:
            with get_db() as db:
                candidates = self._command_candidates(command)
                if not candidates:
                    return None
                prompt = db.query(Prompt).filter(Prompt.command.in_(candidates)).first()
                if not prompt:
                    return None
                prompt.is_active = is_active
                prompt.updated_at = int(time.time())
                db.commit()
                return self._to_prompt_model(prompt)
        except Exception:
            return None

    def delete_prompt_by_id(self, prompt_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Prompt).filter_by(id=self._coerce_prompt_id(db, prompt_id)).delete()
                db.commit()
                return True
        except Exception:
            return False

    def delete_prompt_by_command(self, command: str) -> bool:
        try:
            with get_db() as db:
                candidates = self._command_candidates(command)
                if not candidates:
                    return False
                db.query(Prompt).filter(Prompt.command.in_(candidates)).delete()
                db.commit()
                return True
        except Exception:
            return False

    def delete_prompts_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Prompt).filter_by(user_id=user_id).delete()
                db.commit()
                return True
        except Exception:
            return False


Prompts = PromptsTable()
