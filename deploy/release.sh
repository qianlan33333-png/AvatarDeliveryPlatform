#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: release.sh <absolute-release-dir> <git-sha>" >&2
  exit 2
fi

release_dir="$1"
release_sha="$2"
base_dir="/srv/avatar-delivery"
env_file="$base_dir/shared/runtime.env"
compose_file="$release_dir/compose.production.yaml"
image="avatar-delivery-platform:$release_sha"
previous_dir=""

if [[ "$release_dir" != "$base_dir/releases/"* ]] || [[ ! -f "$compose_file" ]]; then
  echo "invalid release directory" >&2
  exit 2
fi
if [[ ! "$release_sha" =~ ^[0-9a-f]{40}$ ]] || [[ ! -f "$env_file" ]]; then
  echo "release SHA or runtime environment is invalid" >&2
  exit 2
fi
if [[ -L "$base_dir/current" ]]; then
  previous_dir="$(readlink -f "$base_dir/current")"
fi

compose() {
  sudo env APP_IMAGE="$image" APP_RELEASE_SHA="$release_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$compose_file" "$@"
}

rollback_previous() {
  if [[ -z "$previous_dir" ]] || [[ ! -f "$previous_dir/compose.production.yaml" ]]; then
    return
  fi
  previous_sha="$(basename "$previous_dir")"
  sudo env APP_IMAGE="avatar-delivery-platform:$previous_sha" APP_RELEASE_SHA="$previous_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$previous_dir/compose.production.yaml" \
    up -d postgres redis api worker
}

cd "$release_dir"
sudo docker build \
  --label "org.opencontainers.image.revision=$release_sha" \
  --tag "$image" .
compose config >/dev/null
compose up -d postgres redis
compose run --rm migrate
compose up -d api worker

healthy="false"
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:18080/health \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)'; then
    healthy="true"
    break
  fi
  sleep 2
done

if [[ "$healthy" != "true" ]]; then
  compose logs --tail=200 api worker >&2 || true
  rollback_previous
  exit 1
fi

if [[ -n "$previous_dir" ]] && [[ "$previous_dir" != "$release_dir" ]]; then
  basename "$previous_dir" > "$base_dir/previous-sha"
fi
ln -sfn "$release_dir" "$base_dir/current.next"
mv -Tf "$base_dir/current.next" "$base_dir/current"
printf '%s\n' "$release_sha" > "$base_dir/current-sha"
compose ps
