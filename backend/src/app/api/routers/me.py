"""Current-user profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.schemas.common import UserOut
from app.core.deps import current_user

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me", response_model=UserOut)
async def get_me(user=Depends(current_user)):
    return user
