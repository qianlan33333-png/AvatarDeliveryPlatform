import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("REDIS_REQUIRED", "false")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_BOOTSTRAP_PASSWORD", "test-admin-password")
os.environ.setdefault("APP_SECRET_KEY", "test-app-secret-key-with-at-least-32-characters")
os.environ.setdefault("PHONE_ENCRYPTION_KEY", "test-phone-encryption-key")
os.environ.setdefault("PHONE_LOOKUP_PEPPER", "test-phone-lookup-pepper")
os.environ.setdefault("WECHAT_APP_ID", "wx-test-avatar-delivery")
os.environ.setdefault("ENTITLEMENT_WEBHOOK_SECRET", "test-entitlement-webhook-secret")

import pytest

import backend.app.models  # noqa: E402,F401
from backend.app.db import Base, get_engine  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
