"""
Agent management API routes.
"""

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.routers.auth import get_current_user_from_token, require_app_access, verify_tenant_access
from app.api.v1.schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    PromptPreviewResponse,
    PromptTemplateListResponse,
    PromptTemplateOption,
    VoiceListResponse,
)
from app.api.v1.services.agent import agent_service
from app.core.decorators import handle_router_errors

router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(require_app_access("appointment_setter"))])


@router.post("/tenant/{tenant_id}", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
@handle_router_errors(operation_name="create agent")
async def create_agent(
    tenant_id: str,
    agent_data: AgentCreate,
    current_user: Dict = Depends(get_current_user_from_token),
):
    """
    Create a new agent for a tenant.

    - **tenant_id**: ID of the tenant
    - **agent_data**: Agent configuration including name, voice, phone number, etc.
    """
    verify_tenant_access(current_user, tenant_id)
    agent = await agent_service.create_agent(tenant_id, agent_data)
    from app.core.response_mappers import to_agent_response

    return to_agent_response(agent)


@router.get("/tenant/{tenant_id}", response_model=List[AgentResponse])
async def list_agents(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """
    List all agents for a tenant.

    - **tenant_id**: ID of the tenant
    """
    verify_tenant_access(current_user, tenant_id)
    agents = await agent_service.list_agents_by_tenant(tenant_id)
    from app.core.response_mappers import to_agent_response

    return [to_agent_response(agent) for agent in agents]


@router.get("/prompt-templates", response_model=PromptTemplateListResponse)
async def list_prompt_templates():
    """Return starter prompt templates for the agent configuration UI."""
    from app.core.prompts import STARTER_PROMPT_TEMPLATES

    return PromptTemplateListResponse(
        templates=[PromptTemplateOption(**template) for template in STARTER_PROMPT_TEMPLATES]
    )


@router.get("/prompt-preview", response_model=PromptPreviewResponse)
async def preview_default_prompt(
    service_type: str = "Home Services",
    agent_name: str = "Assistant",
    current_user: Dict = Depends(get_current_user_from_token),
):
    """Return the default system prompt for a service type (used when no custom prompt is saved)."""
    from app.core.prompts import get_template
    from app.core.validators import validate_service_type

    validated_service_type = validate_service_type(service_type)
    prompt = get_template(service_type=validated_service_type, agent_name=agent_name)
    return PromptPreviewResponse(
        prompt=prompt,
        service_type=validated_service_type,
        agent_name=agent_name,
    )


@router.get("/{agent_id}", response_model=AgentResponse)
@handle_router_errors(not_found_message="Agent not found", operation_name="get agent")
async def get_agent(agent_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """
    Get agent by ID.

    - **agent_id**: ID of the agent
    """
    agent = await agent_service.get_agent(agent_id)
    if agent:
        verify_tenant_access(current_user, agent.get("tenant_id"))
    from app.core.response_mappers import to_agent_response

    return to_agent_response(agent)


@router.put("/{agent_id}", response_model=AgentResponse)
@handle_router_errors(not_found_message="Agent not found", operation_name="update agent")
async def update_agent(agent_id: str, agent_data: AgentUpdate, current_user: Dict = Depends(get_current_user_from_token)):
    """
    Update an agent.

    - **agent_id**: ID of the agent
    - **agent_data**: Updated agent configuration
    """
    agent = await agent_service.get_agent(agent_id)
    if agent:
        verify_tenant_access(current_user, agent.get("tenant_id"))
    agent = await agent_service.update_agent(agent_id, agent_data)
    from app.core.response_mappers import to_agent_response

    return to_agent_response(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """
    Delete an agent.

    - **agent_id**: ID of the agent
    """
    try:
        agent = await agent_service.get_agent(agent_id)
        if agent:
            verify_tenant_access(current_user, agent.get("tenant_id"))
        success = await agent_service.delete_agent(agent_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        return None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete agent: {str(e)}")


@router.post("/{agent_id}/activate")
async def activate_agent(agent_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """
    Activate an agent.

    - **agent_id**: ID of the agent
    """
    try:
        agent = await agent_service.get_agent(agent_id)
        if agent:
            verify_tenant_access(current_user, agent.get("tenant_id"))
        success = await agent_service.activate_agent(agent_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        return {"message": "Agent activated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to activate agent: {str(e)}")


@router.post("/{agent_id}/deactivate")
async def deactivate_agent(agent_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """
    Deactivate an agent.

    - **agent_id**: ID of the agent
    """
    try:
        agent = await agent_service.get_agent(agent_id)
        if agent:
            verify_tenant_access(current_user, agent.get("tenant_id"))
        success = await agent_service.deactivate_agent(agent_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        return {"message": "Agent deactivated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to deactivate agent: {str(e)}")


@router.get("/voices/list", response_model=VoiceListResponse)
async def list_available_voices():
    """
    Get list of available Gemini Live voices.

    Returns the 30 prebuilt voices supported by Gemini Live for agent configuration.
    """
    try:
        voices = agent_service.get_available_voices()
        return VoiceListResponse(voices=voices, total=len(voices))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get voices: {str(e)}")
