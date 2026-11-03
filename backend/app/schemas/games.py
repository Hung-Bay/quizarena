"""Schemas cho Join Game và Guest Token (AU-9, AU-10)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class JoinGameRequest(BaseModel):
    pin: str = Field(min_length=6, max_length=6, description="Mã PIN 6 ký tự của trận đấu")
    nickname: str = Field(min_length=2, max_length=20, description="Biệt danh trong trận")


class JoinGameResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    game_id: uuid.UUID
    nickname: str
    type: Literal["guest", "player"]
