# 分身交付平台 V1 Implementation Plan

## Goal

交付独立的微信小程序课程平台，首版仅包含用户、课程、视频素材、课程权益 webhook、基础问答和课程推荐。

## Architecture

- FastAPI modular monolith owns identity, entitlement, catalog, playback admission, admin and chat orchestration.
- PostgreSQL is the only durable truth; Redis is used only for leases, limits and ephemeral tickets.
- Tencent VOD/CDN owns upload, transcode, storage and video delivery.
- AI-CRM and HuangYouCanAI remain isolated reference systems only.

## Execution batches

1. Repository skeleton, health, Docker and CI.
2. Data model, admin authentication and minimal admin shell.
3. Course, lesson and Tencent VOD media management.
4. WeChat identity, phone binding and entitlement webhook.
5. Native mini-program catalog and player.
6. Basic chat and keyword course recommendation.
7. Admission control, monitoring, deploy and mixed-load verification.

