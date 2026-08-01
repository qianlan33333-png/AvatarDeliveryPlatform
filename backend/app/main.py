from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from backend.app.config import get_settings
from backend.app.db import Base, get_engine
from backend.app.health import router as health_router
from backend.app.modules.admin.auth import bootstrap_admin
from backend.app.modules.admin.router import router as admin_router
from backend.app.modules.catalog.router import router as catalog_router
from backend.app.modules.media.router import admin_router as media_admin_router
from backend.app.modules.media.router import api_router as media_api_router

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.app_env in {"development", "test"}:
        # Production always runs Alembic before starting the API.
        import backend.app.models  # noqa: F401

        Base.metadata.create_all(bind=get_engine())
    bootstrap_admin()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        https_only=settings.is_production,
        same_site="lax",
        max_age=8 * 60 * 60,
    )
    application.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    application.include_router(health_router)
    application.include_router(admin_router)
    application.include_router(catalog_router)
    application.include_router(media_admin_router)
    application.include_router(media_api_router)

    @application.exception_handler(HTTPException)
    async def admin_auth_redirect(request: Request, exc: HTTPException):
        if exc.status_code == 401 and request.url.path.startswith("/admin"):
            target = f"/admin/login?next={request.url.path}"
            return RedirectResponse(target, status_code=303)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    @application.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/admin", status_code=307)

    return application


app = create_app()
