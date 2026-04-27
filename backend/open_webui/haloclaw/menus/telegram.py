"""Telegram interactive menu handlers for HaloClaw.

Provides inline keyboard menus for model selection, thinking intensity,
tool toggle, settings display, history clearing, and help.
All UI text is in Chinese.
"""

import logging
import math
from typing import Optional

from open_webui.haloclaw.config import HALOCLAW_DEFAULT_MODEL
from open_webui.haloclaw.models import (
    ExternalUsers,
    MessageLogs,
    ExternalUserModel,
    GatewayModel,
    Gateways,
)
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

# Pagination: models per page within a group
MODELS_PER_PAGE = 8
# Groups per page on the group list
GROUPS_PER_PAGE = 8

# Thinking effort labels (Chinese)
EFFORT_LABELS = {
    "off": "关闭",
    "low": "低",
    "medium": "中",
    "high": "高",
}

EFFORT_MAP = {"off": None, "low": "low", "med": "medium", "high": "high"}


def _group_models(models: list[dict]) -> list[dict]:
    """Group models by connection name (same as the admin model list page).

    Each group: {"name": "OpenRouter", "models": [...], "index": 0}
    Groups sorted by model count (descending).
    """
    groups_map: dict[str, list[dict]] = {}
    for m in models:
        group = m.get("connection_name") or m.get("owned_by") or "Unknown"
        if group not in groups_map:
            groups_map[group] = []
        groups_map[group].append(m)

    groups = sorted(groups_map.items(), key=lambda x: -len(x[1]))
    return [
        {"name": name, "models": mods, "index": i}
        for i, (name, mods) in enumerate(groups)
    ]


def _resolve_ext_user(gateway: GatewayModel, user) -> ExternalUserModel:
    """Resolve the ExternalUser from a Telegram user object."""
    return ExternalUsers.get_or_create(
        gateway_id=gateway.id,
        platform="telegram",
        platform_user_id=str(user.id),
        platform_username=user.username,
        platform_display_name=user.full_name,
    )


async def _get_available_models(app, gateway: GatewayModel) -> list[dict]:
    """Load models using the same approach as the dispatcher (FakeRequest + get_all_models)."""
    from open_webui.haloclaw.dispatcher import _FakeRequest
    from open_webui.utils.models import get_all_models
    from open_webui.utils.model_identity import get_model_aliases, get_model_selection_id
    from open_webui.models.users import Users

    gateway_owner = Users.get_user_by_id(gateway.user_id)
    if not gateway_owner:
        return []

    try:
        from open_webui.utils.user_connections import maybe_migrate_user_connections

        fake_request = _FakeRequest(app)
        gateway_owner = maybe_migrate_user_connections(fake_request, gateway_owner)
        fake_request.state.connection_user = gateway_owner
        available_models = await get_all_models(fake_request, user=gateway_owner)
        if not available_models:
            return []

        result = []
        seen = set()
        for model in available_models:
            if not isinstance(model, dict):
                continue
            model_id = get_model_selection_id(model) or model.get("id")
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            name = model.get("name", model_id)
            result.append({
                "id": model_id,
                "name": name,
                "aliases": get_model_aliases(model),
                "connection_name": model.get("connection_name"),
                "owned_by": model.get("owned_by"),
            })
        result.sort(key=lambda m: m["name"])
        return result
    except Exception as e:
        log.warning(f"HaloClaw menus: failed to load models: {e}")
        return []


def _resolve_effective_model(gateway: GatewayModel, ext_user: ExternalUserModel) -> tuple[str, str]:
    if ext_user.model_override:
        return ext_user.model_override, "Telegram 覆盖"
    if gateway.default_model_id:
        return gateway.default_model_id, "网关默认"
    if HALOCLAW_DEFAULT_MODEL.value:
        return HALOCLAW_DEFAULT_MODEL.value, "全局默认"
    return "未设置", "未设置"


def _model_matches_id(model: dict, model_id: str) -> bool:
    if not model_id:
        return False
    return model.get("id") == model_id or model_id in (model.get("aliases") or [])


def _resolve_model_display_id(models: list[dict], model_id: str) -> str:
    for model in models:
        if _model_matches_id(model, model_id):
            return model.get("id") or model_id
    return model_id


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------


async def handle_start(update, context, gateway: GatewayModel) -> None:
    """Enhanced /start with settings summary and quick buttons."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    user = update.message.from_user
    ext_user = _resolve_ext_user(gateway, user)
    model_id, model_source = _resolve_effective_model(gateway, ext_user)
    model_display = _truncate_model_name(model_id)

    text = (
        f"你好！我是由 HaloClaw 驱动的 AI 助手。\n\n"
        f"当前模型: {model_display}（{model_source}）\n"
        f"发送任意消息开始对话，或使用下方菜单调整设置。"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("切换模型", callback_data="cmd:model"),
            InlineKeyboardButton("设置", callback_data="cmd:settings"),
        ],
        [InlineKeyboardButton("帮助", callback_data="cmd:help")],
    ])

    await update.message.reply_text(text, reply_markup=keyboard)


async def handle_model(update, context, gateway: GatewayModel, app) -> None:
    """Show model group selection (first level)."""
    user = update.message.from_user
    ext_user = _resolve_ext_user(gateway, user)
    keyboard, text = await _build_group_keyboard(gateway, ext_user, app, page=0)
    await update.message.reply_text(text, reply_markup=keyboard)


async def handle_think(update, context, gateway: GatewayModel) -> None:
    """Show thinking intensity selection."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    user = update.message.from_user
    ext_user = _resolve_ext_user(gateway, user)

    meta = ext_user.meta or {}
    thinking = meta.get("thinking", {})
    current = "off"
    if isinstance(thinking, dict) and thinking.get("enabled"):
        current = thinking.get("effort", "medium")

    text = f"🧠 思考强度\n当前: {EFFORT_LABELS.get(current, current)}\n\n选择思考强度："

    buttons = []
    for key, label in EFFORT_LABELS.items():
        marker = " ✓" if (key == current or (key == "off" and current == "off")) else ""
        cb_key = key if key == "off" else {"low": "low", "medium": "med", "high": "high"}.get(key, key)
        buttons.append(InlineKeyboardButton(f"{label}{marker}", callback_data=f"tk:{cb_key}"))

    keyboard = InlineKeyboardMarkup([buttons])
    await update.message.reply_text(text, reply_markup=keyboard)


async def handle_tools(update, context, gateway: GatewayModel) -> None:
    """Show tools toggle."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    user = update.message.from_user
    ext_user = _resolve_ext_user(gateway, user)

    meta = ext_user.meta or {}
    enabled = meta.get("tools_enabled", True)

    gateway_meta = gateway.meta or {}
    has_tools = bool(gateway_meta.get("tool_ids"))

    if not has_tools:
        await update.message.reply_text("⚠️ 此网关未配置任何工具。请联系管理员开启。")
        return

    status = "已开启" if enabled else "已关闭"
    text = f"🔧 工具开关\n当前状态: {status}"

    if enabled:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("关闭工具", callback_data="tl:0")]
        ])
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("开启工具", callback_data="tl:1")]
        ])

    await update.message.reply_text(text, reply_markup=keyboard)


async def handle_settings(update, context, gateway: GatewayModel, app) -> None:
    """Display current settings summary."""
    user = update.message.from_user
    ext_user = _resolve_ext_user(gateway, user)

    text = _build_settings_text(gateway, ext_user, str(update.message.chat_id))
    await update.message.reply_text(text)


async def handle_clear(update, context, gateway: GatewayModel) -> None:
    """Show clear history confirmation."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("确认清除", callback_data="clr:y"),
            InlineKeyboardButton("取消", callback_data="clr:n"),
        ]
    ])

    await update.message.reply_text(
        "⚠️ 确定要清除所有对话历史吗？此操作不可撤销。",
        reply_markup=keyboard,
    )


async def handle_help(update, context, gateway: GatewayModel) -> None:
    """Show help text."""
    await update.message.reply_text(_HELP_TEXT)


_HELP_TEXT = (
    "📖 帮助\n\n"
    "可用命令：\n"
    "/model - 切换 AI 模型\n"
    "/think - 调节思考强度\n"
    "/tools - 开关工具（联网搜索等）\n"
    "/settings - 查看当前设置\n"
    "/clear - 清除对话历史\n"
    "/help - 显示此帮助\n\n"
    "直接发送文字消息即可开始对话。"
)


# ---------------------------------------------------------------------------
# Callback Query Handler
# ---------------------------------------------------------------------------


async def handle_callback(update, context, gateway_id: str, app) -> None:
    """Route callback queries to appropriate handlers."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    gateway = Gateways.get_by_id(gateway_id)
    if not gateway:
        return

    user = query.from_user
    ext_user = _resolve_ext_user(gateway, user)
    data = query.data

    if data.startswith("mg:"):
        await _handle_model_group_callback(query, gateway, ext_user, app, data)
    elif data.startswith("tk:"):
        await _handle_think_callback(query, ext_user, data)
    elif data.startswith("tl:"):
        await _handle_tools_callback(query, ext_user, data)
    elif data.startswith("clr:"):
        await _handle_clear_callback(query, gateway, data)
    elif data.startswith("cmd:"):
        await _handle_cmd_callback(query, gateway, ext_user, app, data)


async def _handle_model_group_callback(query, gateway, ext_user, app, data: str) -> None:
    """Handle two-level model selection callbacks.

    Callback data format:
      mg:default               - clear per-user override and follow defaults
      mg:gl:p:<page>           - group list pagination
      mg:<gidx>                - enter a group (show models page 0)
      mg:<gidx>:p:<page>       - model list pagination within group
      mg:<gidx>:s:<midx>       - select a model within group
      mg:back                  - back to group list
    """
    parts = data[3:]  # strip "mg:"

    if parts == "back":
        # Back to group list page 0
        keyboard, text = await _build_group_keyboard(gateway, ext_user, app, page=0)
        await _safe_edit(query.message, text, keyboard)
        return

    if parts == "default":
        ExternalUsers.update_model_override(ext_user.id, None)
        ext_user.model_override = None
        model_id, model_source = _resolve_effective_model(gateway, ext_user)
        model_display = _truncate_model_name(model_id)
        await _safe_edit(
            query.message,
            f"✅ 已恢复为跟随默认模型: {model_display}（{model_source}）",
        )
        return

    if parts.startswith("gl:p:"):
        # Group list pagination
        try:
            page = int(parts[5:])
        except ValueError:
            return
        keyboard, text = await _build_group_keyboard(gateway, ext_user, app, page=page)
        await _safe_edit(query.message, text, keyboard)
        return

    # Parse group index and possible sub-action
    # mg:<gidx>  or  mg:<gidx>:p:<page>  or  mg:<gidx>:s:<midx>
    tokens = parts.split(":")
    try:
        gidx = int(tokens[0])
    except ValueError:
        return

    models = await _get_available_models(app, gateway)
    groups = _group_models(models)
    if gidx < 0 or gidx >= len(groups):
        await _safe_edit(query.message, "⚠️ 无效的分组")
        return

    group = groups[gidx]

    if len(tokens) == 1:
        # Enter group → show models page 0
        keyboard, text = _build_group_models_keyboard(group, gidx, ext_user, gateway, page=0)
        await _safe_edit(query.message, text, keyboard)

    elif len(tokens) >= 3 and tokens[1] == "p":
        # Pagination within group
        try:
            page = int(tokens[2])
        except ValueError:
            return
        keyboard, text = _build_group_models_keyboard(group, gidx, ext_user, gateway, page=page)
        await _safe_edit(query.message, text, keyboard)

    elif len(tokens) >= 3 and tokens[1] == "s":
        # Select model within group
        try:
            midx = int(tokens[2])
        except ValueError:
            return
        group_models = group["models"]
        if midx < 0 or midx >= len(group_models):
            await _safe_edit(query.message, "⚠️ 无效的模型选择")
            return

        selected = group_models[midx]
        ExternalUsers.update_model_override(ext_user.id, selected["id"])
        ext_user.model_override = selected["id"]
        display_name = _truncate_model_name(selected["name"])
        await _safe_edit(query.message, f"✅ 已切换到: {display_name}（Telegram 覆盖）")


async def _handle_think_callback(query, ext_user, data: str) -> None:
    """Handle thinking intensity callback."""
    effort_key = data[3:]  # off, low, med, high

    if effort_key not in EFFORT_MAP:
        return

    meta = dict(ext_user.meta or {})
    effort_value = EFFORT_MAP[effort_key]

    if effort_value is None:
        meta["thinking"] = {"enabled": False, "effort": "medium"}
    else:
        meta["thinking"] = {"enabled": True, "effort": effort_value}

    ExternalUsers.update_meta(ext_user.id, meta)

    label = EFFORT_LABELS.get(effort_key, effort_key)
    await _safe_edit(query.message, f"✅ 思考强度已设为: {label}")


async def _handle_tools_callback(query, ext_user, data: str) -> None:
    """Handle tools toggle callback."""
    enabled = data[3:] == "1"

    meta = dict(ext_user.meta or {})
    meta["tools_enabled"] = enabled

    ExternalUsers.update_meta(ext_user.id, meta)

    status = "已开启" if enabled else "已关闭"
    await _safe_edit(query.message, f"✅ 工具{status}")


async def _handle_clear_callback(query, gateway, data: str) -> None:
    """Handle clear history confirmation callback."""
    if data == "clr:y":
        chat_id = str(query.message.chat_id)
        MessageLogs.delete_by_chat(gateway.id, chat_id)
        await _safe_edit(query.message, "✅ 对话历史已清除")
    else:
        await _safe_edit(query.message, "已取消")


async def _handle_cmd_callback(query, gateway, ext_user, app, data: str) -> None:
    """Handle quick-action command callbacks from /start."""
    cmd = data[4:]  # model, settings, help
    if cmd == "model":
        keyboard, text = await _build_group_keyboard(gateway, ext_user, app, page=0)
        await _safe_edit(query.message, text, keyboard)
    elif cmd == "settings":
        text = _build_settings_text(gateway, ext_user, str(query.message.chat_id))
        await _safe_edit(query.message, text)
    elif cmd == "help":
        await _safe_edit(query.message, _HELP_TEXT)


# ---------------------------------------------------------------------------
# Keyboard Builders
# ---------------------------------------------------------------------------


async def _build_group_keyboard(gateway, ext_user, app, page: int):
    """Build group list keyboard (first level of model selection)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    models = await _get_available_models(app, gateway)
    if not models:
        return None, "⚠️ 没有可用的模型"

    groups = _group_models(models)
    total_pages = math.ceil(len(groups) / GROUPS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * GROUPS_PER_PAGE
    end = start + GROUPS_PER_PAGE
    page_groups = groups[start:end]

    current_model, current_source = _resolve_effective_model(gateway, ext_user)
    if current_model == "未设置":
        current_model = ""
    current_display_model = _resolve_model_display_id(models, current_model)
    # Find which group the current model belongs to
    current_group = ""
    if current_model:
        for m in models:
            if _model_matches_id(m, current_model):
                current_group = m.get("connection_name") or m.get("owned_by") or ""
                break

    rows = []
    if ext_user.model_override:
        rows.append([
            InlineKeyboardButton("↺ 跟随默认模型", callback_data="mg:default")
        ])
    else:
        rows.append([
            InlineKeyboardButton("✓ 当前跟随默认模型", callback_data="mg:default")
        ])

    for i in range(0, len(page_groups), 2):
        row = []
        for j in range(i, min(i + 2, len(page_groups))):
            g = page_groups[j]
            label = f"{g['name']} ({len(g['models'])})"
            if g["name"] == current_group:
                label = f"✓ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"mg:{g['index']}"))
        rows.append(row)

    # Pagination
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"mg:gl:p:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="mg:gl:p:0"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"mg:gl:p:{page + 1}"))
        rows.append(nav)

    keyboard = InlineKeyboardMarkup(rows)
    current_display = _truncate_model_name(current_display_model or "未设置")
    text = (
        f"🤖 选择模型分组（共 {len(models)} 个模型）\n"
        f"当前: {current_display}（{current_source}）"
    )
    return keyboard, text


def _build_group_models_keyboard(group: dict, gidx: int, ext_user, gateway, page: int):
    """Build model list keyboard within a group (second level)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    models = group["models"]
    total_pages = math.ceil(len(models) / MODELS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * MODELS_PER_PAGE
    end = start + MODELS_PER_PAGE
    page_models = models[start:end]

    current_model, current_source = _resolve_effective_model(gateway, ext_user)
    if current_model == "未设置":
        current_model = ""
    current_display_model = _resolve_model_display_id(models, current_model)

    rows = []
    if ext_user.model_override:
        rows.append([
            InlineKeyboardButton("↺ 跟随默认模型", callback_data="mg:default")
        ])
    else:
        rows.append([
            InlineKeyboardButton("✓ 当前跟随默认模型", callback_data="mg:default")
        ])

    for i in range(0, len(page_models), 2):
        row = []
        for j in range(i, min(i + 2, len(page_models))):
            m = page_models[j]
            midx = start + j
            name = _truncate_model_name(m["name"], max_len=25)
            if _model_matches_id(m, current_model):
                name = f"✓ {name}"
            row.append(InlineKeyboardButton(name, callback_data=f"mg:{gidx}:s:{midx}"))
        rows.append(row)

    # Pagination within group
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀", callback_data=f"mg:{gidx}:p:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"mg:{gidx}:p:{page}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶", callback_data=f"mg:{gidx}:p:{page + 1}"))
        rows.append(nav)

    # Back button
    rows.append([InlineKeyboardButton("↩ 返回分组列表", callback_data="mg:back")])

    keyboard = InlineKeyboardMarkup(rows)
    current_display = _truncate_model_name(current_display_model or "未设置")
    text = (
        f"🤖 {group['name']} ({len(models)} 个模型)\n"
        f"当前: {current_display}（{current_source}）"
    )
    return keyboard, text


# ---------------------------------------------------------------------------
# Shared Helpers
# ---------------------------------------------------------------------------


def _build_settings_text(gateway, ext_user, chat_id: str) -> str:
    """Build settings summary text."""
    meta = ext_user.meta or {}
    model_id, model_source = _resolve_effective_model(gateway, ext_user)
    model_display = _truncate_model_name(model_id)

    thinking = meta.get("thinking", {})
    if isinstance(thinking, dict) and thinking.get("enabled"):
        think_label = EFFORT_LABELS.get(thinking.get("effort", "medium"), "中")
    else:
        think_label = "关闭"

    tools_enabled = meta.get("tools_enabled", True)
    gateway_meta = gateway.meta or {}
    if not gateway_meta.get("tool_ids"):
        tools_label = "未配置"
    else:
        tools_label = "已开启" if tools_enabled else "已关闭"

    history = MessageLogs.get_history(
        gateway_id=gateway.id,
        platform_chat_id=chat_id,
        limit=9999,
    )

    return (
        f"⚙️ 当前设置\n\n"
        f"📎 模型: {model_display}（{model_source}）\n"
        f"🧠 思考: {think_label}\n"
        f"🔧 工具: {tools_label}\n"
        f"💬 历史: {len(history)} 条消息"
    )


async def _safe_edit(message, text: str, reply_markup=None) -> None:
    """Edit message text, silently ignoring errors (e.g., same content)."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        pass


def _truncate_model_name(name: str, max_len: int = 40) -> str:
    """Truncate long model names for display."""
    if len(name) <= max_len:
        return name
    return name[:max_len - 1] + "…"
