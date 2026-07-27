from typing import List, Optional

from pydantic import BaseModel, Field

from app.api.v1.schemas.auth import UserResponse


class PlatformAppResponse(BaseModel):
    id: str
    slug: str
    label: str
    description: str
    icon_key: str
    status: str
    default_route: str


class PlatformBootstrapResponse(BaseModel):
    user: UserResponse
    allowed_app_ids: List[str]
    default_app_id: Optional[str] = None
    apps: List[PlatformAppResponse]


class PlatformAppAccessUpdateRequest(BaseModel):
    allowed_app_ids: List[str] = Field(default_factory=list)
    default_app_id: Optional[str] = None
