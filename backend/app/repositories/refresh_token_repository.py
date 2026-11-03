"""Truy vấn bảng refresh_tokens (AU-4, AU-5)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


@dataclass(frozen=True)
class RotatedToken:
    user_id: uuid.UUID
    family_id: uuid.UUID


@dataclass(frozen=True)
class TokenRow:
    user_id: uuid.UUID
    family_id: uuid.UUID
    revoked_at: datetime | None


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> None:
    session.add(
        RefreshToken(
            user_id=user_id, family_id=family_id, token_hash=token_hash, expires_at=expires_at
        )
    )
    await session.flush()


async def revoke_if_active(session: AsyncSession, token_hash: str) -> RotatedToken | None:
    """Thu hồi token NẾU còn hiệu lực (chưa thu hồi, chưa hết hạn), trong MỘT câu UPDATE duy nhất.

    Đây là điểm chống đua: khi hai request cùng gửi một token, DB chỉ cho đúng MỘT request
    nhận được dòng trả về. Request còn lại nhận None → không bao giờ sinh ra hai token con.
    """
    row = (
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > func.now(),
            )
            .values(revoked_at=func.now())
            .returning(RefreshToken.user_id, RefreshToken.family_id)
        )
    ).one_or_none()
    return RotatedToken(row.user_id, row.family_id) if row else None


async def find_by_hash(session: AsyncSession, token_hash: str) -> TokenRow | None:
    row = (
        await session.execute(
            select(RefreshToken.user_id, RefreshToken.family_id, RefreshToken.revoked_at).where(
                RefreshToken.token_hash == token_hash
            )
        )
    ).one_or_none()
    return TokenRow(row.user_id, row.family_id, row.revoked_at) if row else None


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> int:
    """Thu hồi mọi token còn hiệu lực của một "họ". Trả về số token vừa bị thu hồi."""
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now())
        .returning(RefreshToken.id)
    )
    return len(result.all())
