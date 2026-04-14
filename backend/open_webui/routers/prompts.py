from typing import Optional

from open_webui.models.prompts import (
    PromptForm,
    PromptMetaForm,
    PromptUserResponse,
    PromptListResponse,
    PromptModel,
    Prompts,
)
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from open_webui.utils.auth import get_verified_user
from open_webui.utils.access_control import (
    can_read_resource,
    can_write_resource,
    ensure_requested_access_control_allowed,
    ensure_resource_acl_change_allowed,
    has_permission,
)

router = APIRouter()


############################
# GetPrompts (unpaginated, legacy)
############################


@router.get("/", response_model=list[PromptModel])
async def get_prompts(user=Depends(get_verified_user)):
    if user.role == "admin":
        prompts = Prompts.get_prompts()
    else:
        prompts = Prompts.get_prompts_by_user_id(user.id, "read")
    return prompts


############################
# GetPromptList (paginated)
############################


@router.get("/list", response_model=PromptListResponse)
async def get_prompt_list(
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=100),
    order_by: str = Query("updated_at"),
    user=Depends(get_verified_user),
):
    if user.role == "admin":
        result = Prompts.get_prompts_paginated(
            page=page, limit=limit, order_by=order_by
        )
    else:
        # For non-admin, still use access-filtered list then paginate in-memory
        all_prompts = Prompts.get_prompts_by_user_id(user.id, "write")
        total = len(all_prompts)
        start = (page - 1) * limit
        items = all_prompts[start : start + limit]
        result = PromptListResponse(items=items, total=total)
    return result


############################
# CreateNewPrompt
############################


@router.post("/create", response_model=Optional[PromptModel])
async def create_new_prompt(
    request: Request, form_data: PromptForm, user=Depends(get_verified_user)
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.prompts", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    ensure_requested_access_control_allowed(
        request,
        user,
        form_data.access_control,
        public_permission_key="sharing.public_prompts",
    )

    prompt = Prompts.get_prompt_by_command(form_data.command)
    if prompt is None:
        prompt = Prompts.insert_new_prompt(user.id, form_data)

        if prompt:
            return prompt
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.COMMAND_TAKEN,
    )


############################
# GetPromptById
############################


@router.get("/id/{prompt_id}", response_model=Optional[PromptModel])
async def get_prompt_by_id(prompt_id: str, user=Depends(get_verified_user)):
    prompt = Prompts.get_prompt_by_id(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if can_read_resource(user, prompt):
        return prompt

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
    )


############################
# UpdatePromptById
############################


@router.post("/id/{prompt_id}/update", response_model=Optional[PromptModel])
async def update_prompt_by_id(
    request: Request,
    prompt_id: str,
    form_data: PromptForm,
    user=Depends(get_verified_user),
):
    prompt = Prompts.get_prompt_by_id(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, prompt):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if "access_control" not in getattr(form_data, "model_fields_set", set()):
        form_data.access_control = prompt.access_control

    ensure_resource_acl_change_allowed(
        request,
        user,
        prompt,
        form_data.access_control,
        public_permission_key="sharing.public_prompts",
    )

    result = Prompts.update_prompt_by_id(prompt_id, form_data)
    if result:
        return result
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.DEFAULT(),
    )


############################
# UpdatePromptMetaById
############################


@router.post("/id/{prompt_id}/meta", response_model=Optional[PromptModel])
async def update_prompt_meta_by_id(
    prompt_id: str,
    form_data: PromptMetaForm,
    user=Depends(get_verified_user),
):
    prompt = Prompts.get_prompt_by_id(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, prompt):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Prompts.update_prompt_meta_by_id(prompt_id, form_data)
    if result:
        return result
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.DEFAULT(),
    )


############################
# TogglePromptById
############################


@router.post("/id/{prompt_id}/toggle", response_model=Optional[PromptModel])
async def toggle_prompt_by_id(
    prompt_id: str,
    user=Depends(get_verified_user),
):
    prompt = Prompts.get_prompt_by_id(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, prompt):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Prompts.toggle_prompt_by_id(prompt_id, not prompt.is_active)
    if result:
        return result
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.DEFAULT(),
    )


############################
# DeletePromptById
############################


@router.delete("/id/{prompt_id}/delete", response_model=bool)
async def delete_prompt_by_id(prompt_id: str, user=Depends(get_verified_user)):
    prompt = Prompts.get_prompt_by_id(prompt_id)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, prompt):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    return Prompts.delete_prompt_by_id(prompt_id)


############################
# Legacy command-based endpoints (backward compatibility)
############################


@router.get("/command/{command}", response_model=Optional[PromptModel])
async def get_prompt_by_command(command: str, user=Depends(get_verified_user)):
    prompt = Prompts.get_prompt_by_command(command)

    if prompt:
        if can_read_resource(user, prompt):
            return prompt
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ERROR_MESSAGES.NOT_FOUND,
    )


@router.post("/command/{command}/update", response_model=Optional[PromptModel])
async def update_prompt_by_command(
    request: Request,
    command: str,
    form_data: PromptForm,
    user=Depends(get_verified_user),
):
    prompt = Prompts.get_prompt_by_command(command)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, prompt):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if "access_control" not in getattr(form_data, "model_fields_set", set()):
        form_data.access_control = prompt.access_control

    ensure_resource_acl_change_allowed(
        request,
        user,
        prompt,
        form_data.access_control,
        public_permission_key="sharing.public_prompts",
    )

    result = Prompts.update_prompt_by_command(command, form_data)
    if result:
        return result
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.DEFAULT(),
    )


@router.post("/command/{command}/toggle", response_model=Optional[PromptModel])
async def toggle_prompt_by_command(
    command: str,
    user=Depends(get_verified_user),
):
    prompt = Prompts.get_prompt_by_command(command)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, prompt):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Prompts.toggle_prompt_by_command(command, not prompt.is_active)
    if result:
        return result
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.DEFAULT(),
    )


@router.delete("/command/{command}/delete", response_model=bool)
async def delete_prompt_by_command(command: str, user=Depends(get_verified_user)):
    prompt = Prompts.get_prompt_by_command(command)
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, prompt):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    return Prompts.delete_prompt_by_command(command)
