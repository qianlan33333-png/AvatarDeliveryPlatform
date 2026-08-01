from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models import User
from backend.app.security import SecurityValueError, load_user_token

DBSession = Annotated[Session, Depends(get_db)]


def _bearer_token(authorization: str) -> str:
    scheme, _, value = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token required",
        )
    return value.strip()


def current_user(
    db: DBSession,
    authorization: str = Header(""),
) -> User:
    try:
        user_id = load_user_token(_bearer_token(authorization))
    except SecurityValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user disabled")
    return user


def optional_current_user(
    db: DBSession,
    authorization: str = Header(""),
) -> User | None:
    if not authorization:
        return None
    return current_user(db, authorization)


CurrentUser = Annotated[User, Depends(current_user)]
OptionalCurrentUser = Annotated[User | None, Depends(optional_current_user)]
