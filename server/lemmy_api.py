"""Admin endpoints for managing fold (Lemmy) account linkage.

Each band member links their own fold account here — username + password,
captured once, exchanged for a JWT, encrypted server-side, stored. Plain
credentials are never persisted.

Endpoints
---------
GET    /api/admin/lemmy/status   → {configured, linked, lemmy_user_id, lemmy_url}
POST   /api/admin/lemmy/link     → {fold_username, fold_password} → status
POST   /api/admin/lemmy/unlink   → 204
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.auth import get_db, require_admin
from server.lemmy_client import (
    LemmyUnavailable,
    is_configured,
    link_account,
    status_for_user,
    unlink_account,
)
from server.models import User


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/lemmy", tags=["Lemmy"])


class LinkBody(BaseModel):
    fold_username: str = Field(..., min_length=1, max_length=120)
    fold_password: str = Field(..., min_length=1)


@router.get("/status", summary="Lemmy linkage status for the calling user")
def lemmy_status(
    user: User = Depends(require_admin),
):
    return status_for_user(user)


@router.post("/link", summary="Link your fold account")
def lemmy_link(
    body: LinkBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not is_configured():
        raise HTTPException(status_code=503, detail="Lemmy not configured on this server")
    try:
        result = link_account(db, user, body.fold_username, body.fold_password)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**status_for_user(user), **result}


@router.post("/unlink", status_code=204, summary="Unlink your fold account")
def lemmy_unlink(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    unlink_account(db, user)
    return None
