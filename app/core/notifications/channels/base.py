"""Contrato mínimo de un canal de notificación (spec 077, RF-009/RF-010,
research.md §4). Agregar un canal futuro (email/SMS/WhatsApp) es añadir una
clase que implemente `send()` y registrarla en `dispatch.py` — ningún punto de
llamada de negocio conoce esta interfaz."""
from __future__ import annotations

from typing import Protocol

from app.models.notification_event import NotificationEvent


class NotificationChannel(Protocol):
    def send(self, event: NotificationEvent) -> None:
        ...
