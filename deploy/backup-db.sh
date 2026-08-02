#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: backup-db.sh <runtime-env-file> <application-image>" >&2
  exit 2
fi

env_file="$1"
application_image="$2"
backup_dir="/srv/avatar-delivery/backups"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
encrypted_name="avatar-delivery-${timestamp}.dump.aesgcm"
encrypted_path="$backup_dir/$encrypted_name"
staging_token="$(openssl rand -hex 8)"
staging_name=".$encrypted_name.$staging_token.staging"
staging_path="$backup_dir/$staging_name"
key_file="$(dirname "$env_file")/backup-encryption.key"
backup_complete="false"

if [[ ! -f "$env_file" ]] || [[ -z "$application_image" ]]; then
  echo "runtime environment or application image is unavailable" >&2
  exit 2
fi

umask 077
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
exec 8>"$backup_dir/.backup.lock"
if ! flock -n 8; then
  echo "another database backup is already running" >&2
  exit 1
fi

cleanup() {
  exit_code=$?
  if [[ "$backup_complete" != "true" ]]; then
    rm -f "$staging_path" "$staging_path.partial"
  fi
  trap - EXIT
  exit "$exit_code"
}
trap cleanup EXIT

if [[ -e "$encrypted_path" ]] || [[ -L "$encrypted_path" ]]; then
  echo "backup output already exists; refusing to overwrite or remove it" >&2
  exit 1
fi

if [[ ! -f "$key_file" ]] || [[ -L "$key_file" ]]; then
  echo "dedicated backup encryption key is unavailable" >&2
  exit 2
fi
backup_key="$(<"$key_file")"
backup_key="${backup_key%$'\r'}"
if [[ ${#backup_key} -lt 32 ]] \
  || [[ "$backup_key" == *$'\n'* ]] \
  || [[ "$backup_key" == replace-* ]]; then
  echo "backup encryption key is missing or too short" >&2
  exit 2
fi
if [[ "$(stat -c '%a' "$key_file")" != "600" ]]; then
  echo "backup encryption key permissions must be 600" >&2
  exit 2
fi
unset backup_key

if [[ "$(sudo docker inspect -f '{{.State.Running}}' avatar-delivery-postgres 2>/dev/null || true)" != "true" ]]; then
  echo "existing PostgreSQL container is not running" >&2
  exit 1
fi
if [[ "$(sudo docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' avatar-delivery-postgres 2>/dev/null || true)" != "avatar-delivery" ]]; then
  echo "PostgreSQL container belongs to another compose project" >&2
  exit 1
fi

sudo docker exec avatar-delivery-postgres \
  sh -ec 'PGPASSWORD="${POSTGRES_PASSWORD:?}" exec pg_dump --username "${POSTGRES_USER:?}" --dbname "${POSTGRES_DB:?}" --format=custom' \
| sudo docker run --rm -i \
  --user "$(id -u):$(id -g)" \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --env BACKUP_ENCRYPTION_KEY_FILE=/run/secrets/backup-key \
  --volume "$key_file:/run/secrets/backup-key:ro" \
  --volume "$backup_dir:/backup" \
  "$application_image" \
  python -m backend.app.backup_crypto \
  encrypt - "/backup/$staging_name"

test -s "$staging_path"
test "$(stat -c '%a' "$staging_path")" = "600"
sudo docker run --rm \
  --user "$(id -u):$(id -g)" \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --env BACKUP_ENCRYPTION_KEY_FILE=/run/secrets/backup-key \
  --volume "$key_file:/run/secrets/backup-key:ro" \
  --volume "$backup_dir:/backup:ro" \
  "$application_image" \
  python -m backend.app.backup_crypto \
  verify "/backup/$staging_name"

# Hard-link publication is atomic and fails closed if a same-second backup
# appeared after the initial check. Cleanup only ever touches this run's unique
# staging path, never an existing final backup.
if ! ln "$staging_path" "$encrypted_path"; then
  echo "backup output collision detected during atomic publication" >&2
  exit 1
fi
rm -f "$staging_path"

backup_complete="true"
printf '%s\n' "$encrypted_path"
