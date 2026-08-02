#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: release.sh <absolute-release-dir> <git-sha>" >&2
  exit 2
fi

requested_release_dir="$1"
release_sha="$2"
base_dir="/srv/avatar-delivery"
env_file="$base_dir/shared/runtime.env"
backup_key_file="$base_dir/shared/backup-encryption.key"
release_dir="$(readlink -f -- "$requested_release_dir" 2>/dev/null || true)"
compose_file="$release_dir/compose.production.yaml"
image="avatar-delivery-platform:$release_sha"
previous_dir=""
candidate_name="avatar-delivery-api-candidate-${release_sha:0:12}"

if [[ "$requested_release_dir" != /* ]] \
  || [[ "$release_dir" != "$base_dir/releases/"* ]] \
  || [[ "$(basename "$release_dir")" != "$release_sha" ]] \
  || [[ ! -f "$compose_file" ]]; then
  echo "invalid release directory" >&2
  exit 2
fi
if [[ ! "$release_sha" =~ ^[0-9a-f]{40}$ ]] || [[ ! -f "$env_file" ]]; then
  echo "release SHA or runtime environment is invalid" >&2
  exit 2
fi
if [[ -e "$base_dir/current" ]] || [[ -L "$base_dir/current" ]]; then
  if [[ ! -L "$base_dir/current" ]]; then
    echo "current release path is not a symbolic link" >&2
    exit 2
  fi
  previous_dir="$(readlink -f -- "$base_dir/current" 2>/dev/null || true)"
  if [[ "$previous_dir" != "$base_dir/releases/"* ]] \
    || [[ ! "$(basename "$previous_dir")" =~ ^[0-9a-f]{40}$ ]] \
    || [[ ! -f "$previous_dir/compose.production.yaml" ]]; then
    echo "current release link points outside the release directory" >&2
    exit 2
  fi
fi

exec 9>"$base_dir/shared/release.lock"
if ! flock -n 9; then
  echo "another release is already running" >&2
  exit 1
fi

compose() {
  sudo env APP_IMAGE="$image" APP_RELEASE_SHA="$release_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$compose_file" "$@"
}

container_ready() {
  local container_name="$1"
  local container_status
  container_status="$(sudo docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || true)"
  [[ "$container_status" == "healthy" || "$container_status" == "running" ]]
}

rollback_previous() {
  if [[ -z "$previous_dir" ]] || [[ ! -f "$previous_dir/compose.production.yaml" ]]; then
    return 1
  fi
  local previous_sha
  previous_sha="$(basename "$previous_dir")"
  if [[ ! "$previous_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "previous release SHA is invalid" >&2
    return 1
  fi
  local previous_image="avatar-delivery-platform:$previous_sha"
  local previous_compose="$previous_dir/compose.production.yaml"
  local service_list
  if ! service_list="$(sudo env APP_IMAGE="$previous_image" APP_RELEASE_SHA="$previous_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$previous_compose" config --services)"; then
    echo "previous compose file cannot be rendered" >&2
    return 1
  fi
  local previous_services=(api)
  if grep -qx "worker" <<<"$service_list"; then
    previous_services+=(worker)
  fi
  if grep -qx "knowledge-worker" <<<"$service_list"; then
    previous_services+=(knowledge-worker)
  fi

  # Keep the already migrated PostgreSQL/Redis containers. Older compose files
  # may still name a pre-pgvector image, so rollback only application services.
  compose stop api worker knowledge-worker >/dev/null 2>&1 || true
  if ! sudo env APP_IMAGE="$previous_image" APP_RELEASE_SHA="$previous_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$previous_compose" \
    up -d --no-deps "${previous_services[@]}"; then
    echo "previous application services could not be restored" >&2
    return 1
  fi
  local previous_workers_ready
  for _ in $(seq 1 30); do
    if curl --fail --silent --show-error http://127.0.0.1:18080/health \
      | python3 -c 'import json,sys; expected=sys.argv[1]; data=json.load(sys.stdin); sys.exit(0 if data.get("ok") and data.get("version") == expected else 1)' "$previous_sha"; then
      previous_workers_ready="true"
      if grep -qx "worker" <<<"$service_list" \
        && ! container_ready avatar-delivery-worker; then
        previous_workers_ready="false"
      fi
      if grep -qx "knowledge-worker" <<<"$service_list" \
        && ! container_ready avatar-delivery-knowledge-worker; then
        previous_workers_ready="false"
      fi
      if [[ "$previous_workers_ready" == "true" ]]; then
        return 0
      fi
    fi
    sleep 2
  done
  sudo env APP_IMAGE="$previous_image" APP_RELEASE_SHA="$previous_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$previous_compose" \
    logs --tail=200 "${previous_services[@]}" >&2 || true
  return 1
}

validate_previous_rollback() {
  if [[ -z "$previous_dir" ]]; then
    return 0
  fi
  local previous_sha
  previous_sha="$(basename "$previous_dir")"
  local previous_image="avatar-delivery-platform:$previous_sha"
  if ! sudo docker image inspect "$previous_image" >/dev/null 2>&1; then
    echo "previous application image is unavailable; refusing a non-rollbackable release" >&2
    return 1
  fi
  if ! sudo env APP_IMAGE="$previous_image" APP_RELEASE_SHA="$previous_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$previous_dir/compose.production.yaml" \
    config >/dev/null; then
    echo "previous compose file is no longer renderable" >&2
    return 1
  fi
}

restore_previous_infrastructure() {
  if [[ -z "$previous_dir" ]]; then
    return 1
  fi
  local previous_sha
  previous_sha="$(basename "$previous_dir")"
  local previous_image="avatar-delivery-platform:$previous_sha"
  local previous_compose="$previous_dir/compose.production.yaml"
  local service_list
  service_list="$(sudo env APP_IMAGE="$previous_image" APP_RELEASE_SHA="$previous_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$previous_compose" config --services)" || return 1
  local infrastructure_services=()
  if grep -qx "postgres" <<<"$service_list"; then
    infrastructure_services+=(postgres)
  fi
  if grep -qx "redis" <<<"$service_list"; then
    infrastructure_services+=(redis)
  fi
  if [[ ${#infrastructure_services[@]} -eq 0 ]]; then
    return 1
  fi
  sudo env APP_IMAGE="$previous_image" APP_RELEASE_SHA="$previous_sha" AVATAR_ENV_FILE="$env_file" \
    docker compose --project-name avatar-delivery \
    --env-file "$env_file" -f "$previous_compose" \
    up -d "${infrastructure_services[@]}" || return 1
  if grep -qx "postgres" <<<"$service_list"; then
    for _ in $(seq 1 30); do
      if sudo docker exec avatar-delivery-postgres pg_isready --quiet; then
        return 0
      fi
      sleep 2
    done
    return 1
  fi
}

cleanup_candidate() {
  sudo docker rm -f "$candidate_name" >/dev/null 2>&1 || true
}

ensure_backup_encryption_key() {
  local key_lines=()
  mapfile -t key_lines < <(grep '^BACKUP_ENCRYPTION_KEY=' "$env_file" || true)
  if [[ ${#key_lines[@]} -gt 1 ]]; then
    echo "runtime environment contains duplicate backup encryption keys" >&2
    return 1
  fi
  local legacy_key=""
  if [[ ${#key_lines[@]} -eq 1 ]]; then
    legacy_key="${key_lines[0]#BACKUP_ENCRYPTION_KEY=}"
    legacy_key="${legacy_key%$'\r'}"
  fi

  local stored_key=""
  if [[ -e "$backup_key_file" ]] || [[ -L "$backup_key_file" ]]; then
    if [[ ! -f "$backup_key_file" ]] || [[ -L "$backup_key_file" ]]; then
      echo "backup encryption key must be a regular non-symlink file" >&2
      return 1
    fi
    stored_key="$(<"$backup_key_file")"
    stored_key="${stored_key%$'\r'}"
    if [[ ${#stored_key} -lt 32 ]] \
      || [[ "$stored_key" == *$'\n'* ]] \
      || [[ "$stored_key" == replace-* ]]; then
      echo "dedicated backup encryption key is invalid" >&2
      return 1
    fi
    if [[ -n "$legacy_key" ]] && [[ "$legacy_key" != "$stored_key" ]]; then
      echo "legacy and dedicated backup encryption keys differ; refusing rotation" >&2
      return 1
    fi
    chmod 600 "$backup_key_file"
  else
    if [[ -n "$legacy_key" ]]; then
      if [[ ${#legacy_key} -lt 32 ]] \
        || [[ "$legacy_key" == *$'\n'* ]] \
        || [[ "$legacy_key" == replace-* ]]; then
        echo "legacy backup encryption key is invalid" >&2
        return 1
      fi
      stored_key="$legacy_key"
    else
      stored_key="$(openssl rand -hex 32)"
    fi

    local key_replacement
    key_replacement="$(mktemp "$base_dir/shared/.backup-encryption.key.XXXXXX")"
    if ! printf '%s\n' "$stored_key" > "$key_replacement" \
      || ! chmod 600 "$key_replacement" \
      || ! mv -f "$key_replacement" "$backup_key_file"; then
      rm -f "$key_replacement"
      echo "backup encryption key could not be persisted" >&2
      return 1
    fi
  fi

  # Keep the backup-only key out of API/worker container environments. Existing
  # installations are migrated without rotating the key so old backups remain
  # decryptable.
  if [[ ${#key_lines[@]} -eq 1 ]]; then
    local replacement=""
    replacement="$(mktemp "$base_dir/shared/.runtime.env.XXXXXX")"
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" == BACKUP_ENCRYPTION_KEY=* ]]; then
        continue
      fi
      printf '%s\n' "$line" >> "$replacement"
    done < "$env_file"
    if ! chmod 600 "$replacement" || ! mv -f "$replacement" "$env_file"; then
      rm -f "$replacement"
      echo "backup key could not be removed from the application environment" >&2
      return 1
    fi
  fi
  unset legacy_key stored_key key_lines
}

trap cleanup_candidate EXIT

cd "$release_dir"
sudo docker build \
  --label "org.opencontainers.image.revision=$release_sha" \
  --tag "$image" .

umask 077
ensure_backup_encryption_key
compose config >/dev/null
validate_previous_rollback

postgres_id="$(sudo docker inspect -f '{{.Id}}' avatar-delivery-postgres 2>/dev/null || true)"
if [[ -n "$postgres_id" ]]; then
  postgres_project="$(sudo docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' avatar-delivery-postgres 2>/dev/null || true)"
  if [[ "$postgres_project" != "avatar-delivery" ]]; then
    echo "PostgreSQL container belongs to another compose project" >&2
    exit 1
  fi
  if [[ "$(sudo docker inspect -f '{{.State.Running}}' avatar-delivery-postgres)" != "true" ]]; then
    sudo docker start avatar-delivery-postgres >/dev/null
  fi
  postgres_ready="false"
  for _ in $(seq 1 30); do
    if sudo docker exec avatar-delivery-postgres pg_isready --quiet; then
      postgres_ready="true"
      break
    fi
    sleep 2
  done
  if [[ "$postgres_ready" != "true" ]]; then
    echo "existing PostgreSQL did not become ready; refusing to migrate" >&2
    exit 1
  fi
  "$release_dir/deploy/backup-db.sh" "$env_file" "$image"
else
  if [[ -n "$previous_dir" ]]; then
    echo "an existing release has no PostgreSQL container; refusing to create an empty database" >&2
    exit 1
  fi
  echo "first deployment: no existing PostgreSQL database to back up"
fi
if ! compose up -d postgres redis; then
  compose logs --tail=200 postgres redis >&2 || true
  if ! restore_previous_infrastructure; then
    echo "new infrastructure failed and previous infrastructure could not be restored" >&2
  fi
  exit 1
fi
if ! compose run --rm migrate; then
  compose logs --tail=200 postgres redis >&2 || true
  echo "database migration failed; automatic schema downgrade is intentionally disabled" >&2
  exit 1
fi

# Start the candidate beside the currently healthy API first. A bad image or
# runtime configuration must not replace the version serving port 18080.
cleanup_candidate
sudo docker run -d \
  --name "$candidate_name" \
  --env-file "$env_file" \
  --env "APP_VERSION=$release_sha" \
  --network avatar-delivery_default \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --pids-limit 256 \
  --memory 640m \
  --cpus 0.90 \
  "$image" >/dev/null

candidate_healthy="false"
for _ in $(seq 1 30); do
  if sudo docker exec "$candidate_name" python -c \
    'import json,sys,urllib.request; expected=sys.argv[1]; data=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)); sys.exit(0 if data.get("ok") and data.get("version") == expected else 1)' \
    "$release_sha"; then
    candidate_healthy="true"
    break
  fi
  sleep 2
done
if [[ "$candidate_healthy" != "true" ]]; then
  sudo docker logs --tail=200 "$candidate_name" >&2 || true
  exit 1
fi
cleanup_candidate
if ! compose up -d api worker knowledge-worker; then
  compose logs --tail=200 api worker knowledge-worker >&2 || true
  if ! rollback_previous; then
    echo "new services failed and the previous application could not be restored" >&2
  fi
  exit 1
fi

healthy="false"
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:18080/health \
    | python3 -c "import json,sys; data=json.load(sys.stdin); sys.exit(0 if data.get('ok') and data.get('version') == '$release_sha' else 1)"; then
    if container_ready avatar-delivery-worker \
      && container_ready avatar-delivery-knowledge-worker; then
      healthy="true"
      break
    fi
  fi
  sleep 2
done

if [[ "$healthy" != "true" ]]; then
  compose logs --tail=200 api worker knowledge-worker >&2 || true
  if ! rollback_previous; then
    echo "new release is unhealthy and the previous application could not be restored" >&2
  fi
  exit 1
fi

if [[ -n "$previous_dir" ]] && [[ "$previous_dir" != "$release_dir" ]]; then
  basename "$previous_dir" > "$base_dir/previous-sha"
fi
ln -sfn "$release_dir" "$base_dir/current.next"
mv -Tf "$base_dir/current.next" "$base_dir/current"
printf '%s\n' "$release_sha" > "$base_dir/current-sha"
compose ps
