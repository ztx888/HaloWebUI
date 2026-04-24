import os
import re
import site
import tempfile
from importlib import import_module, reload
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from html import unescape
from pathlib import Path
from threading import Lock
from typing import Any, Optional

import markdown
from bs4 import BeautifulSoup, NavigableString, Tag
from fpdf import FPDF
from PIL import Image

from open_webui.env import STATIC_DIR, FONTS_DIR
from open_webui.models.chats import ChatTitleMessagesForm
from open_webui.utils.chat_image_refs import resolve_chat_image_url_to_bytes

_FONT_CACHE_DIR = Path(tempfile.gettempdir()) / "halo_pdf_font_cache"
_FONT_FILE_CACHE: dict[str, Path] = {}
_FONT_CACHE_LOCK = Lock()


class PDFGenerator:
    PAGE_MARGIN = 15
    SECTION_GAP = 5
    MESSAGE_GAP = 8
    LINE_HEIGHT = 6
    SMALL_LINE_HEIGHT = 5
    CODE_LINE_HEIGHT = 5
    LIST_INDENT = 7
    BLOCK_INDENT = 5
    MAX_IMAGE_HEIGHT = 160
    MARKDOWN_EXTENSIONS = [
        "extra",
        "tables",
        "fenced_code",
        "sane_lists",
        "nl2br",
        "pymdownx.superfences",
    ]

    def __init__(
        self,
        form_data: ChatTitleMessagesForm,
        *,
        user_id: Optional[str] = None,
        is_admin: bool = False,
    ):
        self.form_data = form_data
        self.user_id = user_id
        self.is_admin = is_admin
        self.font_family = "Helvetica"
        self.temp_image_paths: list[str] = []

    def format_timestamp(self, timestamp: float) -> str:
        try:
            date_time = datetime.fromtimestamp(timestamp)
            return date_time.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError, OSError):
            return ""

    def generate_chat_pdf(self) -> bytes:
        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=self.PAGE_MARGIN)
        pdf.set_margins(self.PAGE_MARGIN, self.PAGE_MARGIN, self.PAGE_MARGIN)
        pdf.add_page()

        try:
            self._configure_fonts(pdf)
            pdf.set_title(self.form_data.title or "chat")
            pdf.set_author("Halo WebUI")
            self._render_document(pdf)
            pdf_bytes = pdf.output()
            return bytes(pdf_bytes)
        finally:
            self._cleanup_temp_images()

    def _render_document(self, pdf: FPDF) -> None:
        title = (self.form_data.title or "聊天导出").strip() or "聊天导出"

        pdf.set_font(self.font_family, "B", 20)
        self._write_full_width_block(pdf, 10, title)
        pdf.ln(2)

        pdf.set_font(self.font_family, "", 10)
        pdf.set_text_color(107, 114, 128)
        self._write_full_width_block(
            pdf,
            self.SMALL_LINE_HEIGHT,
            f"由 Halo WebUI 导出 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )
        pdf.set_text_color(17, 24, 39)
        pdf.ln(2)

        messages = self.form_data.messages if isinstance(self.form_data.messages, list) else []
        for index, message in enumerate(messages):
            self._render_message(pdf, message, is_first=index == 0)

    def _render_message(self, pdf: FPDF, message: dict[str, Any], *, is_first: bool) -> None:
        if not is_first:
            pdf.ln(self.MESSAGE_GAP)

        role = self._normalize_role(message.get("role"))
        model = self._format_model_label(message)
        timestamp = self.format_timestamp(message.get("timestamp")) if message.get("timestamp") else ""
        stats_line = self._build_stats_line(message)
        instruction = self._safe_text(message.get("instruction"))
        content = self._stringify_content(message.get("content"))
        files = message.get("files") if isinstance(message.get("files"), list) else []
        code_executions = (
            message.get("code_executions")
            if isinstance(message.get("code_executions"), list)
            else []
        )

        if pdf.will_page_break(28):
            pdf.add_page()

        self._draw_separator(pdf)
        pdf.set_text_color(17, 24, 39)
        pdf.set_font(self.font_family, "B", 13)
        header = role if not model else f"{role} · {model}"
        self._write_full_width_block(pdf, 7, header)

        meta_parts = [part for part in [timestamp, stats_line] if part]
        if meta_parts:
            pdf.set_font(self.font_family, "", 9)
            pdf.set_text_color(107, 114, 128)
            self._write_full_width_block(
                pdf,
                self.SMALL_LINE_HEIGHT,
                " | ".join(meta_parts),
            )

        if instruction:
            pdf.ln(1)
            pdf.set_font(self.font_family, "", 10)
            pdf.set_text_color(107, 114, 128)
            self._write_full_width_block(
                pdf,
                self.SMALL_LINE_HEIGHT,
                f"指令：{instruction}",
            )

        pdf.set_text_color(17, 24, 39)

        image_files = [file for file in files if file.get("type") == "image"]
        if image_files:
            pdf.ln(2)
            for image_file in image_files:
                self._render_image_from_url(
                    pdf,
                    image_file.get("url"),
                    alt_text=image_file.get("name") or "图片",
                )

        if content:
            pdf.ln(2)
            self._render_markdown_content(pdf, content)

        if code_executions:
            pdf.ln(2)
            self._render_code_executions(pdf, code_executions)

    def _render_markdown_content(self, pdf: FPDF, content: str) -> None:
        self._render_markdown_fragment(pdf, content, list_level=0)

    def _render_markdown_fragment(self, pdf: FPDF, content: str, *, list_level: int) -> None:
        content = self._normalize_markdown_for_pdf(content)
        html = markdown.markdown(
            content,
            extensions=self.MARKDOWN_EXTENSIONS,
            output_format="html5",
        )
        soup = BeautifulSoup(html, "html.parser")
        self._render_children(pdf, list(soup.children), list_level=list_level)

    def _normalize_markdown_for_pdf(self, content: str) -> str:
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        normalized_lines: list[str] = []
        ordered_base_indent: int | None = None
        ordered_child_indent: int | None = None

        for line in lines:
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)

            ordered_match = re.match(r"^(\s*)\d+[.)]\s+", line)
            unordered_match = re.match(r"^(\s*)[-+*]\s+", line)

            if ordered_match:
                ordered_base_indent = len(ordered_match.group(1))
                ordered_child_indent = ordered_base_indent + 4
                normalized_lines.append(line)
                continue

            if (
                ordered_base_indent is not None
                and ordered_child_indent is not None
                and unordered_match
                and indent > ordered_base_indent
                and indent < ordered_child_indent
            ):
                normalized_lines.append(" " * ordered_child_indent + stripped)
                continue

            if stripped and ordered_base_indent is not None and indent <= ordered_base_indent:
                ordered_base_indent = None
                ordered_child_indent = None

            normalized_lines.append(line)

        return "\n".join(normalized_lines)

    def _render_children(self, pdf: FPDF, children: list[Any], *, list_level: int) -> None:
        for child in children:
            if isinstance(child, NavigableString):
                text = self._normalize_text(str(child))
                if text:
                    self._write_paragraph(pdf, text)
                continue

            if isinstance(child, Tag):
                self._render_tag(pdf, child, list_level=list_level)

    def _render_tag(self, pdf: FPDF, tag: Tag, *, list_level: int) -> None:
        name = tag.name.lower()

        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._render_heading(pdf, tag, level=int(name[1]))
            return

        if name == "p":
            self._render_paragraph_tag(pdf, tag)
            return

        if name in {"ul", "ol"}:
            self._render_list(pdf, tag, level=list_level)
            return

        if name == "pre":
            self._render_code_block(
                pdf,
                self._normalize_code_text(tag.get_text("\n", strip=False)),
                language=self._extract_code_language(tag),
            )
            return

        if name == "blockquote":
            self._render_blockquote(pdf, tag, list_level=list_level)
            return

        if name == "table":
            self._render_table(pdf, tag)
            return

        if name == "img":
            self._render_image_from_url(pdf, tag.get("src"), alt_text=tag.get("alt"))
            return

        if name == "hr":
            self._draw_separator(pdf)
            return

        if name == "details":
            self._render_details(pdf, tag, list_level=list_level)
            return

        if name in {"div", "section", "article"}:
            self._render_children(pdf, list(tag.children), list_level=list_level)
            return

        text = self._inline_text(tag)
        if text:
            self._write_paragraph(pdf, text)

    def _render_heading(self, pdf: FPDF, tag: Tag, *, level: int) -> None:
        size_map = {1: 18, 2: 16, 3: 14, 4: 13, 5: 12, 6: 11}
        line_map = {1: 9, 2: 8, 3: 7, 4: 6.5, 5: 6, 6: 6}
        text = self._inline_text(tag)
        if not text:
            return

        pdf.ln(2)
        pdf.set_font(self.font_family, "B", size_map.get(level, 12))
        pdf.set_text_color(17, 24, 39)
        self._write_full_width_block(pdf, line_map.get(level, 6), text)
        pdf.ln(1)

    def _render_paragraph_tag(self, pdf: FPDF, tag: Tag) -> None:
        images = [child for child in tag.children if isinstance(child, Tag) and child.name == "img"]
        inline_children = [
            child
            for child in tag.children
            if not (isinstance(child, Tag) and child.name and child.name.lower() == "img")
        ]

        if self._has_visible_inline_content(inline_children):
            self._write_inline_block(
                pdf,
                inline_children,
                line_height=self.LINE_HEIGHT,
                font_size=11,
            )
            self._finish_inline_block(pdf, self.LINE_HEIGHT, extra_gap=1)

        for image in images:
            self._render_image_from_url(pdf, image.get("src"), alt_text=image.get("alt"))

    def _render_list(self, pdf: FPDF, tag: Tag, *, level: int) -> None:
        items = [child for child in tag.children if isinstance(child, Tag) and child.name == "li"]
        if not items:
            return

        ordered = tag.name.lower() == "ol"
        start = self._safe_int(tag.get("start"), fallback=1) if ordered else 1
        markers = [
            f"{start + index}." if ordered else "•"
            for index in range(len(items))
        ]
        marker_width = max(pdf.get_string_width(marker) for marker in markers) + 3

        for marker, item in zip(markers, items):
            self._render_list_item(pdf, item, marker, level=level, marker_width=marker_width)

    def _render_list_item(
        self,
        pdf: FPDF,
        item: Tag,
        marker: str,
        *,
        level: int,
        marker_width: float,
    ) -> None:
        intro_nodes = self._extract_list_intro_nodes(item)
        child_blocks = [
            child
            for child in item.children
            if isinstance(child, Tag) and child.name.lower() in {"ul", "ol", "pre", "blockquote", "table", "details"}
        ]

        left_indent = self.LIST_INDENT * level
        start_x = pdf.l_margin + left_indent
        available_width = self._content_width(pdf) - left_indent
        text_width = max(10, available_width - marker_width)
        line_height = self.LINE_HEIGHT

        if self._has_visible_inline_content(intro_nodes):
            y = pdf.get_y()
            original_left_margin = pdf.l_margin
            pdf.set_font(self.font_family, "", 11)
            pdf.set_text_color(17, 24, 39)
            pdf.set_xy(start_x, y)
            pdf.multi_cell(marker_width, line_height, marker, align="R", new_x="RIGHT", new_y="TOP")
            pdf.set_left_margin(start_x + marker_width)
            pdf.set_xy(start_x + marker_width, y)
            self._render_inline_nodes(
                pdf,
                intro_nodes,
                line_height=line_height,
                font_size=11,
            )
            self._finish_inline_block(pdf, line_height, extra_gap=0)
            pdf.set_left_margin(original_left_margin)
            pdf.set_x(original_left_margin)
        else:
            pdf.set_x(start_x)
            pdf.multi_cell(available_width, line_height, marker)

        for child in child_blocks:
            if child.name.lower() in {"ul", "ol"}:
                self._render_list(pdf, child, level=level + 1)
            else:
                with self._temporary_indent(pdf, self.LIST_INDENT * (level + 1)):
                    self._render_tag(pdf, child, list_level=level + 1)

    def _render_blockquote(self, pdf: FPDF, tag: Tag, *, list_level: int) -> None:
        if not self._has_visible_inline_content(list(tag.children)):
            return

        pdf.ln(1)
        x = pdf.l_margin + self.LIST_INDENT * list_level
        y = pdf.get_y()
        with self._temporary_indent(pdf, self.BLOCK_INDENT + self.LIST_INDENT * list_level):
            pdf.set_text_color(55, 65, 81)
            self._render_children(pdf, list(tag.children), list_level=list_level)
            end_y = pdf.get_y()
            pdf.set_draw_color(209, 213, 219)
            pdf.line(x, y, x, max(y + 8, end_y))
        pdf.set_text_color(17, 24, 39)

    def _render_table(self, pdf: FPDF, tag: Tag) -> None:
        rows: list[list[str]] = []
        for tr in tag.find_all("tr"):
            cells = [
                self._normalize_text(cell.get_text(" ", strip=True))
                for cell in tr.find_all(["th", "td"])
            ]
            if cells:
                rows.append(cells)

        if not rows:
            return

        table_text = "\n".join(" | ".join(cell for cell in row if cell) for row in rows)
        self._render_code_block(pdf, table_text, language="table")

    def _render_details(self, pdf: FPDF, tag: Tag, *, list_level: int) -> None:
        summary_text = self._get_details_summary_text(tag)
        if summary_text:
            pdf.set_font(self.font_family, "B", 11)
            pdf.set_text_color(55, 65, 81)
            self._write_full_width_block(pdf, self.LINE_HEIGHT, summary_text)
            pdf.set_text_color(17, 24, 39)

        body_source = self._extract_details_body_source(tag)
        if body_source:
            if self._safe_text(tag.attrs.get("type")).lower() == "reasoning":
                body_source = re.sub(r"(?m)^\s*>\s?", "", body_source)
            pdf.ln(1)
            self._render_markdown_fragment(pdf, body_source, list_level=list_level)

    def _render_code_executions(self, pdf: FPDF, executions: list[dict[str, Any]]) -> None:
        pdf.set_font(self.font_family, "B", 12)
        pdf.set_text_color(17, 24, 39)
        self._write_full_width_block(pdf, 7, "代码执行")
        pdf.ln(1)

        for execution in executions:
            title = self._safe_text(execution.get("name")) or "执行记录"
            language = self._safe_text(execution.get("language"))
            code = self._safe_text(execution.get("code"))
            result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
            output = self._safe_text(result.get("output"))
            error = self._safe_text(result.get("error"))
            files = result.get("files") if isinstance(result.get("files"), list) else []

            pdf.set_font(self.font_family, "B", 11)
            self._write_full_width_block(pdf, self.LINE_HEIGHT, title)

            if code:
                self._render_code_block(pdf, code, language=language or "code")

            if output:
                self._render_labeled_block(pdf, "输出", output)

            if error:
                self._render_labeled_block(pdf, "错误", error, error_state=True)

            for file in files:
                if isinstance(file, dict) and isinstance(file.get("url"), str):
                    self._render_image_from_url(
                        pdf,
                        file.get("url"),
                        alt_text=file.get("name") or "输出图片",
                    )

            pdf.ln(2)

    def _render_code_block(self, pdf: FPDF, code: str, *, language: Optional[str] = None) -> None:
        normalized = self._normalize_code_text(code)
        if not normalized:
            return

        if language:
            pdf.set_font(self.font_family, "B", 9)
            pdf.set_text_color(107, 114, 128)
            self._write_full_width_block(pdf, self.SMALL_LINE_HEIGHT, language)

        fill = (248, 250, 252)
        pdf.set_fill_color(*fill)
        pdf.set_text_color(31, 41, 55)
        pdf.set_font(self.font_family, "", 9.5)
        for line in normalized.splitlines() or [""]:
            self._write_full_width_block(
                pdf,
                self.CODE_LINE_HEIGHT,
                line or " ",
                fill=True,
            )
        pdf.set_text_color(17, 24, 39)
        pdf.ln(1)

    def _render_labeled_block(
        self,
        pdf: FPDF,
        label: str,
        text: str,
        *,
        error_state: bool = False,
    ) -> None:
        pdf.set_font(self.font_family, "B", 9)
        pdf.set_text_color(107, 114, 128)
        self._write_full_width_block(pdf, self.SMALL_LINE_HEIGHT, label)
        pdf.set_font(self.font_family, "", 10)
        if error_state:
            pdf.set_text_color(185, 28, 28)
        else:
            pdf.set_text_color(31, 41, 55)
        self._write_full_width_block(pdf, self.LINE_HEIGHT, text)
        pdf.set_text_color(17, 24, 39)
        pdf.ln(1)

    def _render_image_from_url(
        self,
        pdf: FPDF,
        url: Any,
        *,
        alt_text: Optional[str] = None,
    ) -> None:
        try:
            resolved = resolve_chat_image_url_to_bytes(
                url,
                user_id=self.user_id,
                is_admin=self.is_admin,
            )
        except Exception:
            resolved = None

        if not resolved:
            if alt_text:
                self._write_paragraph(pdf, alt_text)
            return

        mime_type, image_bytes = resolved
        image_path = self._create_temp_image_file(image_bytes, mime_type)
        if not image_path:
            if alt_text:
                self._write_paragraph(pdf, alt_text)
            return

        try:
            with Image.open(image_path) as image:
                width_px, height_px = image.size
        except Exception:
            if alt_text:
                self._write_paragraph(pdf, alt_text)
            return

        if width_px <= 0 or height_px <= 0:
            return

        max_width = self._content_width(pdf)
        max_height = self.MAX_IMAGE_HEIGHT
        scale = min(max_width / width_px, max_height / height_px, 1.0)
        width_mm = width_px * scale
        height_mm = height_px * scale

        if pdf.will_page_break(height_mm + 4):
            pdf.add_page()

        x = pdf.l_margin + (self._content_width(pdf) - width_mm) / 2
        y = pdf.get_y()
        pdf.image(image_path, x=x, y=y, w=width_mm, h=height_mm)
        pdf.ln(height_mm + 3)

    def _build_stats_line(self, message: dict[str, Any]) -> str:
        usage = self._extract_usage(message)
        if not usage:
            return ""

        total = usage.get("total_tokens")
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        elapsed = None
        speed = None

        if isinstance(usage.get("total_duration"), (int, float)) and usage.get("total_duration", 0) > 0:
            elapsed = f"{float(usage['total_duration']) / 1e9:.2f}"
        elif isinstance(message.get("completedAt"), (int, float)) and isinstance(
            message.get("timestamp"), (int, float)
        ):
            delta = float(message["completedAt"]) - float(message["timestamp"])
            if delta > 0:
                elapsed = f"{delta:.2f}"

        if isinstance(usage.get("response_token/s"), (int, float)):
            speed = f"{float(usage['response_token/s']):.2f}"
        elif isinstance(output_tokens, (int, float)) and elapsed:
            elapsed_value = float(elapsed)
            if elapsed_value > 0:
                speed = f"{float(output_tokens) / elapsed_value:.2f}"

        if isinstance(total, (int, float)):
            tokens = str(int(total))
        elif isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float)):
            tokens = str(int(input_tokens) + int(output_tokens))
        else:
            tokens = ""

        parts = []
        if speed:
            parts.append(f"速度：{speed} T/s")
        if tokens:
            parts.append(f"Token：{tokens}")
        if elapsed:
            parts.append(f"耗时：{elapsed} 秒")
        return " | ".join(parts)

    def _extract_usage(self, message: dict[str, Any]) -> dict[str, Any]:
        usage = message.get("usage")
        if isinstance(usage, dict):
            return usage

        info = message.get("info")
        if isinstance(info, dict):
            nested_usage = info.get("usage")
            if isinstance(nested_usage, dict):
                return nested_usage
            return info

        return {}

    def _format_model_label(self, message: dict[str, Any]) -> str:
        model_name = self._safe_text(message.get("modelName"))
        if model_name:
            return model_name

        model = self._safe_text(message.get("model"))
        return re.sub(r"^[0-9a-f]{8}\.", "", model, flags=re.IGNORECASE)

    def _normalize_role(self, role: Any) -> str:
        raw = self._safe_text(role).lower()
        if raw == "assistant":
            return "助手"
        if raw == "user":
            return "用户"
        if raw == "system":
            return "系统"
        if raw == "tool":
            return "工具"
        return raw.title() if raw else "消息"

    def _stringify_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = [self._stringify_content(item) for item in content]
            return "\n".join(part for part in parts if part)

        if isinstance(content, dict):
            for key in ("md", "text", "content", "value"):
                if key in content:
                    value = self._stringify_content(content[key])
                    if value:
                        return value
            parts = [self._stringify_content(value) for value in content.values()]
            return "\n".join(part for part in parts if part)

        if content is None:
            return ""

        return str(content)

    def _safe_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _normalize_text(self, text: str) -> str:
        normalized = unescape(text or "")
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def _normalize_code_text(self, text: str) -> str:
        normalized = unescape(text or "")
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized = normalized.rstrip("\n")
        return normalized

    def _inline_text(self, tag: Tag, *, strip_images: bool = False) -> str:
        clone = BeautifulSoup(str(tag), "html.parser")
        root = clone.find(tag.name) or clone

        if strip_images:
            for image in root.find_all("img"):
                image.extract()

        text = root.get_text(" ", strip=True)
        return self._normalize_text(text)

    def _normalize_inline_text(self, text: str) -> str:
        normalized = unescape(text or "")
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[ \t\n\f\v]+", " ", normalized)
        return normalized

    def _merge_font_style(self, current: str, addition: str) -> str:
        merged = set((current or "").upper())
        merged.update((addition or "").upper())
        return "".join(style for style in "BIU" if style in merged)

    def _has_visible_inline_content(self, nodes: list[Any]) -> bool:
        for node in nodes:
            if isinstance(node, NavigableString) and self._normalize_inline_text(str(node)).strip():
                return True
            if isinstance(node, Tag):
                if node.name and node.name.lower() == "img":
                    continue
                if self._normalize_inline_text(node.get_text(" ", strip=True)).strip():
                    return True
        return False

    def _render_inline_nodes(
        self,
        pdf: FPDF,
        nodes: list[Any],
        *,
        line_height: float,
        font_size: float,
        style: str = "",
    ) -> None:
        for node in nodes:
            if isinstance(node, NavigableString):
                text = self._normalize_inline_text(str(node))
                if text:
                    pdf.set_font(self.font_family, style, font_size)
                    pdf.write(line_height, text)
                continue

            if not isinstance(node, Tag):
                continue

            name = node.name.lower()

            if name == "br":
                pdf.ln(line_height)
                pdf.set_x(pdf.l_margin)
                continue

            if name in {"strong", "b"}:
                self._render_inline_nodes(
                    pdf,
                    list(node.children),
                    line_height=line_height,
                    font_size=font_size,
                    style=self._merge_font_style(style, "B"),
                )
                continue

            if name in {"em", "i"}:
                self._render_inline_nodes(
                    pdf,
                    list(node.children),
                    line_height=line_height,
                    font_size=font_size,
                    style=self._merge_font_style(style, "I"),
                )
                continue

            if name == "img":
                continue

            self._render_inline_nodes(
                pdf,
                list(node.children),
                line_height=line_height,
                font_size=font_size,
                style=style,
            )

    def _write_inline_block(
        self,
        pdf: FPDF,
        nodes: list[Any],
        *,
        line_height: float,
        font_size: float,
        style: str = "",
    ) -> None:
        pdf.set_x(pdf.l_margin)
        self._render_inline_nodes(
            pdf,
            nodes,
            line_height=line_height,
            font_size=font_size,
            style=style,
        )

    def _finish_inline_block(
        self,
        pdf: FPDF,
        line_height: float,
        *,
        extra_gap: float = 0,
    ) -> None:
        if abs(pdf.get_x() - pdf.l_margin) > 0.1:
            pdf.ln(line_height)
        if extra_gap:
            pdf.ln(extra_gap)

    def _extract_list_intro_nodes(self, item: Tag) -> list[Any]:
        nodes: list[Any] = []

        for child in item.children:
            if isinstance(child, NavigableString):
                if self._normalize_inline_text(str(child)).strip():
                    nodes.append(child)
                continue

            if not isinstance(child, Tag):
                continue

            name = child.name.lower()
            if name in {"ul", "ol"}:
                continue
            if name in {"pre", "blockquote", "table", "details"}:
                break

            nodes.append(child)

        return nodes

    def _extract_list_intro_text(self, item: Tag) -> str:
        parts: list[str] = []

        for child in item.children:
            if isinstance(child, NavigableString):
                text = self._normalize_text(str(child))
                if text:
                    parts.append(text)
                continue

            if not isinstance(child, Tag):
                continue

            name = child.name.lower()
            if name in {"ul", "ol"}:
                continue
            if name in {"pre", "blockquote", "table", "details"}:
                break

            text = self._inline_text(child)
            if text:
                parts.append(text)

        return self._normalize_text(" ".join(parts))

    def _extract_code_language(self, tag: Tag) -> Optional[str]:
        code_tag = tag.find("code")
        if not code_tag:
            return None

        for class_name in code_tag.get("class", []):
            if class_name.startswith("language-"):
                return class_name.removeprefix("language-")
        return None

    def _write_paragraph(self, pdf: FPDF, text: str) -> None:
        if not text:
            return

        pdf.set_font(self.font_family, "", 11)
        pdf.set_text_color(17, 24, 39)
        self._write_full_width_block(pdf, self.LINE_HEIGHT, text)
        pdf.ln(1)

    def _write_full_width_block(
        self,
        pdf: FPDF,
        line_height: float,
        text: str,
        **kwargs,
    ) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(self._content_width(pdf), line_height, text, **kwargs)

    def _draw_separator(self, pdf: FPDF) -> None:
        if pdf.get_y() > self.PAGE_MARGIN + 4:
            pdf.ln(1)
            pdf.set_draw_color(229, 231, 235)
            y = pdf.get_y()
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(3)

    def _content_width(self, pdf: FPDF) -> float:
        return pdf.w - pdf.l_margin - pdf.r_margin

    def _create_temp_image_file(self, data: bytes, mime_type: str) -> Optional[str]:
        extension = ".png"
        if mime_type == "image/jpeg":
            extension = ".jpg"
        elif mime_type == "image/webp":
            extension = ".webp"
        elif mime_type == "image/gif":
            extension = ".gif"

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
        temp_file.write(data)
        temp_file.close()
        self.temp_image_paths.append(temp_file.name)
        return temp_file.name

    def _cleanup_temp_images(self) -> None:
        for path in self.temp_image_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        self.temp_image_paths.clear()

    def _extract_details_body_source(self, tag: Tag) -> str:
        parts: list[str] = []
        for child in tag.contents:
            if isinstance(child, Tag) and child.name and child.name.lower() == "summary":
                continue
            parts.append(str(child))
        return "".join(parts).strip()

    def _format_duration_label(self, duration_seconds: float) -> str:
        total_seconds = max(0, int(round(duration_seconds)))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            if seconds > 0:
                return f"思考 {hours} 小时 {minutes} 分 {seconds} 秒"
            if minutes > 0:
                return f"思考 {hours} 小时 {minutes} 分"
            return f"思考 {hours} 小时"

        if minutes > 0:
            if seconds > 0:
                return f"思考 {minutes} 分 {seconds} 秒"
            return f"思考 {minutes} 分钟"

        return f"思考 {total_seconds} 秒"

    def _get_details_summary_text(self, tag: Tag) -> str:
        details_type = self._safe_text(tag.attrs.get("type")).lower()
        done = self._safe_text(tag.attrs.get("done")).lower() == "true"
        duration = tag.attrs.get("duration")

        if details_type == "reasoning":
            try:
                if done and duration is not None:
                    return self._format_duration_label(float(duration))
            except (TypeError, ValueError):
                pass
            return "思考中…"

        if details_type == "code_interpreter":
            return "已分析" if done else "分析中…"

        if details_type == "tool_calls":
            tool_name = self._safe_text(tag.attrs.get("name"))
            if done:
                return f"已执行工具：{tool_name}" if tool_name else "已执行工具"
            return f"执行中：{tool_name}" if tool_name else "执行中…"

        if details_type == "error":
            return "错误"

        summary = tag.find("summary")
        if summary:
            return self._inline_text(summary)

        return ""

    @contextmanager
    def _temporary_indent(self, pdf: FPDF, amount: float):
        original_margin = pdf.l_margin
        pdf.set_left_margin(original_margin + amount)
        pdf.set_x(original_margin + amount)
        try:
            yield
        finally:
            pdf.set_left_margin(original_margin)
            pdf.set_x(original_margin)

    def _configure_fonts(self, pdf: FPDF) -> None:
        errors: list[str] = []

        for font_dir in self._iter_font_dirs():
            legacy_error = self._try_configure_legacy_fonts(pdf, font_dir)
            if legacy_error is None:
                return
            errors.append(legacy_error)

            halo_error = self._try_configure_halo_fonts(pdf, font_dir)
            if halo_error is None:
                return
            errors.append(halo_error)

        if errors:
            raise RuntimeError(errors[-1])

        raise RuntimeError(
            "服务端缺少可用中文 PDF 字体，当前环境无法完成文档型 PDF 导出。"
        )

    def _find_font_file(
        self,
        font_dir: Path,
        base_name: str,
        *,
        extensions: tuple[str, ...],
    ) -> Path | None:
        for extension in extensions:
            candidate = font_dir / f"{base_name}{extension}"
            if candidate.exists():
                return candidate
        return None

    def _build_font_cache_key(self, font_path: Path) -> str:
        stat = font_path.stat()
        resolved = font_path.resolve()
        return f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}"

    def _ensure_woff2_decoder_ready(self) -> None:
        decoder_module_name = None

        for module_name in ("brotlicffi", "brotli"):
            try:
                import_module(module_name)
                decoder_module_name = module_name
                break
            except ImportError:
                continue

        if decoder_module_name is None:
            raise RuntimeError(
                "服务端缺少 Brotli 字体解码依赖，当前环境无法读取 Halo 自带中文字体。"
                "请安装 brotli 后再导出 PDF。"
            )

        try:
            woff2_module = import_module("fontTools.ttLib.woff2")
        except Exception as exc:
            raise RuntimeError(
                "服务端中文字体转换链初始化失败，当前环境无法完成文档型 PDF 导出。"
            ) from exc

        if getattr(woff2_module, "haveBrotli", False):
            return

        reloaded_module = reload(woff2_module)
        if not getattr(reloaded_module, "haveBrotli", False):
            raise RuntimeError(
                f"服务端缺少 Brotli 字体解码依赖，当前环境无法读取 Halo 自带中文字体。"
                f"已检测到 {decoder_module_name} 安装异常，请重新安装后再导出 PDF。"
            )

    def _materialize_font_for_fpdf(self, font_path: Path) -> Path:
        suffix = font_path.suffix.lower()
        if suffix in {".ttf", ".otf"}:
            return font_path

        if suffix != ".woff2":
            raise RuntimeError(
                "服务端缺少可用中文 PDF 字体，当前环境无法完成文档型 PDF 导出。"
            )

        cache_key = self._build_font_cache_key(font_path)

        with _FONT_CACHE_LOCK:
            cached_path = _FONT_FILE_CACHE.get(cache_key)
            if cached_path and cached_path.exists():
                return cached_path

        try:
            from fontTools.ttLib import TTFont
        except Exception as exc:
            raise RuntimeError(
                "服务端缺少可用中文 PDF 字体处理能力，当前环境无法完成文档型 PDF 导出。"
                "请补齐可用的中文 ttf/otf 字体，或安装 Halo 自带字体所需的转换依赖。"
            ) from exc

        self._ensure_woff2_decoder_ready()

        cache_name = sha256(cache_key.encode("utf-8")).hexdigest()[:16]
        cached_path = _FONT_CACHE_DIR / f"{font_path.stem}-{cache_name}.ttf"

        with _FONT_CACHE_LOCK:
            existing_path = _FONT_FILE_CACHE.get(cache_key)
            if existing_path and existing_path.exists():
                return existing_path

            if cached_path.exists():
                _FONT_FILE_CACHE[cache_key] = cached_path
                return cached_path

            _FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

            tt_font = None
            try:
                tt_font = TTFont(str(font_path))
                tt_font.flavor = None
                tt_font.save(str(cached_path))
                _FONT_FILE_CACHE[cache_key] = cached_path
                return cached_path
            except Exception as exc:
                try:
                    if cached_path.exists():
                        cached_path.unlink()
                except OSError:
                    pass
                if "brotli" in str(exc).lower():
                    raise RuntimeError(
                        "服务端缺少 Brotli 字体解码依赖，当前环境无法读取 Halo 自带中文字体。"
                        "请安装 brotli 后再导出 PDF。"
                    ) from exc
                raise RuntimeError(
                    "服务端缺少可用中文 PDF 字体，当前环境无法完成文档型 PDF 导出。"
                    "请补齐可用的中文 ttf/otf 字体，或检查 Halo 自带字体转换链是否可用。"
                ) from exc
            finally:
                if tt_font is not None:
                    tt_font.close()

    def _iter_font_dirs(self) -> list[Path]:
        candidates = [
            Path(FONTS_DIR),
            STATIC_DIR / "fonts",
            STATIC_DIR / "assets" / "fonts",
        ]

        try:
            candidates.append(Path(site.getsitepackages()[0]) / "static" / "fonts")
        except Exception:
            pass

        candidates.append(Path(".") / "backend" / "static" / "fonts")

        unique_candidates: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            candidate_str = str(candidate)
            if candidate_str in seen or not candidate.exists():
                continue
            seen.add(candidate_str)
            unique_candidates.append(candidate)

        return unique_candidates

    def _try_configure_legacy_fonts(self, pdf: FPDF, font_dir: Path) -> str | None:
        regular = self._find_font_file(font_dir, "NotoSans-Regular", extensions=(".ttf", ".otf"))
        bold = self._find_font_file(font_dir, "NotoSans-Bold", extensions=(".ttf", ".otf"))
        italic = self._find_font_file(font_dir, "NotoSans-Italic", extensions=(".ttf", ".otf"))

        if not regular or not bold:
            return (
                "服务端缺少旧版 PDF 导出字体资源（NotoSans-Regular.ttf / NotoSans-Bold.ttf），"
                "无法继续使用文档型 PDF 导出。"
            )

        try:
            pdf.add_font("NotoSans", "", str(regular))
            pdf.add_font("NotoSans", "b", str(bold))
            if italic:
                pdf.add_font("NotoSans", "i", str(italic))

            fallback_fonts: list[str] = []
            for family, filename in [
                ("NotoSansKR", "NotoSansKR-Regular.ttf"),
                ("NotoSansJP", "NotoSansJP-Regular.ttf"),
                ("NotoSansSC", "NotoSansSC-Regular.ttf"),
                ("Twemoji", "Twemoji.ttf"),
            ]:
                stem, _ = os.path.splitext(filename)
                font_path = self._find_font_file(font_dir, stem, extensions=(".ttf", ".otf"))
                if not font_path:
                    continue
                pdf.add_font(family, "", str(font_path))
                fallback_fonts.append(family)

            self.font_family = "NotoSans"
            pdf.set_font(self.font_family, size=11)
            if fallback_fonts:
                pdf.set_fallback_fonts(fallback_fonts)
            return None
        except Exception as exc:
            return "服务端缺少可用中文 PDF 字体，当前环境无法完成文档型 PDF 导出。"

    def _try_configure_halo_fonts(self, pdf: FPDF, font_dir: Path) -> str | None:
        regular = self._find_font_file(
            font_dir,
            "HarmonyOS_SansSC_Regular",
            extensions=(".ttf", ".otf", ".woff2"),
        )
        bold = self._find_font_file(
            font_dir,
            "HarmonyOS_SansSC_Bold",
            extensions=(".ttf", ".otf", ".woff2"),
        )

        if not regular:
            return "服务端缺少可用中文 PDF 字体，当前环境无法完成文档型 PDF 导出。"

        try:
            pdf.add_font("HaloSansSC", "", str(self._materialize_font_for_fpdf(regular)))
            if bold:
                pdf.add_font("HaloSansSC", "b", str(self._materialize_font_for_fpdf(bold)))

            self.font_family = "HaloSansSC"
            pdf.set_font(self.font_family, size=11)
            return None
        except RuntimeError as exc:
            return str(exc)
        except Exception:
            return "服务端缺少可用中文 PDF 字体，当前环境无法完成文档型 PDF 导出。"

    def _safe_int(self, value: Any, *, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback
