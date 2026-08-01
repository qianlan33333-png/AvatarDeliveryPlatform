# Avatar Delivery Platform Agent Entry

This repository owns the standalone avatar and course-delivery platform.

## Hard boundaries

- Do not import, modify, deploy, or share runtime state with AI-CRM or HuangYouCanAI.
- `49.232.57.128` is not a deployment target for this repository.
- Video bytes must go directly between clients and Tencent VOD/CDN; the API must never proxy uploads or playback segments.
- PostgreSQL `course_entitlements` is the only current entitlement truth. Event rows are immutable audit records.
- Production integrations remain disabled until their credentials are explicitly configured.

## Frontend rules

- Reuse the HuangYouCanAI cream/yellow visual language without copying its growth-product information architecture.
- Level-one admin pages contain lists, filters, and primary actions only. Create/edit/upload flows use level-two pages.
- A page has one page-level title. Do not repeat it inside the first card.

## Delivery

- Every change requires tests appropriate to its risk.
- `main` is production-bound only after CI, exact-image deployment, health checks, and rollback metadata succeed.

