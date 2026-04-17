import logging
import time
from typing import Any, Optional

from open_webui.internal.db import Base, JSONField, get_db


from open_webui.models.chats import Chats
from open_webui.models.groups import Groups


from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text

log = logging.getLogger(__name__)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _deep_merge_dict(
    current: dict,
    patch: dict,
    *,
    replace_paths: Optional[set[tuple[str, ...]]] = None,
    _path: tuple[str, ...] = (),
) -> dict:
    merged = dict(current)
    normalized_replace_paths = replace_paths or set()

    for key, value in patch.items():
        next_path = (*_path, key)
        if next_path in normalized_replace_paths:
            merged[key] = value
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(
                _as_dict(merged.get(key)),
                value,
                replace_paths=normalized_replace_paths,
                _path=next_path,
            )
        else:
            merged[key] = value

    return merged


class UserSettingsRevisionConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("User settings revision conflict")

####################
# User DB Schema
####################


class User(Base):
    __tablename__ = "user"

    id = Column(String, primary_key=True)
    name = Column(String)
    email = Column(String)
    role = Column(String)
    profile_image_url = Column(Text)

    last_active_at = Column(BigInteger)
    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)

    api_key = Column(String, nullable=True, unique=True)
    settings = Column(JSONField, nullable=True)
    info = Column(JSONField, nullable=True)

    oauth_sub = Column(Text, unique=True)
    note = Column(Text, nullable=True)


class UserSettings(BaseModel):
    ui: Optional[dict] = {}
    revision: int = 0
    model_config = ConfigDict(extra="allow")
    pass


class UserSettingsUpdateForm(BaseModel):
    ui: Optional[dict] = None
    revision: Optional[int] = None
    model_config = ConfigDict(extra="allow")
    pass


class UserModel(BaseModel):
    id: str
    name: str
    email: str
    role: str = "pending"
    profile_image_url: str

    last_active_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch

    api_key: Optional[str] = None
    settings: Optional[UserSettings] = None
    info: Optional[dict] = None

    oauth_sub: Optional[str] = None
    note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    profile_image_url: str


class UserNameResponse(BaseModel):
    id: str
    name: str
    role: str
    profile_image_url: str


class UserRoleUpdateForm(BaseModel):
    id: str
    role: str


class UserUpdateForm(BaseModel):
    name: str
    email: str
    profile_image_url: str
    password: Optional[str] = None
    note: Optional[str] = None


class UsersTable:
    def insert_new_user(
        self,
        id: str,
        name: str,
        email: str,
        profile_image_url: str = "/user.png",
        role: str = "pending",
        oauth_sub: Optional[str] = None,
    ) -> Optional[UserModel]:
        with get_db() as db:
            user = UserModel(
                **{
                    "id": id,
                    "name": name,
                    "email": email,
                    "role": role,
                    "profile_image_url": profile_image_url,
                    "last_active_at": int(time.time()),
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "oauth_sub": oauth_sub,
                }
            )
            result = User(**user.model_dump())
            db.add(result)
            db.commit()
            db.refresh(result)
            if result:
                return user
            else:
                return None

    def get_user_by_id(self, id: str) -> Optional[UserModel]:
        try:
            with get_db() as db:
                user = db.query(User).filter_by(id=id).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def get_users_map_by_ids(self, ids: list[str]) -> dict[str, UserModel]:
        """Batch user lookup: single IN query instead of N+1."""
        if not ids:
            return {}
        try:
            with get_db() as db:
                users = db.query(User).filter(User.id.in_(ids)).all()
                return {u.id: UserModel.model_validate(u) for u in users}
        except Exception:
            return {}

    def get_user_by_api_key(self, api_key: str) -> Optional[UserModel]:
        try:
            with get_db() as db:
                user = db.query(User).filter_by(api_key=api_key).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def get_user_by_email(self, email: str) -> Optional[UserModel]:
        try:
            with get_db() as db:
                user = db.query(User).filter_by(email=email).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def get_user_by_oauth_sub(self, sub: str) -> Optional[UserModel]:
        try:
            with get_db() as db:
                user = db.query(User).filter_by(oauth_sub=sub).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def get_users(
        self, skip: Optional[int] = None, limit: Optional[int] = None
    ) -> list[UserModel]:
        with get_db() as db:

            query = db.query(User).order_by(User.created_at.desc())

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            users = query.all()

            return [UserModel.model_validate(user) for user in users]

    def get_users_by_user_ids(self, user_ids: list[str]) -> list[UserModel]:
        with get_db() as db:
            users = db.query(User).filter(User.id.in_(user_ids)).all()
            return [UserModel.model_validate(user) for user in users]

    def get_num_users(self) -> Optional[int]:
        with get_db() as db:
            return db.query(User).count()

    def get_first_user(self) -> UserModel:
        try:
            with get_db() as db:
                user = db.query(User).order_by(User.created_at).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def get_user_webhook_url_by_id(self, id: str) -> Optional[str]:
        try:
            with get_db() as db:
                user = db.query(User).filter_by(id=id).first()

                if user.settings is None:
                    return None
                else:
                    return (
                        user.settings.get("ui", {})
                        .get("notifications", {})
                        .get("webhook_url", None)
                    )
        except Exception:
            return None

    def update_user_role_by_id(self, id: str, role: str) -> Optional[UserModel]:
        try:
            with get_db() as db:
                db.query(User).filter_by(id=id).update({"role": role})
                db.commit()
                user = db.query(User).filter_by(id=id).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def update_user_profile_image_url_by_id(
        self, id: str, profile_image_url: str
    ) -> Optional[UserModel]:
        try:
            with get_db() as db:
                db.query(User).filter_by(id=id).update(
                    {"profile_image_url": profile_image_url}
                )
                db.commit()

                user = db.query(User).filter_by(id=id).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def update_user_last_active_by_id(self, id: str) -> Optional[UserModel]:
        try:
            with get_db() as db:
                db.query(User).filter_by(id=id).update(
                    {"last_active_at": int(time.time())}
                )
                db.commit()

                user = db.query(User).filter_by(id=id).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def update_user_oauth_sub_by_id(
        self, id: str, oauth_sub: str
    ) -> Optional[UserModel]:
        try:
            with get_db() as db:
                db.query(User).filter_by(id=id).update({"oauth_sub": oauth_sub})
                db.commit()

                user = db.query(User).filter_by(id=id).first()
                return UserModel.model_validate(user)
        except Exception:
            return None

    def update_user_by_id(self, id: str, updated: dict) -> Optional[UserModel]:
        try:
            with get_db() as db:
                db.query(User).filter_by(id=id).update(updated)
                db.commit()

                user = db.query(User).filter_by(id=id).first()
                return UserModel.model_validate(user)
                # return UserModel(**user.dict())
        except Exception:
            log.exception("Failed to update user by id: %s", id)
            return None

    def update_user_settings_by_id(self, id: str, updated: dict) -> Optional[UserModel]:
        return self.patch_user_settings_by_id(id, updated)

    def patch_user_settings_by_id(
        self,
        id: str,
        updated: dict,
        expected_revision: Optional[int] = None,
        replace_paths: Optional[set[tuple[str, ...]]] = None,
    ) -> Optional[UserModel]:
        try:
            with get_db() as db:
                user = db.query(User).filter_by(id=id).with_for_update().first()
                if user is None:
                    return None

                user_settings = _as_dict(user.settings)
                current_revision = int(user_settings.get("revision") or 0)

                if (
                    expected_revision is not None
                    and int(expected_revision) != current_revision
                ):
                    raise UserSettingsRevisionConflict(current_revision)

                next_settings = _deep_merge_dict(
                    user_settings,
                    _as_dict(updated),
                    replace_paths=replace_paths,
                )
                next_settings["revision"] = current_revision + 1

                user.settings = next_settings
                user.updated_at = int(time.time())
                db.add(user)
                db.commit()
                db.refresh(user)

                return UserModel.model_validate(user)
        except UserSettingsRevisionConflict:
            raise
        except Exception:
            return None

    def delete_user_by_id(self, id: str) -> bool:
        try:
            # Lazy imports to avoid circular dependency
            from open_webui.models.files import Files
            from open_webui.models.prompts import Prompts
            from open_webui.models.tools import Tools
            from open_webui.models.functions import Functions
            from open_webui.models.knowledge import Knowledges
            from open_webui.models.memories import Memories
            from open_webui.models.messages import Messages
            from open_webui.models.notes import Notes
            from open_webui.models.channels import Channels
            from open_webui.models.folders import Folders
            from open_webui.models.tags import Tags

            # Remove User from Groups
            Groups.remove_user_from_all_groups(id)

            # Delete all user resources
            Chats.delete_chats_by_user_id(id)
            Files.delete_files_by_user_id(id)
            Prompts.delete_prompts_by_user_id(id)
            Tools.delete_tools_by_user_id(id)
            Functions.delete_functions_by_user_id(id)
            Knowledges.delete_knowledge_by_user_id(id)
            Memories.delete_memories_by_user_id(id)
            Messages.delete_messages_by_user_id(id)
            Notes.delete_notes_by_user_id(id)
            Channels.delete_channels_by_user_id(id)
            Folders.delete_folders_by_user_id(id)
            Tags.delete_tags_by_user_id(id)

            with get_db() as db:
                # Delete User
                db.query(User).filter_by(id=id).delete()
                db.commit()

            return True
        except Exception:
            return False

    def update_user_api_key_by_id(self, id: str, api_key: str) -> str:
        try:
            with get_db() as db:
                result = db.query(User).filter_by(id=id).update({"api_key": api_key})
                db.commit()
                return True if result == 1 else False
        except Exception:
            return False

    def get_user_api_key_by_id(self, id: str) -> Optional[str]:
        try:
            with get_db() as db:
                user = db.query(User).filter_by(id=id).first()
                return user.api_key
        except Exception:
            return None

    def get_valid_user_ids(self, user_ids: list[str]) -> list[str]:
        with get_db() as db:
            users = db.query(User).filter(User.id.in_(user_ids)).all()
            return [user.id for user in users]


Users = UsersTable()
