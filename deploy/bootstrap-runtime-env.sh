#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]] || [[ ! "$2" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: bootstrap-runtime-env.sh <absolute-env-file> <git-sha>" >&2
  exit 2
fi

env_file="$1"
release_sha="$2"
shared_dir="$(dirname "$env_file")"
backup_key_file="$shared_dir/backup-encryption.key"
if [[ -e "$env_file" ]]; then
  echo "runtime environment already exists; refusing to overwrite" >&2
  exit 1
fi
if [[ -e "$backup_key_file" ]] || [[ -L "$backup_key_file" ]]; then
  echo "backup encryption key already exists; refusing to overwrite" >&2
  exit 1
fi

umask 077
mkdir -p "$shared_dir"

app_secret="$(openssl rand -hex 32)"
postgres_password="$(openssl rand -hex 24)"
redis_password="$(openssl rand -hex 24)"
admin_password="$(openssl rand -hex 12)"
phone_key="$(openssl rand -hex 32)"
phone_pepper="$(openssl rand -hex 32)"
webhook_secret="$(openssl rand -hex 32)"
callback_token="$(openssl rand -hex 32)"
llm_key="$(openssl rand -hex 32)"
knowledge_token="$(openssl rand -hex 32)"
backup_key="$(openssl rand -hex 32)"

cat > "$env_file" <<EOF
APP_ENV=production
APP_VERSION=$release_sha
APP_SECRET_KEY=$app_secret
POSTGRES_DB=avatar_delivery
POSTGRES_USER=avatar
POSTGRES_PASSWORD=$postgres_password
DATABASE_URL=postgresql+psycopg://avatar:$postgres_password@postgres:5432/avatar_delivery
REDIS_PASSWORD=$redis_password
REDIS_URL=redis://:$redis_password@redis:6379/0
REDIS_REQUIRED=true
ADMIN_USERNAME=admin
ADMIN_BOOTSTRAP_PASSWORD=$admin_password
PHONE_ENCRYPTION_KEY=$phone_key
PHONE_LOOKUP_PEPPER=$phone_pepper
WECHAT_APP_ID=wx318698f4c753111e
WECHAT_APP_SECRET=
WECHAT_API_BASE_URL=https://api.weixin.qq.com
USER_TOKEN_MAX_AGE_SECONDS=2592000
ENTITLEMENT_WEBHOOK_SECRET=$webhook_secret
WEBHOOK_MAX_CLOCK_SKEW_SECONDS=300
TENCENT_VOD_SECRET_ID=
TENCENT_VOD_SECRET_KEY=
TENCENT_VOD_SUB_APP_ID=0
TENCENT_VOD_PROCEDURE=avatarDeliveryHLS
TENCENT_VOD_STORAGE_REGION=
TENCENT_VOD_CALLBACK_TOKEN=$callback_token
LLM_ENCRYPTION_KEY=$llm_key
PUBLIC_BASE_URL=https://www.qianlan333.cloud
PLAYBACK_LEASE_TTL_SECONDS=120
PLAYBACK_HEARTBEAT_INTERVAL_SECONDS=30
PLAYBACK_SESSION_LIMIT=100
PLAYBACK_ALERT_THRESHOLD=80
PLAYBACK_PRIORITY_ALERT_THRESHOLD=90
PLAYBACK_REDIRECT_TOKEN_MAX_AGE_SECONDS=180
CHAT_RESERVATION_TTL_SECONDS=60
LLM_CONCURRENCY_LIMIT=10
LLM_REQUEST_TIMEOUT_SECONDS=30
KNOWLEDGE_INTERNAL_TOKEN=$knowledge_token
KNOWLEDGE_INJECTION_ENABLED=false
KNOWLEDGE_CONTEXT_MAX_CHARS=6000
KNOWLEDGE_EMBEDDING_BATCH_SIZE=32
FEISHU_ALERT_WEBHOOK=
EOF

chmod 600 "$env_file"
printf '%s\n' "$backup_key" > "$backup_key_file"
chmod 600 "$backup_key_file"
printf '%s\n' "$admin_password" > "$shared_dir/bootstrap-admin-password.txt"
chmod 600 "$shared_dir/bootstrap-admin-password.txt"
echo "runtime environment created; external integration credentials remain disabled"
