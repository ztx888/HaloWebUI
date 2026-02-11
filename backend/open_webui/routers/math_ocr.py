import base64
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.utils.auth import get_verified_user
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.models import get_all_models

log = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_PROMPT = (
    "You are a math OCR assistant. Extract the mathematical expression from the image and "
    "return only valid LaTeX. Do not include explanations, markdown fences, or extra text. "
    "If there are multiple equations, separate them with line breaks."
)


class MathOCRConvertForm(BaseModel):
    image_base64: str
    model: Optional[str] = None
    prompt: Optional[str] = None


def _strip_data_url_prefix(image_base64: str) -> tuple[str, str]:
    image_base64 = image_base64.strip()
    match = re.match(
        r"^data:(?P<mime>image\/[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$",
        image_base64,
        re.DOTALL,
    )
    if not match:
        return "image/png", image_base64
    return match.group("mime"), match.group("data")


def _extract_text_content(response: dict) -> str:
    choices = response.get("choices", [])
    if not choices:
        return ""

    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append((item.get("text") or "").strip())
        return "\n".join([p for p in parts if p]).strip()

    return ""


def _cleanup_latex_output(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:latex)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


@router.post("/convert")
async def convert_math_ocr(
    request: Request, form_data: MathOCRConvertForm, user=Depends(get_verified_user)
):
    mime_type, raw_base64 = _strip_data_url_prefix(form_data.image_base64)

    try:
        base64.b64decode(raw_base64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")

    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    models = request.app.state.MODELS
    model_id = (
        form_data.model
        or request.app.state.config.MATH_OCR_MODEL_ID
        or request.app.state.config.TASK_MODEL
    )

    if not model_id:
        raise HTTPException(
            status_code=400,
            detail="No Math OCR model configured. Please set one in Admin Settings.",
        )

    if model_id not in models:
        raise HTTPException(
            status_code=404,
            detail=f"Configured model not found: {model_id}",
        )

    content = [
        {"type": "text", "text": form_data.prompt or DEFAULT_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{raw_base64}"}},
    ]

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as exc:
        log.exception("Math OCR conversion failed")
        raise HTTPException(status_code=500, detail=str(exc))

    latex = _cleanup_latex_output(_extract_text_content(response if isinstance(response, dict) else {}))
    if not latex:
        raise HTTPException(
            status_code=502,
            detail="Model returned an empty response.",
        )

    return {"latex": latex, "model": model_id}
