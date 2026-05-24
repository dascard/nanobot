"""Prompt Runtime V2 管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import get_db


router = APIRouter()


class PromptV2TemplateCreateRequest(BaseModel):
    template_key: str
    content: str
    name: str = ""
    kind: str = "tool"
    tool_name: str = ""
    description: str = ""


class PromptV2TemplateSaveRequest(BaseModel):
    content: str


class PromptV2FlowSaveRequest(BaseModel):
    flow: dict = Field(default_factory=dict)


@router.get("/prompt-v2/variables")
def list_prompt_v2_variables(
    template: str = Query(""),
    _auth=Depends(verify_admin),
):
    from core.prompt_v2.variables import list_variables

    template_key = str(template or "").removesuffix(".md").strip()
    return {
        "template": template_key,
        "items": list_variables(template_key),
    }


@router.get("/prompt-v2/templates")
def list_prompt_v2_templates(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.prompt_v2.template_store import list_templates

    return list_templates(db=db)


@router.post("/prompt-v2/templates")
def create_prompt_v2_template(
    body: PromptV2TemplateCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.prompt_v2.template_store import create_template
    from core.prompt_v2.variables import PromptVariableError

    try:
        result = create_template(
            body.template_key,
            content=body.content,
            name=body.name,
            kind=body.kind,
            tool_name=body.tool_name,
            description=body.description,
        )
    except PromptVariableError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    audit_request(db, request, "create_prompt_v2_template", "prompt_v2", result["template_key"], result)
    return result


@router.get("/prompt-v2/templates/{template_key:path}")
def get_prompt_v2_template(template_key: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.prompt_v2.template_store import get_template

    try:
        return get_template(template_key, db=db)
    except FileNotFoundError:
        raise HTTPException(404, "V2 模板不存在")
    except Exception as e:
        raise HTTPException(400, str(e))


@router.put("/prompt-v2/templates/{template_key:path}")
def save_prompt_v2_template(
    template_key: str,
    body: PromptV2TemplateSaveRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.prompt_v2.template_store import save_template
    from core.prompt_v2.variables import PromptVariableError

    try:
        result = save_template(template_key, body.content)
    except PromptVariableError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    audit_request(db, request, "save_prompt_v2_template", "prompt_v2", template_key, result)
    return result


@router.delete("/prompt-v2/templates/{template_key:path}")
def delete_prompt_v2_template(
    template_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.prompt_v2.template_store import delete_runtime_template

    try:
        result = delete_runtime_template(template_key)
    except Exception as e:
        raise HTTPException(400, str(e))
    audit_request(db, request, "delete_prompt_v2_template", "prompt_v2", result["template_key"], result)
    return result


@router.post("/prompt-v2/templates/{template_key:path}/reset")
def reset_prompt_v2_template(
    template_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.prompt_v2.template_store import reset_template

    try:
        result = reset_template(template_key)
    except Exception as e:
        raise HTTPException(400, str(e))
    audit_request(db, request, "reset_prompt_v2_template", "prompt_v2", result["template_key"], result)
    return result


@router.get("/prompt-v2/flow")
def get_prompt_v2_flow(_auth=Depends(verify_admin)):
    from core.prompt_v2.flow import default_flow_path, load_flow, runtime_flow_path

    flow = load_flow()
    return {
        "flow": flow.flow,
        "source": flow.source,
        "path": str(flow.path),
        "default_path": str(default_flow_path()),
        "runtime_path": str(runtime_flow_path()),
    }


@router.put("/prompt-v2/flow")
def save_prompt_v2_flow(
    body: PromptV2FlowSaveRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.prompt_v2.flow import PromptFlowError, save_flow

    try:
        result = save_flow(body.flow)
    except PromptFlowError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    audit_request(db, request, "save_prompt_v2_flow", "prompt_v2_flow", "chat_flow", result)
    return result
