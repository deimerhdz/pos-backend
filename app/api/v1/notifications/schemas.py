from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.timezone import UtcDatetime


class NotificationResponse(BaseModel):
    id: UUID
    event_type: str
    related_entity_type: str
    related_entity_id: UUID
    payload: dict[str, Any]
    created_at: UtcDatetime
    attended_at: Optional[UtcDatetime] = None
    attended_by_user_id: Optional[UUID] = None
    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    """Forma de `GET /notifications` (contracts/notifications-api.md).

    En modo navegación (`page`/`size`) trae `total`/`page`/`size`. En modo
    recuperación (`after_id`) esos tres campos van `None` — la respuesta es
    solo `items`, en orden ascendente."""
    items: list[NotificationResponse]
    total: Optional[int] = None
    page: Optional[int] = None
    size: Optional[int] = None


class AttendResponse(BaseModel):
    id: UUID
    attended_at: UtcDatetime
    attended_by_user_id: UUID
    model_config = ConfigDict(from_attributes=True)


class PublicKeyResponse(BaseModel):
    public_key: str


class PushKeysIn(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: PushKeysIn
    user_agent: Optional[str] = None


class PushSubscriptionDeleteIn(BaseModel):
    endpoint: str
