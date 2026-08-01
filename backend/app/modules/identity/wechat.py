from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from backend.app.config import Settings, get_settings


class WeChatAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeChatSession:
    openid: str
    unionid: str | None = None


class WeChatClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._access_token_lock = threading.Lock()

    def _require_credentials(self) -> None:
        if not self.settings.wechat_app_id or not self.settings.wechat_app_secret:
            raise WeChatAPIError("微信小程序 AppID/AppSecret 尚未配置")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.wechat_api_base_url.rstrip('/')}{path}"
        try:
            with httpx.Client(timeout=6.0) as client:
                response = client.request(method, url, params=params, json=json_body)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeChatAPIError("微信接口暂时不可用") from exc
        if not isinstance(payload, dict):
            raise WeChatAPIError("微信接口返回格式异常")
        error_code = int(payload.get("errcode", 0) or 0)
        if error_code:
            error_message = str(payload.get("errmsg", "unknown error"))
            raise WeChatAPIError(f"微信接口错误 {error_code}: {error_message}")
        return payload

    def exchange_login_code(self, code: str) -> WeChatSession:
        self._require_credentials()
        payload = self._request_json(
            "GET",
            "/sns/jscode2session",
            params={
                "appid": self.settings.wechat_app_id,
                "secret": self.settings.wechat_app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
        )
        openid = str(payload.get("openid", ""))
        if not openid:
            raise WeChatAPIError("微信登录结果缺少 openid")
        unionid = str(payload["unionid"]) if payload.get("unionid") else None
        return WeChatSession(openid=openid, unionid=unionid)

    def _get_access_token(self) -> str:
        self._require_credentials()
        with self._access_token_lock:
            now = time.monotonic()
            if self._access_token and now < self._access_token_expires_at:
                return self._access_token
            payload = self._request_json(
                "GET",
                "/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.settings.wechat_app_id,
                    "secret": self.settings.wechat_app_secret,
                },
            )
            access_token = str(payload.get("access_token", ""))
            if not access_token:
                raise WeChatAPIError("微信 access_token 获取失败")
            expires_in = max(300, int(payload.get("expires_in", 7200)))
            self._access_token = access_token
            self._access_token_expires_at = now + expires_in - 120
            return access_token

    def exchange_phone_code(self, code: str) -> str:
        access_token = self._get_access_token()
        payload = self._request_json(
            "POST",
            "/wxa/business/getuserphonenumber",
            params={"access_token": access_token},
            json_body={"code": code},
        )
        phone_info = payload.get("phone_info")
        if not isinstance(phone_info, dict):
            raise WeChatAPIError("微信手机号结果缺少 phone_info")
        phone = str(phone_info.get("phoneNumber") or phone_info.get("purePhoneNumber") or "")
        if not phone:
            raise WeChatAPIError("微信手机号结果为空")
        return phone


@lru_cache
def get_wechat_client() -> WeChatClient:
    return WeChatClient()
