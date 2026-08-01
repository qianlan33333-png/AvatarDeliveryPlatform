from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.models import VideoAsset, WebhookEvent
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.media.service import (
    VODConfigurationError,
    build_upload_signature,
    canonical_payload_hash,
    duration_from,
    event_data_for,
    external_event_id,
    first_recursive_value,
    playback_url_from,
    session_context_asset_id,
)
from backend.app.web import templates

admin_router = APIRouter(prefix="/admin", tags=["admin-media"])
api_router = APIRouter(prefix="/api/v1/media", tags=["media-webhook"])


def get_asset_or_404(db: Session, asset_id: str) -> VideoAsset:
    asset = db.get(VideoAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="video asset not found")
    return asset


@admin_router.get("/media", response_class=HTMLResponse, include_in_schema=False)
def media_page(request: Request, db: DBSession, admin: CurrentAdmin):
    assets = list(db.scalars(select(VideoAsset).order_by(VideoAsset.created_at.desc())))
    return templates.TemplateResponse(
        request=request,
        name="admin/media.html",
        context=admin_context(request, admin, assets=assets),
    )


@admin_router.get("/media/new", response_class=HTMLResponse, include_in_schema=False)
def new_media_page(request: Request, admin: CurrentAdmin):
    return templates.TemplateResponse(
        request=request,
        name="admin/media_new.html",
        context=admin_context(request, admin, error=""),
    )


@admin_router.post("/media/new", response_class=HTMLResponse, include_in_schema=False)
def create_media(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    title: str = Form(...),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    normalized = title.strip()
    if not normalized:
        return templates.TemplateResponse(
            request=request,
            name="admin/media_new.html",
            context=admin_context(request, admin, error="素材标题不能为空"),
            status_code=422,
        )
    asset = VideoAsset(title=normalized)
    db.add(asset)
    db.flush()
    asset.session_context = json.dumps(
        {"asset_id": asset.id}, ensure_ascii=False, separators=(",", ":")
    )
    db.commit()
    return RedirectResponse(f"/admin/media/{asset.id}/upload", status_code=303)


@admin_router.get(
    "/media/{asset_id}/upload", response_class=HTMLResponse, include_in_schema=False
)
def upload_media_page(
    asset_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
):
    asset = get_asset_or_404(db, asset_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/media_upload.html",
        context=admin_context(request, admin, asset=asset),
    )


@admin_router.get("/media/{asset_id}/upload-signature", include_in_schema=False)
def upload_signature(asset_id: str, db: DBSession, _: CurrentAdmin):
    asset = get_asset_or_404(db, asset_id)
    if asset.status == "archived":
        raise HTTPException(status_code=409, detail="archived asset cannot be uploaded")
    settings = get_settings()
    try:
        signature = build_upload_signature(
            secret_id=settings.tencent_vod_secret_id,
            secret_key=settings.tencent_vod_secret_key,
            session_context=asset.session_context,
            sub_app_id=settings.tencent_vod_sub_app_id,
            procedure=settings.tencent_vod_procedure,
            storage_region=settings.tencent_vod_storage_region,
        )
    except VODConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "signature": signature.value,
        "expires_at": signature.expires_at,
        "asset_id": asset.id,
    }


@admin_router.get("/media/{asset_id}/status", include_in_schema=False)
def media_status(asset_id: str, db: DBSession, _: CurrentAdmin):
    asset = get_asset_or_404(db, asset_id)
    return {
        "id": asset.id,
        "status": asset.status,
        "error_message": asset.error_message,
        "provider_file_id": asset.provider_file_id,
    }


@admin_router.post("/media/{asset_id}/retry", include_in_schema=False)
def retry_media(
    asset_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    asset = get_asset_or_404(db, asset_id)
    if asset.status in {"ready", "archived"}:
        raise HTTPException(status_code=409, detail="asset cannot be retried")
    asset.status = "pending_upload"
    asset.error_message = ""
    asset.provider_file_id = None
    asset.source_url = ""
    asset.playback_url = ""
    db.commit()
    return RedirectResponse(f"/admin/media/{asset.id}/upload", status_code=303)


@admin_router.post("/media/{asset_id}/rename", include_in_schema=False)
def rename_media(
    asset_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    title: str = Form(...),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    asset = get_asset_or_404(db, asset_id)
    if not title.strip():
        raise HTTPException(status_code=422, detail="asset title is required")
    asset.title = title.strip()
    db.commit()
    return RedirectResponse(f"/admin/media/{asset.id}/upload", status_code=303)


@admin_router.post("/media/{asset_id}/archive", include_in_schema=False)
def archive_media(
    asset_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    asset = get_asset_or_404(db, asset_id)
    if asset.lessons:
        raise HTTPException(status_code=409, detail="bound asset cannot be archived")
    asset.status = "archived"
    db.commit()
    return RedirectResponse("/admin/media", status_code=303)


def _callback_token_is_valid(configured: str, query_token: str, header_token: str) -> bool:
    provided = header_token or query_token
    return bool(configured and provided and hmac.compare_digest(configured, provided))


def _locate_asset(db: Session, payload: dict[str, Any]) -> VideoAsset | None:
    asset_id = session_context_asset_id(payload)
    if asset_id:
        asset = db.get(VideoAsset, asset_id)
        if asset:
            return asset
    data = event_data_for(payload)
    file_id = str(data.get("FileId", ""))
    if file_id:
        return db.scalar(select(VideoAsset).where(VideoAsset.provider_file_id == file_id))
    return None


def _apply_callback(asset: VideoAsset, payload: dict[str, Any]) -> None:
    event_type = str(payload.get("EventType", ""))
    data = event_data_for(payload)
    file_id = str(data.get("FileId", ""))
    if file_id:
        asset.provider_file_id = file_id
    asset.provider_payload = payload
    if event_type == "NewFileUpload":
        asset.status = "processing"
        asset.source_url = str(data.get("MediaUrl", ""))
        asset.cover_url = str(data.get("CoverUrl", ""))
        return
    if event_type != "ProcedureStateChanged":
        return
    callback_status = str(data.get("Status", "")).upper()
    if callback_status in {"FAIL", "FAILED"}:
        asset.status = "failed"
        asset.error_message = str(
            first_recursive_value(data, {"ErrMsg", "ErrorMessage", "Message"})
            or "腾讯云 VOD 转码失败"
        )
        return
    if callback_status not in {"FINISH", "SUCCESS"}:
        asset.status = "processing"
        return
    playback_url = playback_url_from(payload)
    if not playback_url:
        asset.status = "failed"
        asset.error_message = "转码完成但回调中没有可播放地址"
        return
    asset.playback_url = playback_url
    asset.duration_seconds = duration_from(payload)
    cover = first_recursive_value(data, {"CoverUrl", "CoverURL"})
    if cover:
        asset.cover_url = str(cover)
    asset.status = "ready"
    asset.error_message = ""


@api_router.post("/vod/callback")
def vod_callback(
    payload: dict[str, Any],
    db: DBSession,
    token: str = Query(""),
    x_avatar_callback_token: str = Header(""),
):
    settings = get_settings()
    if not _callback_token_is_valid(
        settings.tencent_vod_callback_token, token, x_avatar_callback_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid callback token",
        )
    payload_hash = canonical_payload_hash(payload)
    event_id = external_event_id(payload, payload_hash)
    existing = db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == "tencent_vod",
            WebhookEvent.external_event_id == event_id,
        )
    )
    if existing:
        if existing.payload_hash != payload_hash:
            raise HTTPException(status_code=409, detail="callback event id conflict")
        return {"code": 0, "message": "duplicate"}

    event = WebhookEvent(
        provider="tencent_vod",
        external_event_id=event_id,
        payload_hash=payload_hash,
        payload=payload,
    )
    db.add(event)
    asset = _locate_asset(db, payload)
    if asset:
        _apply_callback(asset, payload)
        event.status = "processed"
    else:
        event.status = "ignored"
        event.error_message = "no matching video asset"
    event.processed_at = datetime.now(UTC)
    db.commit()
    return JSONResponse({"code": 0, "message": event.status})
