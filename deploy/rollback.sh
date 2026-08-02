#!/usr/bin/env bash
set -Eeuo pipefail

base_dir="/srv/avatar-delivery"
env_file="$base_dir/shared/runtime.env"
target_sha="${1:-}"
if [[ -z "$target_sha" ]] && [[ -f "$base_dir/previous-sha" ]]; then
  target_sha="$(tr -d '\r\n' < "$base_dir/previous-sha")"
fi
if [[ ! "$target_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "valid rollback SHA is required" >&2
  exit 2
fi

release_dir="$base_dir/releases/$target_sha"
compose_file="$release_dir/compose.production.yaml"
image="avatar-delivery-platform:$target_sha"
if [[ ! -f "$compose_file" ]] || ! sudo docker image inspect "$image" >/dev/null 2>&1; then
  echo "rollback release or image is unavailable" >&2
  exit 2
fi

sudo env APP_IMAGE="$image" AVATAR_ENV_FILE="$env_file" \
  docker compose --project-name avatar-delivery \
  --env-file "$env_file" -f "$compose_file" up -d postgres redis api worker

for _ in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:18080/health \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)'; then
    ln -sfn "$release_dir" "$base_dir/current.next"
    mv -Tf "$base_dir/current.next" "$base_dir/current"
    printf '%s\n' "$target_sha" > "$base_dir/current-sha"
    exit 0
  fi
  sleep 2
done
exit 1
