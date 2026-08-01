from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from backend.app.config import get_settings
from backend.app.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI):
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
    application.include_router(health_router)

    @application.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/admin", status_code=307)

    return application


app = create_app()

