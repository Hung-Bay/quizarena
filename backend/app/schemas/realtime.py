"""Schemas cho WebSocket ticket (AU-11)."""

import uuid

from pydantic import BaseModel


class WsTicketRequest(BaseModel):
    game_id: uuid.UUID


class WsTicketResponse(BaseModel):
    ticket: str
    expires_in: int = 30  # giây
