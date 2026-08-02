from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_backs_up_before_migration_and_keeps_candidate_off_host_ports() -> None:
    script = (ROOT / "deploy/release.sh").read_text()

    assert script.index('"$release_dir/deploy/backup-db.sh"') < script.index(
        "compose run --rm migrate"
    )
    assert "--publish 127.0.0.1:18081:8000" not in script
    assert 'docker exec "$candidate_name" python' in script
    assert "up -d --no-deps" in script
    assert "container_ready avatar-delivery-worker" in script
    assert "container_ready avatar-delivery-knowledge-worker" in script
    assert "previous_services=(postgres redis" not in script
    assert "refusing a non-rollbackable release" in script
    assert (
        "database migration failed; automatic schema downgrade is intentionally disabled"
        in script
    )


def test_database_backup_streams_directly_into_isolated_encryption_container() -> None:
    script = (ROOT / "deploy/backup-db.sh").read_text()

    assert 'source "$env_file"' not in script
    assert "plain_name=" not in script
    assert "encrypt -" in script
    assert 'verify "/backup/$staging_name"' in script
    assert script.count("--network none") == 2
    assert 'PGPASSWORD=$POSTGRES_PASSWORD' not in script
    assert "PostgreSQL container belongs to another compose project" in script
    assert "backup output already exists; refusing to overwrite or remove it" in script
    assert "flock -n 8" in script
    assert 'rm -f "$encrypted_path"' not in script
    assert 'ln "$staging_path" "$encrypted_path"' in script
    assert 'rm -f "$staging_path" "$staging_path.partial"' in script
    assert '--env-file "$key_file"' not in script
    assert "grep '^BACKUP_ENCRYPTION_KEY='" not in script
    assert script.count("BACKUP_ENCRYPTION_KEY_FILE=/run/secrets/backup-key") == 2
    assert script.count('$key_file:/run/secrets/backup-key:ro') == 2


def test_release_migrates_backup_key_out_of_application_environment() -> None:
    script = (ROOT / "deploy/release.sh").read_text()

    assert 'backup_key_file="$base_dir/shared/backup-encryption.key"' in script
    assert "legacy and dedicated backup encryption keys differ" in script
    assert "if [[ \"$line\" == BACKUP_ENCRYPTION_KEY=* ]]" in script


def test_manual_rollback_never_recreates_infrastructure_and_supports_old_compose() -> None:
    script = (ROOT / "deploy/rollback.sh").read_text()

    assert 'application_services=(api)' in script
    assert 'application_services+=(worker)' in script
    assert 'application_services+=(knowledge-worker)' in script
    assert 'up -d --no-deps "${application_services[@]}"' in script
    assert "up -d postgres" not in script
    assert "up -d redis" not in script
    assert 'services=(postgres redis' not in script
    assert "infrastructure container identity changed" in script
    assert 'data.get("version") == expected' in script
    assert 'wait_for_application "$target_sha" "$target_services"' in script
    assert "container_ready avatar-delivery-worker" in script
    assert "container_ready avatar-delivery-knowledge-worker" in script
