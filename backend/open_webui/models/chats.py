import logging
import json
import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_db
from open_webui.models.tags import TagModel, Tag, Tags
from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.image_generation_options import (
    sanitize_chat_payload_image_generation_options,
)

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text, JSON, Index
from sqlalchemy import or_, func, select, and_, text
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql import exists

####################
# Chat DB Schema
####################

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


class Chat(Base):
    __tablename__ = "chat"

    id = Column(String, primary_key=True)
    user_id = Column(String)
    title = Column(Text)
    chat = Column(JSON)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)

    share_id = Column(Text, unique=True, nullable=True)
    archived = Column(Boolean, default=False)
    pinned = Column(Boolean, default=False, nullable=True)

    meta = Column(JSON, server_default="{}")
    folder_id = Column(Text, nullable=True)
    assistant_id = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_chat_user_id", "user_id"),
        Index("ix_chat_folder_id", "folder_id"),
        Index("ix_chat_assistant_id", "assistant_id"),
        Index("ix_chat_updated_at", "updated_at"),
        Index("ix_chat_created_at", "created_at"),
    )


####################
# ChatMessage DB Schema
####################


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id = Column(String, primary_key=True)
    chat_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=True)

    parent_id = Column(String, nullable=True)

    model = Column(String, nullable=True)

    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)

    meta = Column(JSON, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_chat_message_chat_id", "chat_id"),
        Index("ix_chat_message_user_id", "user_id"),
        Index("ix_chat_message_model", "model"),
        Index("ix_chat_message_created_at", "created_at"),
    )


class ChatMessageModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_id: str
    user_id: str
    role: str
    content: Optional[str] = None
    parent_id: Optional[str] = None
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    meta: Optional[dict] = None
    created_at: int
    updated_at: int


class ChatModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    chat: dict

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    share_id: Optional[str] = None
    archived: bool = False
    pinned: Optional[bool] = False

    meta: dict = {}
    folder_id: Optional[str] = None
    assistant_id: Optional[str] = None


####################
# Forms
####################


class ChatForm(BaseModel):
    chat: dict
    folder_id: Optional[str] = None
    assistant_id: Optional[str] = None


class ChatImportForm(ChatForm):
    meta: Optional[dict] = {}
    pinned: Optional[bool] = False
    folder_id: Optional[str] = None
    assistant_id: Optional[str] = None


class ChatTitleMessagesForm(BaseModel):
    title: str
    messages: list[dict]


class ChatTitleForm(BaseModel):
    title: str


class ChatComposerStateForm(BaseModel):
    composer_state: dict


class ChatResponse(BaseModel):
    id: str
    user_id: str
    title: str
    chat: dict
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch
    share_id: Optional[str] = None  # id of the chat to be shared
    archived: bool
    pinned: Optional[bool] = False
    meta: dict = {}
    folder_id: Optional[str] = None
    assistant_id: Optional[str] = None


class ChatTitleIdResponse(BaseModel):
    id: str
    title: str
    updated_at: int
    created_at: int
    assistant_id: Optional[str] = None


####################
# ChatReaction DB Schema
####################


class ChatReaction(Base):
    """Kept for Alembic migration compatibility - do not remove."""
    __tablename__ = "chat_reaction"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    chat_id = Column(Text, nullable=False)
    message_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_chat_reaction_chat_message", "chat_id", "message_id"),
    )


# [REACTION_FEATURE] Commented out - reaction feature disabled for now
# class ChatReactionModel(BaseModel):
#     model_config = ConfigDict(from_attributes=True)
#
#     id: str
#     user_id: str
#     chat_id: str
#     message_id: str
#     name: str
#     created_at: int
#
#
# class ChatReactionSummary(BaseModel):
#     name: str
#     user_ids: list[str]
#     count: int


def normalize_chat_payload(chat: Optional[dict]) -> dict:
    """Drop the legacy top-level messages copy when history is the source of truth."""
    if not isinstance(chat, dict):
        return {} if chat is None else chat

    normalized = dict(chat)
    history = normalized.get("history")

    if isinstance(history, dict) and "messages" in history:
        normalized.pop("messages", None)

    return normalized


class ChatTable:
    def _next_user_chat_timestamp(self, db, user_id: str) -> int:
        now = int(time.time())
        latest_updated_at = (
            db.query(func.max(Chat.updated_at)).filter_by(user_id=user_id).scalar() or 0
        )
        return latest_updated_at + 1 if latest_updated_at >= now else now

    def _build_chat_row(
        self,
        *,
        user_id: str,
        chat_payload: dict,
        meta: Optional[dict] = None,
        pinned: Optional[bool] = False,
        folder_id: Optional[str] = None,
        assistant_id: Optional[str] = None,
        now: Optional[int] = None,
    ) -> Chat:
        normalized_chat = normalize_chat_payload(chat_payload)
        normalized_chat, _changed = sanitize_chat_payload_image_generation_options(
            normalized_chat
        )
        now = now if now is not None else int(time.time())
        title = normalized_chat["title"] if "title" in normalized_chat else "New Chat"

        return Chat(
            **{
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "title": title,
                "chat": normalized_chat,
                "meta": meta or {},
                "pinned": pinned,
                "folder_id": folder_id,
                "assistant_id": assistant_id,
                "created_at": now,
                "updated_at": now,
            }
        )

    def insert_new_chat(self, user_id: str, form_data: ChatForm) -> Optional[ChatModel]:
        with get_db() as db:
            now = self._next_user_chat_timestamp(db, user_id)
            result = self._build_chat_row(
                user_id=user_id,
                chat_payload=form_data.chat,
                folder_id=form_data.folder_id,
                assistant_id=form_data.assistant_id,
                now=now,
            )
            db.add(result)
            db.commit()
            db.refresh(result)
            return ChatModel.model_validate(result) if result else None

    def import_chat(
        self, user_id: str, form_data: ChatImportForm
    ) -> Optional[ChatModel]:
        with get_db() as db:
            now = self._next_user_chat_timestamp(db, user_id)
            result = self._build_chat_row(
                user_id=user_id,
                chat_payload=form_data.chat,
                meta=form_data.meta,
                pinned=form_data.pinned,
                folder_id=form_data.folder_id,
                assistant_id=form_data.assistant_id,
                now=now,
            )
            db.add(result)
            db.commit()
            db.refresh(result)
            return ChatModel.model_validate(result) if result else None

    def replace_chats_by_user_id(
        self, user_id: str, import_forms: list[ChatImportForm]
    ) -> list[ChatModel]:
        with get_db() as db:
            base_now = int(time.time())
            rows = [
                self._build_chat_row(
                    user_id=user_id,
                    chat_payload=form_data.chat,
                    meta=form_data.meta,
                    pinned=form_data.pinned,
                    folder_id=form_data.folder_id,
                    assistant_id=form_data.assistant_id,
                    now=base_now + idx,
                )
                for idx, form_data in enumerate(import_forms)
            ]

            existing_chat_ids = [
                chat_id for (chat_id,) in db.query(Chat.id).filter_by(user_id=user_id).all()
            ]
            shared_chat_ids = [f"shared-{chat_id}" for chat_id in existing_chat_ids]

            if shared_chat_ids:
                db.query(Chat).filter(Chat.user_id.in_(shared_chat_ids)).delete(
                    synchronize_session=False
                )

            db.query(ChatMessage).filter_by(user_id=user_id).delete(
                synchronize_session=False
            )
            db.query(Tag).filter_by(user_id=user_id).delete(synchronize_session=False)
            db.query(Chat).filter_by(user_id=user_id).delete(synchronize_session=False)

            if rows:
                db.add_all(rows)

            db.commit()

            for row in rows:
                db.refresh(row)

            return [ChatModel.model_validate(row) for row in rows]

    def update_chat_by_id(
        self, id: str, chat: dict
    ) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                normalized_chat = normalize_chat_payload(chat)
                normalized_chat, _changed = sanitize_chat_payload_image_generation_options(
                    normalized_chat
                )
                chat_item = db.get(Chat, id)
                chat_item.chat = normalized_chat
                flag_modified(chat_item, "chat")
                chat_item.title = (
                    normalized_chat["title"] if "title" in normalized_chat else "New Chat"
                )
                chat_item.updated_at = self._next_user_chat_timestamp(db, chat_item.user_id)
                db.commit()
                db.refresh(chat_item)

                return ChatModel.model_validate(chat_item)
        except Exception:
            return None

    def update_chat_composer_state_by_id(
        self, id: str, composer_state: dict
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat_dict = chat.chat or {}
        sanitized_composer_state, _changed = sanitize_chat_payload_image_generation_options(
            {"composer_state": composer_state if isinstance(composer_state, dict) else {}}
        )
        next_chat = {
            **chat_dict,
            "composer_state": sanitized_composer_state.get("composer_state", {}),
        }

        return self.update_chat_by_id(id, next_chat)

    def update_chat_title_by_id(self, id: str, title: str) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        chat["title"] = title

        return self.update_chat_by_id(id, chat)

    def update_chat_tags_by_id(
        self, id: str, tags: list[str], user
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        self.delete_all_tags_by_id_and_user_id(id, user.id)

        for tag in chat.meta.get("tags", []):
            if self.count_chats_by_tag_name_and_user_id(tag, user.id) == 0:
                Tags.delete_tag_by_name_and_user_id(tag, user.id)

        for tag_name in tags:
            if tag_name.lower() == "none":
                continue

            self.add_chat_tag_by_id_and_user_id_and_tag_name(id, user.id, tag_name)
        return self.get_chat_by_id(id)

    def get_chat_title_by_id(self, id: str) -> Optional[str]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("title", "New Chat")

    def get_messages_by_chat_id(self, id: str) -> Optional[dict]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("history", {}).get("messages", {}) or {}

    def get_message_by_id_and_message_id(
        self, id: str, message_id: str
    ) -> Optional[dict]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("history", {}).get("messages", {}).get(message_id, {})

    def upsert_message_to_chat_by_id_and_message_id(
        self, id: str, message_id: str, message: dict
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        user_id = chat.user_id
        chat_dict = chat.chat
        history = chat_dict.get("history", {})

        if message_id in history.get("messages", {}):
            history["messages"][message_id] = {
                **history["messages"][message_id],
                **message,
            }
        else:
            history["messages"][message_id] = message

        history["currentId"] = message_id

        chat_dict["history"] = history
        result = self.update_chat_by_id(id, chat_dict)

        # Dual-write: sync to chat_message table (non-blocking, errors logged)
        try:
            final_message = history["messages"][message_id]
            ChatMessages.upsert_message(
                chat_id=id,
                user_id=user_id,
                message_id=message_id,
                message=final_message,
            )
        except Exception as e:
            log.error(f"Dual-write to chat_message failed: {e}")

        return result

    def add_message_status_to_chat_by_id_and_message_id(
        self, id: str, message_id: str, status: dict
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        history = chat.get("history", {})

        if message_id in history.get("messages", {}):
            status_history = history["messages"][message_id].get("statusHistory", [])
            status_history.append(status)
            history["messages"][message_id]["statusHistory"] = status_history

        chat["history"] = history
        return self.update_chat_by_id(id, chat)

    def insert_shared_chat_by_chat_id(self, chat_id: str) -> Optional[ChatModel]:
        with get_db() as db:
            # Get the existing chat to share
            chat = db.get(Chat, chat_id)
            # Check if the chat is already shared
            if chat.share_id:
                return self.get_chat_by_id_and_user_id(chat.share_id, "shared")
            # Create a new chat with the same data, but with a new ID
            normalized_chat = normalize_chat_payload(chat.chat)
            shared_chat = ChatModel(
                **{
                    "id": str(uuid.uuid4()),
                    "user_id": f"shared-{chat_id}",
                    "title": chat.title,
                    "chat": normalized_chat,
                    "assistant_id": chat.assistant_id,
                    "created_at": chat.created_at,
                    "updated_at": self._next_user_chat_timestamp(db, f"shared-{chat_id}"),
                }
            )
            shared_result = Chat(**shared_chat.model_dump())
            db.add(shared_result)
            db.commit()
            db.refresh(shared_result)

            # Update the original chat with the share_id
            result = (
                db.query(Chat)
                .filter_by(id=chat_id)
                .update({"share_id": shared_chat.id})
            )
            db.commit()
            return shared_chat if (shared_result and result) else None

    def update_shared_chat_by_chat_id(self, chat_id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, chat_id)
                shared_chat = (
                    db.query(Chat).filter_by(user_id=f"shared-{chat_id}").first()
                )

                if shared_chat is None:
                    return self.insert_shared_chat_by_chat_id(chat_id)

                shared_chat.title = chat.title
                shared_chat.chat = normalize_chat_payload(chat.chat)

                shared_chat.updated_at = self._next_user_chat_timestamp(
                    db, shared_chat.user_id
                )
                db.commit()
                db.refresh(shared_chat)

                return ChatModel.model_validate(shared_chat)
        except Exception:
            return None

    def delete_shared_chat_by_chat_id(self, chat_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=f"shared-{chat_id}").delete()
                db.commit()

                return True
        except Exception:
            return False

    def update_chat_share_id_by_id(
        self, id: str, share_id: Optional[str]
    ) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.share_id = share_id
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def toggle_chat_pinned_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.pinned = not chat.pinned
                chat.updated_at = self._next_user_chat_timestamp(db, chat.user_id)
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def toggle_chat_archive_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.archived = not chat.archived
                chat.updated_at = self._next_user_chat_timestamp(db, chat.user_id)
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def archive_all_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=user_id).update({"archived": True})
                db.commit()
                return True
        except Exception:
            return False

    def get_archived_chat_list_by_user_id(
        self, user_id: str, skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, archived=True)
                .order_by(Chat.updated_at.desc())
                # .limit(limit).offset(skip)
                .all()
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_list_by_user_id(
        self,
        user_id: str,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id)
            if not include_archived:
                query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc())

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_title_id_list_by_user_id(
        self,
        user_id: str,
        include_archived: bool = False,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[ChatTitleIdResponse]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id)
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))

            if not include_archived:
                query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc()).with_entities(
                Chat.id, Chat.title, Chat.updated_at, Chat.created_at, Chat.assistant_id
            )

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            all_chats = query.all()

            # result has to be destrctured from sqlalchemy `row` and mapped to a dict since the `ChatModel`is not the returned dataclass.
            return [
                ChatTitleIdResponse.model_validate(
                    {
                        "id": chat[0],
                        "title": chat[1],
                        "updated_at": chat[2],
                        "created_at": chat[3],
                        "assistant_id": chat[4],
                    }
                )
                for chat in all_chats
            ]

    def get_shared_chat_list_by_user_id(
        self, user_id: str
    ) -> list[ChatTitleIdResponse]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id)
                .filter(Chat.share_id != None)
                .order_by(Chat.updated_at.desc())
                .with_entities(
                    Chat.id, Chat.title, Chat.updated_at, Chat.created_at, Chat.share_id
                )
                .all()
            )
            return [
                {
                    "id": chat[0],
                    "title": chat[1],
                    "updated_at": chat[2],
                    "created_at": chat[3],
                    "share_id": chat[4],
                }
                for chat in all_chats
            ]

    def get_chat_list_by_chat_ids(
        self, chat_ids: list[str], skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter(Chat.id.in_(chat_ids))
                .filter_by(archived=False)
                .order_by(Chat.updated_at.desc())
                .all()
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def get_chat_by_share_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                # it is possible that the shared link was deleted. hence,
                # we check if the chat is still shared by checking if a chat with the share_id exists
                chat = db.query(Chat).filter_by(share_id=id).first()

                if chat:
                    return self.get_chat_by_id(chat.id)
                else:
                    return None
        except Exception:
            return None

    def get_chat_by_id_and_user_id(self, id: str, user_id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.query(Chat).filter_by(id=id, user_id=user_id).first()
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def get_chat_meta_by_id_and_user_id(self, id: str, user_id: str) -> Optional[dict]:
        try:
            with get_db() as db:
                row = (
                    db.query(Chat.meta)
                    .filter_by(id=id, user_id=user_id)
                    .first()
                )
                return row[0] if row else None
        except Exception:
            return None

    def get_chats(self, skip: int = 0, limit: int = 50) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                # .limit(limit).offset(skip)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_pinned_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, pinned=True, archived=False)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_archived_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, archived=True)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_user_id_and_search_text(
        self,
        user_id: str,
        search_text: str,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 60,
    ) -> list[ChatModel]:
        """
        Filters chats based on a search query using Python, allowing pagination using skip and limit.
        """
        search_text = search_text.lower().strip()

        if not search_text:
            return self.get_chat_list_by_user_id(user_id, include_archived, skip, limit)

        search_text_words = search_text.split(" ")

        # search_text might contain 'tag:tag_name' format so we need to extract the tag_name, split the search_text and remove the tags
        tag_ids = [
            word.replace("tag:", "").replace(" ", "_").lower()
            for word in search_text_words
            if word.startswith("tag:")
        ]

        search_text_words = [
            word for word in search_text_words if not word.startswith("tag:")
        ]

        search_text = " ".join(search_text_words)

        with get_db() as db:
            query = db.query(Chat).filter(Chat.user_id == user_id)

            if not include_archived:
                query = query.filter(Chat.archived == False)

            query = query.order_by(Chat.updated_at.desc())

            # Check if the database dialect is either 'sqlite' or 'postgresql'
            dialect_name = db.bind.dialect.name
            if dialect_name == "sqlite":
                # SQLite case: using JSON1 extension for JSON searching
                query = query.filter(
                    (
                        Chat.title.ilike(
                            f"%{search_text}%"
                        )  # Case-insensitive search in title
                        | text(
                            """
                            EXISTS (
                                SELECT 1
                                FROM json_each(Chat.chat, '$.history.messages') AS message
                                WHERE LOWER(COALESCE(message.value->>'content', '')) LIKE '%' || :search_text || '%'
                            )
                            OR EXISTS (
                                SELECT 1
                                FROM json_each(Chat.chat, '$.messages') AS message
                                WHERE LOWER(COALESCE(message.value->>'content', '')) LIKE '%' || :search_text || '%'
                            )
                            """
                        )
                    ).params(search_text=search_text)
                )

                # Check if there are any tags to filter, it should have all the tags
                if "none" in tag_ids:
                    query = query.filter(
                        text(
                            """
                            NOT EXISTS (
                                SELECT 1
                                FROM json_each(Chat.meta, '$.tags') AS tag
                            )
                            """
                        )
                    )
                elif tag_ids:
                    query = query.filter(
                        and_(
                            *[
                                text(
                                    f"""
                                    EXISTS (
                                        SELECT 1
                                        FROM json_each(Chat.meta, '$.tags') AS tag
                                        WHERE tag.value = :tag_id_{tag_idx}
                                    )
                                    """
                                ).params(**{f"tag_id_{tag_idx}": tag_id})
                                for tag_idx, tag_id in enumerate(tag_ids)
                            ]
                        )
                    )

            elif dialect_name == "postgresql":
                # PostgreSQL relies on proper JSON query for search
                query = query.filter(
                    (
                        Chat.title.ilike(
                            f"%{search_text}%"
                        )  # Case-insensitive search in title
                        | text(
                            """
                            EXISTS (
                                SELECT 1
                                FROM json_each(COALESCE(Chat.chat->'history'->'messages', '{}'::json)) AS message(key, value)
                                WHERE LOWER(COALESCE(message.value->>'content', '')) LIKE '%' || :search_text || '%'
                            )
                            OR EXISTS (
                                SELECT 1
                                FROM json_array_elements(COALESCE(Chat.chat->'messages', '[]'::json)) AS message
                                WHERE LOWER(COALESCE(message->>'content', '')) LIKE '%' || :search_text || '%'
                            )
                            """
                        )
                    ).params(search_text=search_text)
                )

                # Check if there are any tags to filter, it should have all the tags
                if "none" in tag_ids:
                    query = query.filter(
                        text(
                            """
                            NOT EXISTS (
                                SELECT 1
                                FROM json_array_elements_text(Chat.meta->'tags') AS tag
                            )
                            """
                        )
                    )
                elif tag_ids:
                    query = query.filter(
                        and_(
                            *[
                                text(
                                    f"""
                                    EXISTS (
                                        SELECT 1
                                        FROM json_array_elements_text(Chat.meta->'tags') AS tag
                                        WHERE tag = :tag_id_{tag_idx}
                                    )
                                    """
                                ).params(**{f"tag_id_{tag_idx}": tag_id})
                                for tag_idx, tag_id in enumerate(tag_ids)
                            ]
                        )
                    )
            else:
                raise NotImplementedError(
                    f"Unsupported dialect: {db.bind.dialect.name}"
                )

            # Perform pagination at the SQL level
            all_chats = query.offset(skip).limit(limit).all()

            log.info(f"The number of chats: {len(all_chats)}")

            # Validate and return chats
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_folder_id_and_user_id(
        self,
        folder_id: str,
        user_id: str,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(folder_id=folder_id, user_id=user_id)
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))
            query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc())

            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_folder_ids_and_user_id(
        self, folder_ids: list[str], user_id: str
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter(
                Chat.folder_id.in_(folder_ids), Chat.user_id == user_id
            )
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))
            query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc())

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_assistant_id_and_user_id(
        self,
        assistant_id: str,
        user_id: str,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(
                assistant_id=assistant_id,
                user_id=user_id,
            )
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))
            query = query.filter_by(archived=False)
            query = query.order_by(Chat.updated_at.desc())

            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def update_chat_folder_id_by_id_and_user_id(
        self, id: str, user_id: str, folder_id: Optional[str]
    ) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.folder_id = folder_id
                chat.updated_at = self._next_user_chat_timestamp(db, chat.user_id)
                chat.pinned = False
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def count_chats_by_folder_id_and_user_id(
        self, folder_id: str, user_id: str
    ) -> int:
        try:
            with get_db() as db:
                return db.query(Chat).filter_by(folder_id=folder_id, user_id=user_id).count()
        except Exception:
            return 0

    def get_chat_tags_by_id_and_user_id(self, id: str, user_id: str) -> list[TagModel]:
        with get_db() as db:
            chat = db.get(Chat, id)
            tags = chat.meta.get("tags", [])
            tag_ids = [t.replace(" ", "_").lower() for t in tags]
            return Tags.get_tags_by_ids_and_user_id(tag_ids, user_id)

    def get_chat_list_by_user_id_and_tag_name(
        self, user_id: str, tag_name: str, skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id)
            tag_id = tag_name.replace(" ", "_").lower()

            log.info(f"DB dialect name: {db.bind.dialect.name}")
            if db.bind.dialect.name == "sqlite":
                # SQLite JSON1 querying for tags within the meta JSON field
                query = query.filter(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(Chat.meta, '$.tags') WHERE json_each.value = :tag_id)"
                    )
                ).params(tag_id=tag_id)
            elif db.bind.dialect.name == "postgresql":
                # PostgreSQL JSON query for tags within the meta JSON field (for `json` type)
                query = query.filter(
                    text(
                        "EXISTS (SELECT 1 FROM json_array_elements_text(Chat.meta->'tags') elem WHERE elem = :tag_id)"
                    )
                ).params(tag_id=tag_id)
            else:
                raise NotImplementedError(
                    f"Unsupported dialect: {db.bind.dialect.name}"
                )

            all_chats = query.all()
            log.debug(f"all_chats: {all_chats}")
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def add_chat_tag_by_id_and_user_id_and_tag_name(
        self, id: str, user_id: str, tag_name: str
    ) -> Optional[ChatModel]:
        tag = Tags.get_tag_by_name_and_user_id(tag_name, user_id)
        if tag is None:
            tag = Tags.insert_new_tag(tag_name, user_id)
        try:
            with get_db() as db:
                chat = db.get(Chat, id)

                tag_id = tag.id
                if tag_id not in chat.meta.get("tags", []):
                    chat.meta = {
                        **chat.meta,
                        "tags": list(set(chat.meta.get("tags", []) + [tag_id])),
                    }

                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def count_chats_by_tag_name_and_user_id(self, tag_name: str, user_id: str) -> int:
        with get_db() as db:  # Assuming `get_db()` returns a session object
            query = db.query(Chat).filter_by(user_id=user_id, archived=False)

            # Normalize the tag_name for consistency
            tag_id = tag_name.replace(" ", "_").lower()

            if db.bind.dialect.name == "sqlite":
                # SQLite JSON1 support for querying the tags inside the `meta` JSON field
                query = query.filter(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(Chat.meta, '$.tags') WHERE json_each.value = :tag_id)"
                    )
                ).params(tag_id=tag_id)

            elif db.bind.dialect.name == "postgresql":
                # PostgreSQL JSONB support for querying the tags inside the `meta` JSON field
                query = query.filter(
                    text(
                        "EXISTS (SELECT 1 FROM json_array_elements_text(Chat.meta->'tags') elem WHERE elem = :tag_id)"
                    )
                ).params(tag_id=tag_id)

            else:
                raise NotImplementedError(
                    f"Unsupported dialect: {db.bind.dialect.name}"
                )

            # Get the count of matching records
            count = query.count()

            # Debugging output for inspection
            log.info(f"Count of chats for tag '{tag_name}': {count}")

            return count

    def delete_tag_by_id_and_user_id_and_tag_name(
        self, id: str, user_id: str, tag_name: str
    ) -> bool:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                tags = chat.meta.get("tags", [])
                tag_id = tag_name.replace(" ", "_").lower()

                tags = [tag for tag in tags if tag != tag_id]
                chat.meta = {
                    **chat.meta,
                    "tags": list(set(tags)),
                }
                db.commit()
                return True
        except Exception:
            return False

    def delete_all_tags_by_id_and_user_id(self, id: str, user_id: str) -> bool:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.meta = {
                    **chat.meta,
                    "tags": [],
                }
                db.commit()

                return True
        except Exception:
            return False

    def delete_chat_by_id(self, id: str) -> bool:
        try:
            # Clean up chat_message table first
            ChatMessages.delete_messages_by_chat_id(id)

            with get_db() as db:
                db.query(Chat).filter_by(id=id).delete()
                db.commit()

                return True and self.delete_shared_chat_by_chat_id(id)
        except Exception:
            return False

    def delete_chat_by_id_and_user_id(self, id: str, user_id: str) -> bool:
        try:
            # Clean up chat_message table first
            ChatMessages.delete_messages_by_chat_id(id)

            with get_db() as db:
                db.query(Chat).filter_by(id=id, user_id=user_id).delete()
                db.commit()

                return True and self.delete_shared_chat_by_chat_id(id)
        except Exception:
            return False

    def delete_chats_by_user_id(self, user_id: str) -> bool:
        try:
            # Clean up chat_message table first
            ChatMessages.delete_messages_by_user_id(user_id)

            with get_db() as db:
                self.delete_shared_chats_by_user_id(user_id)

                db.query(Chat).filter_by(user_id=user_id).delete()
                db.commit()

                return True
        except Exception:
            return False

    def delete_chats_by_user_id_and_folder_id(
        self, user_id: str, folder_id: str
    ) -> bool:
        try:
            with get_db() as db:
                # Clean up chat_message table first
                chat_ids = [
                    c.id
                    for c in db.query(Chat.id)
                    .filter_by(user_id=user_id, folder_id=folder_id)
                    .all()
                ]
                if chat_ids:
                    db.query(ChatMessage).filter(
                        ChatMessage.chat_id.in_(chat_ids)
                    ).delete(synchronize_session=False)

                db.query(Chat).filter_by(user_id=user_id, folder_id=folder_id).delete()
                db.commit()

                return True
        except Exception:
            return False

    def move_chats_by_user_id_and_folder_id(
        self, user_id: str, folder_id: str, new_folder_id: Optional[str]
    ) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=user_id, folder_id=folder_id).update(
                    {"folder_id": new_folder_id}
                )
                db.commit()
                return True
        except Exception:
            return False

    def delete_shared_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                chats_by_user = db.query(Chat).filter_by(user_id=user_id).all()
                shared_chat_ids = [f"shared-{chat.id}" for chat in chats_by_user]

                db.query(Chat).filter(Chat.user_id.in_(shared_chat_ids)).delete()
                db.commit()

                return True
        except Exception:
            return False


Chats = ChatTable()


####################
# ChatMessage Table Operations
####################


class ChatMessageTable:
    def upsert_message(
        self,
        chat_id: str,
        user_id: str,
        message_id: str,
        message: dict,
    ) -> Optional[ChatMessageModel]:
        """
        Insert or update a message in the chat_message table.
        Called as part of dual-write from ChatTable.upsert_message_to_chat_by_id_and_message_id.
        """
        try:
            now = int(time.time())
            role = message.get("role", "")
            content = message.get("content", "")

            # Handle content that might be a list (multimodal format)
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content = "\n".join(text_parts)
            elif not isinstance(content, str):
                content = str(content) if content is not None else ""

            parent_id = message.get("parentId")
            model = message.get("model")

            # Extract usage tokens
            usage = message.get("usage", {}) or {}
            if not isinstance(usage, dict):
                usage = {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")

            # Collect non-core fields into meta
            meta_keys = {
                "files", "sources", "code_executions", "statusHistory",
                "childrenIds", "models", "modelName", "modelIdx", "model_ref",
                "done", "error", "info", "completedAt", "userContext",
                "merged", "lastSentence", "originalContent",
            }
            meta = {k: v for k, v in message.items() if k in meta_keys and v is not None}

            created_at = message.get("timestamp", now)

            with get_db() as db:
                existing = db.query(ChatMessage).filter_by(id=message_id).first()
                if existing:
                    existing.content = content
                    existing.parent_id = parent_id
                    existing.model = model
                    existing.prompt_tokens = prompt_tokens
                    existing.completion_tokens = completion_tokens
                    existing.meta = meta if meta else existing.meta
                    existing.updated_at = now
                    db.commit()
                    db.refresh(existing)
                    return ChatMessageModel.model_validate(existing)
                else:
                    new_msg = ChatMessage(
                        id=message_id,
                        chat_id=chat_id,
                        user_id=user_id,
                        role=role,
                        content=content,
                        parent_id=parent_id,
                        model=model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        meta=meta if meta else None,
                        created_at=created_at,
                        updated_at=now,
                    )
                    db.add(new_msg)
                    db.commit()
                    db.refresh(new_msg)
                    return ChatMessageModel.model_validate(new_msg)
        except Exception as e:
            log.error(f"ChatMessageTable.upsert_message error: {e}")
            return None

    def get_messages_by_chat_id(self, chat_id: str) -> list[ChatMessageModel]:
        with get_db() as db:
            messages = (
                db.query(ChatMessage)
                .filter_by(chat_id=chat_id)
                .order_by(ChatMessage.created_at.asc())
                .all()
            )
            return [ChatMessageModel.model_validate(m) for m in messages]

    def get_messages_by_user_id(
        self, user_id: str, skip: int = 0, limit: int = 100
    ) -> list[ChatMessageModel]:
        with get_db() as db:
            messages = (
                db.query(ChatMessage)
                .filter_by(user_id=user_id)
                .order_by(ChatMessage.created_at.desc())
                .offset(skip)
                .limit(limit)
                .all()
            )
            return [ChatMessageModel.model_validate(m) for m in messages]

    def get_usage_by_model(
        self, model: str, user_id: Optional[str] = None
    ) -> dict:
        """Get aggregate token usage for analytics."""
        with get_db() as db:
            query = db.query(
                func.count(ChatMessage.id).label("message_count"),
                func.sum(ChatMessage.prompt_tokens).label("total_prompt_tokens"),
                func.sum(ChatMessage.completion_tokens).label("total_completion_tokens"),
            ).filter(
                ChatMessage.model == model,
                ChatMessage.role == "assistant",
            )
            if user_id:
                query = query.filter(ChatMessage.user_id == user_id)
            result = query.first()
            return {
                "message_count": result.message_count or 0,
                "total_prompt_tokens": result.total_prompt_tokens or 0,
                "total_completion_tokens": result.total_completion_tokens or 0,
            }

    def get_usage_summary(self) -> list[dict]:
        """Get token usage grouped by model for analytics dashboard."""
        with get_db() as db:
            results = (
                db.query(
                    ChatMessage.model,
                    func.count(ChatMessage.id).label("message_count"),
                    func.sum(ChatMessage.prompt_tokens).label("total_prompt_tokens"),
                    func.sum(ChatMessage.completion_tokens).label("total_completion_tokens"),
                )
                .filter(
                    ChatMessage.role == "assistant",
                    ChatMessage.model.isnot(None),
                )
                .group_by(ChatMessage.model)
                .all()
            )
            return [
                {
                    "model": r.model,
                    "message_count": r.message_count or 0,
                    "total_prompt_tokens": r.total_prompt_tokens or 0,
                    "total_completion_tokens": r.total_completion_tokens or 0,
                }
                for r in results
            ]

    def delete_messages_by_chat_id(self, chat_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(ChatMessage).filter_by(chat_id=chat_id).delete()
                db.commit()
                return True
        except Exception:
            return False

    def delete_messages_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(ChatMessage).filter_by(user_id=user_id).delete()
                db.commit()
                return True
        except Exception:
            return False


ChatMessages = ChatMessageTable()


####################
# ChatReaction Operations
####################


# [REACTION_FEATURE] Commented out - reaction feature disabled for now
# class ChatReactionTable:
#     def add_reaction(
#         self, chat_id: str, message_id: str, user_id: str, name: str
#     ) -> Optional[ChatReactionModel]:
#         with get_db() as db:
#             existing = (
#                 db.query(ChatReaction)
#                 .filter_by(chat_id=chat_id, message_id=message_id, user_id=user_id, name=name)
#                 .first()
#             )
#             if existing:
#                 return ChatReactionModel.model_validate(existing)
#
#             reaction = ChatReaction(
#                 id=str(uuid.uuid4()),
#                 user_id=user_id,
#                 chat_id=chat_id,
#                 message_id=message_id,
#                 name=name,
#                 created_at=int(time.time_ns()),
#             )
#             db.add(reaction)
#             db.commit()
#             db.refresh(reaction)
#             return ChatReactionModel.model_validate(reaction)
#
#     def remove_reaction(
#         self, chat_id: str, message_id: str, user_id: str, name: str
#     ) -> bool:
#         with get_db() as db:
#             result = (
#                 db.query(ChatReaction)
#                 .filter_by(chat_id=chat_id, message_id=message_id, user_id=user_id, name=name)
#                 .delete()
#             )
#             db.commit()
#             return result > 0
#
#     def get_reactions_by_chat_and_message(
#         self, chat_id: str, message_id: str
#     ) -> list[ChatReactionSummary]:
#         with get_db() as db:
#             reactions = (
#                 db.query(ChatReaction)
#                 .filter_by(chat_id=chat_id, message_id=message_id)
#                 .all()
#             )
#
#             grouped: dict[str, list[str]] = {}
#             for r in reactions:
#                 if r.name not in grouped:
#                     grouped[r.name] = []
#                 grouped[r.name].append(r.user_id)
#
#             return [
#                 ChatReactionSummary(name=name, user_ids=uids, count=len(uids))
#                 for name, uids in grouped.items()
#             ]
#
#     def delete_reactions_by_chat_id(self, chat_id: str) -> bool:
#         try:
#             with get_db() as db:
#                 db.query(ChatReaction).filter_by(chat_id=chat_id).delete()
#                 db.commit()
#                 return True
#         except Exception:
#             return False
#
#
# ChatReactions = ChatReactionTable()
