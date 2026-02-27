import base64
import math
from decimal import Decimal
from io import BytesIO
from typing import Optional, Union, Tuple

import httpx
from PIL import Image
from fastapi import HTTPException
from pydantic import BaseModel

from open_webui.config import (
    USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE,
    USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE,
    USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE,
    USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE,
    USAGE_CALCULATE_DEFAULT_TOKEN_PRICE,
    USAGE_CALCULATE_DEFAULT_REQUEST_PRICE,
    CREDIT_NO_CREDIT_MSG,
    USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE,
)
from open_webui.models.chats import Chats
from open_webui.models.credits import Credits
from open_webui.models.models import Models, ModelModel


def get_model_price(
    model: Optional[ModelModel] = None,
    is_embedding: Optional[bool] = False,
) -> Tuple[
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
]:
    """
    Returns
    - prompt price
    - completion price
    - prompt long ctx threshold
    - prompt price long ctx
    - completion long ctx threshold
    - completion price long ctx
    - prompt cache price
    - prompt cache price long ctx
    - request price
    - minimum credit
    """
    # embedding
    if is_embedding:
        return (
            Decimal(USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE.value),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
        )
    # no model provide
    if not model or not isinstance(model, ModelModel):
        return (
            Decimal(USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value),
            Decimal(USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(USAGE_CALCULATE_DEFAULT_REQUEST_PRICE.value),
            Decimal(0),
        )
    # base model
    if model.base_model_id:
        base_model = Models.get_model_by_id(model.base_model_id)
        if base_model:
            return get_model_price(base_model)
    # model price
    model_price = model.price or {}
    return (
        Decimal(
            model_price.get("prompt_price", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value)
        ),
        Decimal(
            model_price.get(
                "completion_price", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value
            )
        ),
        Decimal(
            model_price.get(
                "prompt_long_ctx_tokens", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value
            )
        ),
        Decimal(
            model_price.get(
                "prompt_long_ctx_price", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value
            )
        ),
        Decimal(
            model_price.get(
                "completion_long_ctx_tokens", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value
            )
        ),
        Decimal(
            model_price.get(
                "completion_long_ctx_price", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value
            )
        ),
        Decimal(
            model_price.get(
                "prompt_cache_price", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value
            )
        ),
        Decimal(
            model_price.get(
                "prompt_long_ctx_cache_price", USAGE_CALCULATE_DEFAULT_TOKEN_PRICE.value
            )
        ),
        Decimal(
            model_price.get(
                "request_price", USAGE_CALCULATE_DEFAULT_REQUEST_PRICE.value
            )
        ),
        Decimal(model_price.get("minimum_credit", 0)),
    )


def get_feature_price(features: Union[set, list]) -> Decimal:
    if not features:
        return Decimal(0)
    price = Decimal(0)
    for feature in features:
        match feature:
            case "image_generation":
                price += (
                    Decimal(USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE.value) / 1000 / 1000
                )
            case "code_interpreter":
                price += (
                    Decimal(USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE.value)
                    / 1000
                    / 1000
                )
            case "web_search":
                price += (
                    Decimal(USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE.value)
                    / 1000
                    / 1000
                )
            case "direct_tool_servers":
                price += (
                    Decimal(USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE.value)
                    / 1000
                    / 1000
                )
    return price


def is_free_request(model_price: list, form_data: dict) -> bool:
    is_free_model = sum(float(price) for price in model_price) <= 0

    features = (
        form_data.get("features")
        or (form_data.get("metadata") or {}).get("features")
        or {}
    )
    is_feature_free = get_feature_price({k for k, v in features.items() if v}) <= 0

    return is_free_model and is_feature_free


def check_credit_by_user_id(
    user_id: str, form_data: dict, is_embedding: bool = False
) -> None:
    # load model
    model_id = form_data.get("model") or form_data.get("model_id") or ""
    model = Models.get_model_by_id(model_id)
    (
        prompt_price,
        completion_price,
        prompt_long_ctx_tokens,
        prompt_long_ctx_price,
        completion_long_ctx_tokens,
        completion_long_ctx_price,
        prompt_cache_price,
        prompt_long_ctx_cache_price,
        request_price,
        minimum_credit,
    ) = get_model_price(model, is_embedding=is_embedding)
    # check for free
    if is_free_request(
        model_price=[
            prompt_price,
            completion_price,
            prompt_long_ctx_price,
            completion_long_ctx_price,
            prompt_cache_price,
            prompt_long_ctx_cache_price,
            request_price,
        ],
        form_data=form_data,
    ):
        return
    # load credit
    metadata = form_data.get("metadata") or form_data
    credit = Credits.init_credit_by_user_id(user_id=user_id)
    # check for credit
    if credit is None or credit.credit <= 0 or credit.credit < minimum_credit:
        if isinstance(metadata, dict) and metadata:
            chat_id = metadata.get("chat_id")
            message_id = metadata.get("message_id") or metadata.get("id")
            if chat_id and message_id:
                Chats.upsert_message_to_chat_by_id_and_message_id(
                    chat_id,
                    message_id,
                    {"error": {"content": CREDIT_NO_CREDIT_MSG.value}},
                )
        raise HTTPException(status_code=403, detail=CREDIT_NO_CREDIT_MSG.value)


class ImageURL(BaseModel):
    url: str
    detail: str


def calculate_image_token(model_id: str, image: ImageURL) -> int:
    if not image or not image.url:
        return 0

    base_tokens = 85

    if image.detail == "low":
        return 85

    if image.detail == "auto" or not image.detail:
        image.detail = "high"

    tile_tokens = 170

    if model_id.find("gpt-4o-mini") != -1:
        tile_tokens = 5667
        base_tokens = 2833

    if model_id.find("gemini") != -1 or model_id.find("claude") != -1:
        return 3 * base_tokens

    if image.url.startswith("http"):
        with httpx.Client(trust_env=True, timeout=60) as client:
            response = client.get(image.url)
        response.raise_for_status()
        image_data = base64.b64encode(response.content).decode("utf-8")
    else:
        if "," in image.url:
            image_data = image.url.split(",", 1)[1]
        else:
            from open_webui.utils.files import get_image_base64_from_url

            image_data = get_image_base64_from_url(image.url) or image.url

    image_data = base64.b64decode(image_data.encode("utf-8"))
    image = Image.open(BytesIO(image_data))
    width, height = image.size

    short_side = width
    other_side = height

    scale = 1.0

    if height < short_side:
        short_side = height
        other_side = width

    if short_side > 768:
        scale = short_side / 768
        short_side = 768

    other_side = math.ceil(other_side / scale)

    tiles = (short_side + 511) / 512 * ((other_side + 511) / 512)

    return math.ceil(tiles * tile_tokens + base_tokens)


def check_amount(amount: float, amount_control: str) -> bool:
    if not amount_control:
        return True
    checks = amount_control.split(",")
    for check in checks:
        values = check.strip().split("-")
        if len(values) == 2:
            if float(values[0].strip()) <= amount <= float(values[1].strip()):
                return True
        if len(values) == 1:
            if float(values[0].strip()) == amount:
                return True
    return False
