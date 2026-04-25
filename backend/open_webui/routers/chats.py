import json
import logging
import time
import uuid
from copy import deepcopy
from typing import Literal, Optional


from open_webui.socket.main import get_event_emitter
from open_webui.models.chats import (
    ChatForm,
    ChatComposerStateForm,
    ChatImportForm,
    ChatResponse,
    Chats,
    ChatMessages,
    ChatTitleIdResponse,
    normalize_chat_payload,
    # [REACTION_FEATURE] Commented out - reaction feature disabled for now
    # ChatReactions,
    # ChatReactionSummary,
)
from open_webui.models.tags import TagModel, Tags
from open_webui.models.folders import Folders

from open_webui.config import ENABLE_ADMIN_CHAT_ACCESS, ENABLE_ADMIN_EXPORT
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS, FOLDER_MAX_ITEM_COUNT
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field


from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_permission
from open_webui.utils.chat_image_refs import normalize_chat_payload_image_refs
from open_webui.tasks import list_task_ids_by_chat_id

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


def _chat_response(chat) -> Optional[ChatResponse]:
    if chat is None:
        return None

    payload = chat.model_dump()
    payload["chat"] = normalize_chat_payload(payload.get("chat"))
    return ChatResponse(**payload)


def _chat_response_list(chats) -> list[ChatResponse]:
    return [_chat_response(chat) for chat in chats]


class ChatContextResponse(BaseModel):
    tags: list[TagModel] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)


class ChatImportItemForm(BaseModel):
    chat: dict
    meta: dict = Field(default_factory=dict)
    pinned: Optional[bool] = False
    folder_id: Optional[str] = None
    assistant_id: Optional[str] = None


class ChatBatchImportForm(BaseModel):
    items: list[ChatImportItemForm] = Field(default_factory=list)
    mode: Literal["merge", "replace"] = "merge"


class ChatImportFailure(BaseModel):
    index: int
    title: str
    detail: str


class ChatBatchImportResponse(BaseModel):
    mode: Literal["merge", "replace"]
    total: int
    imported: int
    failed: int
    failures: list[ChatImportFailure] = Field(default_factory=list)


def _sync_imported_chat_tags(chat, user_id: str) -> None:
    tags = chat.meta.get("tags", []) if chat else []
    for tag_id in tags:
        tag_id = tag_id.replace(" ", "_").lower()
        tag_name = " ".join([word.capitalize() for word in tag_id.split("_")])
        if (
            tag_id != "none"
            and Tags.get_tag_by_name_and_user_id(tag_name, user_id) is None
        ):
            Tags.insert_new_tag(tag_name, user_id)


async def _normalize_chat_payload_images(chat_payload: dict, user) -> tuple[dict, set[str]]:
    return await run_in_threadpool(
        lambda: normalize_chat_payload_image_refs(
            chat_payload,
            user_id=user.id,
            is_admin=user.role == "admin",
        )
    )


def _sync_changed_chat_messages(
    chat_id: str, user_id: str, chat_payload: dict, changed_message_ids: set[str]
) -> None:
    if not changed_message_ids:
        return

    messages = chat_payload.get("history", {}).get("messages", {}) or {}
    for message_id in changed_message_ids:
        message = messages.get(message_id)
        if isinstance(message, dict):
            ChatMessages.upsert_message(
                chat_id=chat_id,
                user_id=user_id,
                message_id=message_id,
                message=message,
            )


BRANCH_FILE_TYPES = {"doc", "file", "collection"}


def _build_branch_chain(
    history: dict, branch_point_message_id: str
) -> tuple[list[str], dict[str, str]]:
    messages = history.get("messages", {}) or {}
    chain_ids: list[str] = []
    visited: set[str] = set()
    current_id: Optional[str] = branch_point_message_id

    while current_id is not None:
        if current_id in visited:
            raise ValueError("Cycle detected while building branch chain.")

        message = messages.get(current_id)
        if not isinstance(message, dict):
            raise ValueError("Branch point message was not found in chat history.")

        chain_ids.append(current_id)
        visited.add(current_id)

        parent_id = message.get("parentId")
        current_id = parent_id if isinstance(parent_id, str) and parent_id else None

    chain_ids.reverse()
    return chain_ids, {message_id: str(uuid.uuid4()) for message_id in chain_ids}


def _build_branch_history(
    history: dict, branch_point_message_id: str
) -> tuple[dict, list[str], dict[str, str]]:
    if not isinstance(history, dict):
        raise ValueError("Chat history is missing or invalid.")

    messages = history.get("messages", {}) or {}
    if not isinstance(messages, dict):
        raise ValueError("Chat history messages are missing or invalid.")

    chain_ids, message_id_map = _build_branch_chain(history, branch_point_message_id)
    branched_messages: dict[str, dict] = {}

    for index, original_message_id in enumerate(chain_ids):
        original_message = messages.get(original_message_id)
        if not isinstance(original_message, dict):
            raise ValueError("Encountered an invalid message while building branch.")

        cloned_message = deepcopy(original_message)
        cloned_message_id = message_id_map[original_message_id]
        cloned_message["id"] = cloned_message_id
        cloned_message["parentId"] = (
            message_id_map[chain_ids[index - 1]] if index > 0 else None
        )
        cloned_message["childrenIds"] = (
            [message_id_map[chain_ids[index + 1]]]
            if index + 1 < len(chain_ids)
            else []
        )
        branched_messages[cloned_message_id] = cloned_message

    return {
        "messages": branched_messages,
        "currentId": message_id_map[branch_point_message_id],
    }, chain_ids, message_id_map


def _collect_branch_files(history_messages: dict, chain_ids: list[str]) -> list[dict]:
    files: list[dict] = []
    seen: set[str] = set()

    for message_id in chain_ids:
        message = history_messages.get(message_id)
        if not isinstance(message, dict):
            continue

        for file_item in message.get("files") or []:
            if not isinstance(file_item, dict):
                continue
            if file_item.get("type") not in BRANCH_FILE_TYPES:
                continue

            serialized = json.dumps(file_item, sort_keys=True, ensure_ascii=False)
            if serialized in seen:
                continue

            seen.add(serialized)
            files.append(deepcopy(file_item))

    return files


def _remap_branch_selection_threads(
    selection_threads: object, message_id_map: dict[str, str]
) -> dict:
    if not isinstance(selection_threads, dict):
        return {"version": 1, "items": []}

    items = selection_threads.get("items", [])
    if not isinstance(items, list):
        items = []

    remapped_items: list[dict] = []
    for thread in items:
        if not isinstance(thread, dict):
            continue

        source_message_id = thread.get("sourceMessageId")
        if not isinstance(source_message_id, str):
            continue

        new_source_message_id = message_id_map.get(source_message_id)
        if not new_source_message_id:
            continue

        remapped_thread = deepcopy(thread)
        remapped_thread["sourceMessageId"] = new_source_message_id
        remapped_items.append(remapped_thread)

    version = selection_threads.get("version")
    return {
        "version": version if isinstance(version, int) else 1,
        "items": remapped_items,
    }


def _build_branch_chat_payload(
    source_chat: dict,
    source_chat_id: str,
    branch_point_message_id: str,
    title: str,
) -> dict:
    if not isinstance(source_chat, dict):
        raise ValueError("Chat payload is missing or invalid.")

    payload = deepcopy(source_chat)
    history = payload.get("history")
    branched_history, chain_ids, message_id_map = _build_branch_history(
        history, branch_point_message_id
    )

    history_messages = history.get("messages", {}) if isinstance(history, dict) else {}

    payload["history"] = branched_history
    payload["files"] = _collect_branch_files(history_messages, chain_ids)
    payload["selectionThreads"] = _remap_branch_selection_threads(
        payload.get("selectionThreads"), message_id_map
    )
    payload["title"] = title
    payload["timestamp"] = int(time.time() * 1000)
    payload["originalChatId"] = source_chat_id
    payload["branchPointMessageId"] = branch_point_message_id
    payload.pop("messages", None)

    return payload

############################
# GetChatList
############################


@router.get("/", response_model=list[ChatTitleIdResponse])
@router.get("/list", response_model=list[ChatTitleIdResponse])
async def get_session_user_chat_list(
    user=Depends(get_verified_user), page: Optional[int] = None
):
    if page is not None:
        limit = 60
        skip = (page - 1) * limit

        return Chats.get_chat_title_id_list_by_user_id(user.id, skip=skip, limit=limit)
    else:
        return Chats.get_chat_title_id_list_by_user_id(user.id)


############################
# DeleteAllChats
############################


@router.delete("/", response_model=bool)
async def delete_all_user_chats(request: Request, user=Depends(get_verified_user)):

    if user.role == "user" and not has_permission(
        user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Chats.delete_chats_by_user_id(user.id)
    return result


############################
# GetUserChatList
############################


@router.get("/list/user/{user_id}", response_model=list[ChatTitleIdResponse])
async def get_user_chat_list_by_user_id(
    user_id: str,
    user=Depends(get_admin_user),
    skip: int = 0,
    limit: int = 50,
):
    if not ENABLE_ADMIN_CHAT_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return Chats.get_chat_list_by_user_id(
        user_id, include_archived=True, skip=skip, limit=limit
    )


############################
# CreateNewChat
############################


@router.post("/new", response_model=Optional[ChatResponse])
async def create_new_chat(form_data: ChatForm, user=Depends(get_verified_user)):
    try:
        normalized_chat, changed_message_ids = await _normalize_chat_payload_images(
            form_data.chat, user
        )
        chat = Chats.insert_new_chat(
            user.id,
            ChatForm(
                chat=normalized_chat,
                folder_id=form_data.folder_id,
                assistant_id=form_data.assistant_id,
            ),
        )
        if chat and changed_message_ids:
            await run_in_threadpool(
                lambda: _sync_changed_chat_messages(
                    chat.id, user.id, normalized_chat, changed_message_ids
                )
            )
        return _chat_response(chat)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ImportChat
############################


@router.post("/import", response_model=Optional[ChatResponse])
async def import_chat(form_data: ChatImportForm, user=Depends(get_verified_user)):
    try:
        normalized_chat, changed_message_ids = await _normalize_chat_payload_images(
            form_data.chat, user
        )
        chat = Chats.import_chat(
            user.id,
            ChatImportForm(
                chat=normalized_chat,
                meta=form_data.meta,
                pinned=form_data.pinned,
                folder_id=form_data.folder_id,
                assistant_id=form_data.assistant_id,
            ),
        )
        if chat and changed_message_ids:
            await run_in_threadpool(
                lambda: _sync_changed_chat_messages(
                    chat.id, user.id, normalized_chat, changed_message_ids
                )
            )
        _sync_imported_chat_tags(chat, user.id)
        return _chat_response(chat)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


@router.post("/import/batch", response_model=ChatBatchImportResponse)
async def import_chats_batch(
    form_data: ChatBatchImportForm, user=Depends(get_verified_user)
):
    total = len(form_data.items)

    if form_data.mode == "replace":
        try:
            import_forms = []
            normalized_entries: list[tuple[dict, set[str]]] = []
            for item in form_data.items:
                normalized_chat, changed_message_ids = await _normalize_chat_payload_images(
                    item.chat, user
                )
                import_forms.append(
                    ChatImportForm(
                        chat=normalized_chat,
                        meta=item.meta,
                        pinned=item.pinned,
                        folder_id=item.folder_id,
                        assistant_id=item.assistant_id,
                    )
                )
                normalized_entries.append((normalized_chat, changed_message_ids))
            chats = Chats.replace_chats_by_user_id(user.id, import_forms)
            for idx, chat in enumerate(chats):
                normalized_chat, changed_message_ids = normalized_entries[idx]
                if changed_message_ids:
                    await run_in_threadpool(
                        lambda chat_id=chat.id, payload=normalized_chat, changed_ids=changed_message_ids: _sync_changed_chat_messages(
                            chat_id, user.id, payload, changed_ids
                        )
                    )
                _sync_imported_chat_tags(chat, user.id)

            return ChatBatchImportResponse(
                mode=form_data.mode,
                total=total,
                imported=len(chats),
                failed=0,
                failures=[],
            )
        except Exception as e:
            log.exception(e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e) or ERROR_MESSAGES.DEFAULT(),
            )

    imported = 0
    failures: list[ChatImportFailure] = []

    for index, item in enumerate(form_data.items):
        try:
            normalized_chat, changed_message_ids = await _normalize_chat_payload_images(
                item.chat, user
            )
            chat = Chats.import_chat(
                user.id,
                ChatImportForm(
                    chat=normalized_chat,
                    meta=item.meta,
                    pinned=item.pinned,
                    folder_id=item.folder_id,
                    assistant_id=item.assistant_id,
                ),
            )
            if chat is None:
                failures.append(
                    ChatImportFailure(
                        index=index,
                        title=item.chat.get("title", "New Chat"),
                        detail=ERROR_MESSAGES.DEFAULT(),
                    )
                )
                continue

            if changed_message_ids:
                await run_in_threadpool(
                    lambda: _sync_changed_chat_messages(
                        chat.id, user.id, normalized_chat, changed_message_ids
                    )
                )
            _sync_imported_chat_tags(chat, user.id)
            imported += 1
        except Exception as e:
            log.exception(e)
            failures.append(
                ChatImportFailure(
                    index=index,
                    title=item.chat.get("title", "New Chat"),
                    detail=str(e) or ERROR_MESSAGES.DEFAULT(),
                )
            )

    return ChatBatchImportResponse(
        mode=form_data.mode,
        total=total,
        imported=imported,
        failed=len(failures),
        failures=failures,
    )


############################
# GetChats
############################


@router.get("/search", response_model=list[ChatTitleIdResponse])
async def search_user_chats(
    text: str, page: Optional[int] = None, user=Depends(get_verified_user)
):
    if page is None:
        page = 1

    limit = 60
    skip = (page - 1) * limit

    chat_list = [
        ChatTitleIdResponse(**chat.model_dump())
        for chat in Chats.get_chats_by_user_id_and_search_text(
            user.id, text, skip=skip, limit=limit
        )
    ]

    # Delete tag if no chat is found
    words = text.strip().split(" ")
    if page == 1 and len(words) == 1 and words[0].startswith("tag:"):
        tag_id = words[0].replace("tag:", "")
        if len(chat_list) == 0:
            if Tags.get_tag_by_name_and_user_id(tag_id, user.id):
                log.debug(f"deleting tag: {tag_id}")
                Tags.delete_tag_by_name_and_user_id(tag_id, user.id)

    return chat_list


############################
# GetChatsByFolderId
############################


@router.get("/folder/{folder_id}", response_model=list[ChatResponse])
async def get_chats_by_folder_id(folder_id: str, user=Depends(get_verified_user)):
    folder_ids = [folder_id]
    children_folders = Folders.get_children_folders_by_id_and_user_id(
        folder_id, user.id
    )
    if children_folders:
        folder_ids.extend([folder.id for folder in children_folders])

    return _chat_response_list(
        Chats.get_chats_by_folder_ids_and_user_id(folder_ids, user.id)
    )


@router.get("/folder/{folder_id}/list", response_model=list[ChatTitleIdResponse])
async def get_chat_list_by_folder_id(
    folder_id: str, page: Optional[int] = 1, user=Depends(get_verified_user)
):
    try:
        limit = 10
        skip = max((page or 1) - 1, 0) * limit

        return [
            ChatTitleIdResponse(
                id=chat.id,
                title=chat.title,
                updated_at=chat.updated_at,
                created_at=chat.created_at,
                assistant_id=chat.assistant_id,
            )
            for chat in Chats.get_chats_by_folder_id_and_user_id(
                folder_id, user.id, skip=skip, limit=limit
            )
        ]
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )


@router.get("/assistant/{assistant_id}/list", response_model=list[ChatTitleIdResponse])
async def get_chat_list_by_assistant_id(
    assistant_id: str, page: Optional[int] = 1, user=Depends(get_verified_user)
):
    try:
        limit = 10
        skip = max((page or 1) - 1, 0) * limit

        return [
            ChatTitleIdResponse(
                id=chat.id,
                title=chat.title,
                updated_at=chat.updated_at,
                created_at=chat.created_at,
                assistant_id=chat.assistant_id,
            )
            for chat in Chats.get_chats_by_assistant_id_and_user_id(
                assistant_id, user.id, skip=skip, limit=limit
            )
        ]
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )


############################
# GetPinnedChats
############################


@router.get("/pinned", response_model=list[ChatResponse])
async def get_user_pinned_chats(user=Depends(get_verified_user)):
    return _chat_response_list(Chats.get_pinned_chats_by_user_id(user.id))


############################
# GetChats
############################


@router.get("/all", response_model=list[ChatResponse])
async def get_user_chats(user=Depends(get_verified_user)):
    return _chat_response_list(Chats.get_chats_by_user_id(user.id))


############################
# GetArchivedChats
############################


@router.get("/all/archived", response_model=list[ChatResponse])
async def get_user_archived_chats(user=Depends(get_verified_user)):
    return _chat_response_list(Chats.get_archived_chats_by_user_id(user.id))


############################
# GetAllTags
############################


@router.get("/all/tags", response_model=list[TagModel])
async def get_all_user_tags(user=Depends(get_verified_user)):
    try:
        tags = Tags.get_tags_by_user_id(user.id)
        return tags
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetAllChatsInDB
############################


@router.get("/all/db", response_model=list[ChatResponse])
async def get_all_user_chats_in_db(user=Depends(get_admin_user)):
    if not ENABLE_ADMIN_EXPORT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return _chat_response_list(Chats.get_chats())


############################
# GetArchivedChats
############################


@router.get("/archived", response_model=list[ChatTitleIdResponse])
async def get_archived_session_user_chat_list(
    user=Depends(get_verified_user), skip: int = 0, limit: int = 50
):
    return Chats.get_archived_chat_list_by_user_id(user.id, skip, limit)


############################
# ArchiveAllChats
############################


@router.post("/archive/all", response_model=bool)
async def archive_all_chats(user=Depends(get_verified_user)):
    return Chats.archive_all_chats_by_user_id(user.id)


############################
# GetUserSharedChats
############################


@router.get("/shared", response_model=list[dict])
async def get_user_shared_chats(user=Depends(get_verified_user)):
    return Chats.get_shared_chat_list_by_user_id(user.id)


############################
# GetSharedChatById
############################


@router.get("/share/{share_id}", response_model=Optional[ChatResponse])
async def get_shared_chat_by_id(share_id: str, user=Depends(get_verified_user)):
    if user.role == "pending":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if user.role == "user" or (user.role == "admin" and not ENABLE_ADMIN_CHAT_ACCESS):
        chat = Chats.get_chat_by_share_id(share_id)
    elif user.role == "admin" and ENABLE_ADMIN_CHAT_ACCESS:
        chat = Chats.get_chat_by_id(share_id)

    if chat:
        return _chat_response(chat)

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# GetChatsByTags
############################


class TagForm(BaseModel):
    name: str


class TagFilterForm(TagForm):
    skip: Optional[int] = 0
    limit: Optional[int] = 50


@router.post("/tags", response_model=list[ChatTitleIdResponse])
async def get_user_chat_list_by_tag_name(
    form_data: TagFilterForm, user=Depends(get_verified_user)
):
    chats = Chats.get_chat_list_by_user_id_and_tag_name(
        user.id, form_data.name, form_data.skip, form_data.limit
    )
    if len(chats) == 0:
        Tags.delete_tag_by_name_and_user_id(form_data.name, user.id)

    return chats


############################
# GetChatById
############################


@router.get("/{id}", response_model=Optional[ChatResponse])
async def get_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)

    if chat:
        return _chat_response(chat)

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


@router.get("/{id}/context", response_model=ChatContextResponse)
async def get_chat_context_by_id(id: str, user=Depends(get_verified_user)):
    chat_meta = Chats.get_chat_meta_by_id_and_user_id(id, user.id)
    if chat_meta is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )

    tag_models: list[TagModel] = []
    task_ids: list[str] = []

    try:
        tag_ids = chat_meta.get("tags", [])
        tag_models = Tags.get_tags_by_ids_and_user_id(tag_ids, user.id)
    except Exception:
        log.exception("Failed to load chat tags for chat_id=%s", id)

    try:
        task_ids = list_task_ids_by_chat_id(id, blocks_completion_only=True)
    except Exception:
        log.exception("Failed to load chat task ids for chat_id=%s", id)

    return ChatContextResponse(tags=tag_models, task_ids=task_ids)


############################
# UpdateChatById
############################


@router.post("/{id}", response_model=Optional[ChatResponse])
async def update_chat_by_id(
    id: str,
    form_data: ChatForm,
    user=Depends(get_verified_user),
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        updated_chat = {**chat.chat, **form_data.chat}
        normalized_chat, changed_message_ids = await _normalize_chat_payload_images(
            updated_chat, user
        )
        chat = Chats.update_chat_by_id(id, normalized_chat)
        if chat and changed_message_ids:
            await run_in_threadpool(
                lambda: _sync_changed_chat_messages(
                    id, user.id, normalized_chat, changed_message_ids
                )
            )
        return _chat_response(chat)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# UpdateChatComposerStateById
############################


@router.post("/{id}/composer-state", response_model=Optional[ChatResponse])
async def update_chat_composer_state_by_id(
    id: str,
    form_data: ChatComposerStateForm,
    user=Depends(get_verified_user),
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        updated = Chats.update_chat_composer_state_by_id(
            id,
            form_data.composer_state if isinstance(form_data.composer_state, dict) else {},
        )
        return _chat_response(updated)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
    )


############################
# UpdateChatMessageById
############################
class MessageForm(BaseModel):
    content: str


@router.post("/{id}/messages/{message_id}", response_model=Optional[ChatResponse])
async def update_chat_message_by_id(
    id: str, message_id: str, form_data: MessageForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id(id)

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if chat.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    chat = Chats.upsert_message_to_chat_by_id_and_message_id(
        id,
        message_id,
        {
            "content": form_data.content,
        },
    )

    event_emitter = get_event_emitter(
        {
            "user_id": user.id,
            "chat_id": id,
            "message_id": message_id,
        },
        False,
    )

    if event_emitter:
        await event_emitter(
            {
                "type": "chat:message",
                "data": {
                    "chat_id": id,
                    "message_id": message_id,
                    "content": form_data.content,
                },
            }
        )

    return _chat_response(chat)


############################
# SendChatMessageEventById
############################
class EventForm(BaseModel):
    type: str
    data: dict


@router.post("/{id}/messages/{message_id}/event", response_model=Optional[bool])
async def send_chat_message_event_by_id(
    id: str, message_id: str, form_data: EventForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id(id)

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if chat.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    event_emitter = get_event_emitter(
        {
            "user_id": user.id,
            "chat_id": id,
            "message_id": message_id,
        }
    )

    try:
        if event_emitter:
            await event_emitter(form_data.model_dump())
        else:
            return False
        return True
    except:
        return False


############################
# DeleteChatById
############################


@router.delete("/{id}", response_model=bool)
async def delete_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        chat = Chats.get_chat_by_id(id)
        if chat:
            for tag in chat.meta.get("tags", []):
                if Chats.count_chats_by_tag_name_and_user_id(tag, user.id) == 1:
                    Tags.delete_tag_by_name_and_user_id(tag, user.id)

        result = Chats.delete_chat_by_id(id)

        return result
    else:
        if not has_permission(
            user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

        chat = Chats.get_chat_by_id(id)
        if chat:
            for tag in chat.meta.get("tags", []):
                if Chats.count_chats_by_tag_name_and_user_id(tag, user.id) == 1:
                    Tags.delete_tag_by_name_and_user_id(tag, user.id)

        result = Chats.delete_chat_by_id_and_user_id(id, user.id)
        return result


############################
# GetPinnedStatusById
############################


@router.get("/{id}/pinned", response_model=Optional[bool])
async def get_pinned_status_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        return chat.pinned
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# PinChatById
############################


@router.post("/{id}/pin", response_model=Optional[ChatResponse])
async def pin_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.toggle_chat_pinned_by_id(id)
        return _chat_response(chat)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# BranchChat
############################


class BranchForm(BaseModel):
    branch_point_message_id: str
    title: Optional[str] = None


@router.post("/{id}/branch", response_model=Optional[ChatResponse])
async def branch_chat_by_id(
    form_data: BranchForm, id: str, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    branch_point_message_id = form_data.branch_point_message_id.strip()
    if not branch_point_message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Branch point message is required.",
        )

    branch_title = (
        form_data.title.strip()
        if isinstance(form_data.title, str) and form_data.title.strip()
        else f"{chat.title} · 分支"
    )

    try:
        branched_chat = _build_branch_chat_payload(
            chat.chat,
            chat.id,
            branch_point_message_id,
            branch_title,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )

    new_chat = Chats.import_chat(
        user.id,
        ChatImportForm(
            chat=branched_chat,
            meta=deepcopy(chat.meta),
            pinned=False,
            folder_id=chat.folder_id,
            assistant_id=chat.assistant_id,
        ),
    )

    return _chat_response(new_chat)


############################
# CloneChat
############################


class CloneForm(BaseModel):
    title: Optional[str] = None


@router.post("/{id}/clone", response_model=Optional[ChatResponse])
async def clone_chat_by_id(
    form_data: CloneForm, id: str, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        updated_chat = {
            **chat.chat,
            "originalChatId": chat.id,
            "branchPointMessageId": chat.chat["history"]["currentId"],
            "title": form_data.title if form_data.title else f"Clone of {chat.title}",
        }

        chat = Chats.insert_new_chat(
            user.id,
            ChatForm(**{"chat": updated_chat, "assistant_id": chat.assistant_id}),
        )
        return _chat_response(chat)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# CloneSharedChatById
############################


@router.post("/{id}/clone/shared", response_model=Optional[ChatResponse])
async def clone_shared_chat_by_id(id: str, user=Depends(get_verified_user)):

    if user.role == "admin":
        chat = Chats.get_chat_by_id(id)
    else:
        chat = Chats.get_chat_by_share_id(id)

    if chat:
        updated_chat = {
            **chat.chat,
            "originalChatId": chat.id,
            "branchPointMessageId": chat.chat["history"]["currentId"],
            "title": f"Clone of {chat.title}",
        }

        chat = Chats.insert_new_chat(user.id, ChatForm(**{"chat": updated_chat}))
        return _chat_response(chat)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ArchiveChat
############################


@router.post("/{id}/archive", response_model=Optional[ChatResponse])
async def archive_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.toggle_chat_archive_by_id(id)

        # Delete tags if chat is archived
        if chat.archived:
            for tag_id in chat.meta.get("tags", []):
                if Chats.count_chats_by_tag_name_and_user_id(tag_id, user.id) == 0:
                    log.debug(f"deleting tag: {tag_id}")
                    Tags.delete_tag_by_name_and_user_id(tag_id, user.id)
        else:
            for tag_id in chat.meta.get("tags", []):
                tag = Tags.get_tag_by_name_and_user_id(tag_id, user.id)
                if tag is None:
                    log.debug(f"inserting tag: {tag_id}")
                    tag = Tags.insert_new_tag(tag_id, user.id)

        return _chat_response(chat)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ShareChatById
############################


@router.post("/{id}/share", response_model=Optional[ChatResponse])
async def share_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        if chat.share_id:
            shared_chat = Chats.update_shared_chat_by_chat_id(chat.id)
            return _chat_response(shared_chat)

        shared_chat = Chats.insert_shared_chat_by_chat_id(chat.id)
        if not shared_chat:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_MESSAGES.DEFAULT(),
            )
        return _chat_response(shared_chat)

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# DeletedSharedChatById
############################


@router.delete("/{id}/share", response_model=Optional[bool])
async def delete_shared_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        if not chat.share_id:
            return False

        result = Chats.delete_shared_chat_by_chat_id(id)
        update_result = Chats.update_chat_share_id_by_id(id, None)

        return result and update_result != None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# UpdateChatFolderIdById
############################


class ChatFolderIdForm(BaseModel):
    folder_id: Optional[str] = None


@router.post("/{id}/folder", response_model=Optional[ChatResponse])
async def update_chat_folder_id_by_id(
    id: str, form_data: ChatFolderIdForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        if form_data.folder_id and FOLDER_MAX_ITEM_COUNT > 0:
            existing = Chats.get_chats_by_folder_id_and_user_id(
                form_data.folder_id, user.id
            )
            if len(existing) >= FOLDER_MAX_ITEM_COUNT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(
                        f"Folder item limit reached ({FOLDER_MAX_ITEM_COUNT})"
                    ),
                )

        chat = Chats.update_chat_folder_id_by_id_and_user_id(
            id, user.id, form_data.folder_id
        )
        return _chat_response(chat)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetChatTagsById
############################


@router.get("/{id}/tags", response_model=list[TagModel])
async def get_chat_tags_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        tags = chat.meta.get("tags", [])
        return Tags.get_tags_by_ids_and_user_id(tags, user.id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# AddChatTagById
############################


@router.post("/{id}/tags", response_model=list[TagModel])
async def add_tag_by_id_and_tag_name(
    id: str, form_data: TagForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        tags = chat.meta.get("tags", [])
        tag_id = form_data.name.replace(" ", "_").lower()

        if tag_id == "none":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Tag name cannot be 'None'"),
            )

        if tag_id not in tags:
            Chats.add_chat_tag_by_id_and_user_id_and_tag_name(
                id, user.id, form_data.name
            )

        chat = Chats.get_chat_by_id_and_user_id(id, user.id)
        tags = chat.meta.get("tags", [])
        return Tags.get_tags_by_ids_and_user_id(tags, user.id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# DeleteChatTagById
############################


@router.delete("/{id}/tags", response_model=list[TagModel])
async def delete_tag_by_id_and_tag_name(
    id: str, form_data: TagForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        Chats.delete_tag_by_id_and_user_id_and_tag_name(id, user.id, form_data.name)

        if Chats.count_chats_by_tag_name_and_user_id(form_data.name, user.id) == 0:
            Tags.delete_tag_by_name_and_user_id(form_data.name, user.id)

        chat = Chats.get_chat_by_id_and_user_id(id, user.id)
        tags = chat.meta.get("tags", [])
        return Tags.get_tags_by_ids_and_user_id(tags, user.id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# DeleteAllTagsById
############################


@router.delete("/{id}/tags/all", response_model=Optional[bool])
async def delete_all_tags_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        Chats.delete_all_tags_by_id_and_user_id(id, user.id)

        for tag in chat.meta.get("tags", []):
            if Chats.count_chats_by_tag_name_and_user_id(tag, user.id) == 0:
                Tags.delete_tag_by_name_and_user_id(tag, user.id)

        return True
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# Chat Message Reactions
############################

# [REACTION_FEATURE] Commented out - reaction feature disabled for now
# class ReactionForm(BaseModel):
#     name: str
#
#
# @router.get("/{chat_id}/messages/{message_id}/reactions", response_model=list[ChatReactionSummary])
# async def get_chat_message_reactions(
#     chat_id: str, message_id: str, user=Depends(get_verified_user)
# ):
#     return ChatReactions.get_reactions_by_chat_and_message(chat_id, message_id)
#
#
# @router.post("/{chat_id}/messages/{message_id}/reactions/add")
# async def add_chat_message_reaction(
#     chat_id: str, message_id: str, form_data: ReactionForm, user=Depends(get_verified_user)
# ):
#     reaction = ChatReactions.add_reaction(chat_id, message_id, user.id, form_data.name)
#     if reaction:
#         return ChatReactions.get_reactions_by_chat_and_message(chat_id, message_id)
#     raise HTTPException(status_code=400, detail="Failed to add reaction")
#
#
# @router.post("/{chat_id}/messages/{message_id}/reactions/remove")
# async def remove_chat_message_reaction(
#     chat_id: str, message_id: str, form_data: ReactionForm, user=Depends(get_verified_user)
# ):
#     ChatReactions.remove_reaction(chat_id, message_id, user.id, form_data.name)
#     return ChatReactions.get_reactions_by_chat_and_message(chat_id, message_id)
