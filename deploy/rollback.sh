#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -gt 1 ]]; then
  echo "usage: rollback.sh [target-git-sha]" >&2
  exit 2
fi

base_dir="/srv/avatar-delivery"
env_file="$base_dir/shared/runtime.env"
target_sha="${1:-}"
if [[ -z "$target_sha" ]] && [[ -f "$base_dir/previous-sha" ]]; then
  target_sha="$(tr -d '\r\n' < "$base_dir/previous-sha")"
fi
if [[ ! "$target_sha" =~ ^[0-9a-f]{40}$ ]] || [[ ! -f "$env_file" ]]; then
  echo "valid rollback SHA and runtime environment are required" >&2
  exit 2
fi

expected_release_dir="$base_dir/releases/$target_sha"
release_dir="$(readlink -f -- "$expected_release_dir" 2>/dev/null || true)"
compose_file="$release_dir/compose.production.yaml"
image="avatar-delivery-platform:$target_sha"
if [[ "$release_dir" != "$expected_release_dir" ]] \
  || [[ ! -f "$compose_file" ]] \
  || ! sudo docker image inspect "$image" >/dev/null 2>&1; then
  echo "rollback release or image is unavailable" >&2
  exit 2
fi

if [[ ! -L "$base_dir/current" ]]; then
  echo "current release pointer is unavailable" >&2
  exit 2
fi
current_dir="$(readlink -f -- "$base_dir/current" 2>/dev/null || true)"
current_sha="$(basename "$current_dir")"
current_compose="$current_dir/compose.production.yaml"
current_image="avatar-delivery-platform:$current_sha"
if [[ "$current_dir" != "$base_dir/releases/"* ]] \
  || [[ ! "$current_sha" =~ ^[0-9a-f]{40}$ ]] \
  || [[ ! -f "$current_compose" ]] \
  || ! sudo docker image inspect "$current_image" >/dev/null 2>&1; then
  echo "current release cannot be used as a rollback safety version" >&2
  exit 2
fi

exec 9>"$base_dir/shared/release.lock"
if ! flock -n 9; then
  echo "another release or rollback is already running" >&2
  exit 1
fi

render_services() {
  local selected_dir="$1"
  local selected_sha="$2"
  local selected_image="avatar-delivery-platform:$selected_sha"
  sudo env APP_IMAGE="$selected_image" APP_RELEASE_SHA="$selected_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$selected_dir/compose.production.yaml" \
    config --services
}

container_ready() {
  local container_name="$1"
  local container_status
  container_status="$(sudo docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || true)"
  [[ "$container_status" == "healthy" || "$container_status" == "running" ]]
}

stop_project_service() {
  local service="$1"
  local container_ids=()
  mapfile -t container_ids < <(
    sudo docker ps -q \
      --filter "label=com.docker.compose.project=avatar-delivery" \
      --filter "label=com.docker.compose.service=$service"
  )
  if [[ ${#container_ids[@]} -gt 0 ]]; then
    sudo docker stop "${container_ids[@]}" >/dev/null
  fi
}

activate_application() {
  local selected_dir="$1"
  local selected_sha="$2"
  local service_list="$3"
  local selected_image="avatar-delivery-platform:$selected_sha"
  local application_services=(api)
  if grep -qx "worker" <<<"$service_list"; then
    application_services+=(worker)
  else
    stop_project_service worker
  fi
  if grep -qx "knowledge-worker" <<<"$service_list"; then
    application_services+=(knowledge-worker)
  else
    stop_project_service knowledge-worker
  fi

  sudo env APP_IMAGE="$selected_image" APP_RELEASE_SHA="$selected_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$selected_dir/compose.production.yaml" \
    up -d --no-deps "${application_services[@]}"
}

wait_for_application() {
  local expected_sha="$1"
  local service_list="$2"
  local workers_ready
  for _ in $(seq 1 30); do
    if curl --fail --silent --show-error http://127.0.0.1:18080/health \
      | python3 -c 'import json,sys; expected=sys.argv[1]; data=json.load(sys.stdin); sys.exit(0 if data.get("ok") and data.get("version") == expected else 1)' "$expected_sha"; then
      workers_ready="true"
      if grep -qx "worker" <<<"$service_list" \
        && ! container_ready avatar-delivery-worker; then
        workers_ready="false"
      fi
      if grep -qx "knowledge-worker" <<<"$service_list" \
        && ! container_ready avatar-delivery-knowledge-worker; then
        workers_ready="false"
      fi
      if [[ "$workers_ready" == "true" ]]; then
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

log_application() {
  local selected_dir="$1"
  local selected_sha="$2"
  local service_list="$3"
  local selected_image="avatar-delivery-platform:$selected_sha"
  local application_services=(api)
  if grep -qx "worker" <<<"$service_list"; then
    application_services+=(worker)
  fi
  if grep -qx "knowledge-worker" <<<"$service_list"; then
    application_services+=(knowledge-worker)
  fi
  sudo env APP_IMAGE="$selected_image" APP_RELEASE_SHA="$selected_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$selected_dir/compose.production.yaml" \
    logs --tail=200 "${application_services[@]}" >&2 || true
}

target_services="$(render_services "$release_dir" "$target_sha")"
current_services="$(render_services "$current_dir" "$current_sha")"
if ! grep -qx "api" <<<"$target_services" || ! grep -qx "api" <<<"$current_services"; then
  echo "target and current compose files must both define the API service" >&2
  exit 2
fi

postgres_id="$(sudo docker inspect -f '{{.Id}}' avatar-delivery-postgres 2>/dev/null || true)"
redis_id="$(sudo docker inspect -f '{{.Id}}' avatar-delivery-redis 2>/dev/null || true)"
for infrastructure in postgres redis; do
  if [[ "$(sudo docker inspect -f '{{.State.Running}}' "avatar-delivery-$infrastructure" 2>/dev/null || true)" != "true" ]] \
    || [[ "$(sudo docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "avatar-delivery-$infrastructure" 2>/dev/null || true)" != "avatar-delivery" ]]; then
    echo "$infrastructure is not a running Avatar Delivery infrastructure container" >&2
    exit 1
  fi
done

if ! activate_application "$release_dir" "$target_sha" "$target_services" \
  || ! wait_for_application "$target_sha" "$target_services"; then
  log_application "$release_dir" "$target_sha" "$target_services"
  if [[ "$current_sha" != "$target_sha" ]]; then
    if ! activate_application "$current_dir" "$current_sha" "$current_services" \
      || ! wait_for_application "$current_sha" "$current_services"; then
      echo "target rollback failed and the original application could not be restored" >&2
    fi
  fi
  exit 1
fi

if [[ "$(sudo docker inspect -f '{{.Id}}' avatar-delivery-postgres)" != "$postgres_id" ]] \
  || [[ "$(sudo docker inspect -f '{{.Id}}' avatar-delivery-redis)" != "$redis_id" ]]; then
  echo "infrastructure container identity changed during application-only rollback" >&2
  exit 1
fi

if [[ "$current_sha" != "$target_sha" ]]; then
  printf '%s\n' "$current_sha" > "$base_dir/previous-sha"
fi
ln -sfn "$release_dir" "$base_dir/current.next"
mv -Tf "$base_dir/current.next" "$base_dir/current"
printf '%s\n' "$target_sha" > "$base_dir/current-sha"
