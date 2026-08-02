from __future__ import annotations

import io
import stat

import pytest
from cryptography.exceptions import InvalidTag

from backend.app.backup_crypto import (
    BackupCryptoError,
    _load_secret,
    decrypt_backup,
    encrypt_backup,
    encrypt_stream,
    verify_backup,
)


def test_backup_encryption_round_trip_and_tamper_detection(tmp_path) -> None:
    source = tmp_path / "database.dump"
    encrypted = tmp_path / "database.dump.aesgcm"
    restored = tmp_path / "database.restored"
    source.write_bytes((b"avatar-delivery-backup\0" * 100_000) + b"tail")
    secret = "backup-secret-with-more-than-thirty-two-characters"

    encrypt_backup(source, encrypted, secret)
    assert encrypted.read_bytes()[:5] == b"AVBK1"
    assert source.read_bytes() != encrypted.read_bytes()
    assert stat.S_IMODE(encrypted.stat().st_mode) == 0o600
    verify_backup(encrypted, secret)

    decrypt_backup(encrypted, restored, secret)
    assert restored.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(restored.stat().st_mode) == 0o600

    tampered = bytearray(encrypted.read_bytes())
    tampered[-20] ^= 1
    encrypted.write_bytes(tampered)
    second_restore = tmp_path / "tampered.restored"
    with pytest.raises(InvalidTag):
        decrypt_backup(encrypted, second_restore, secret)
    with pytest.raises(InvalidTag):
        verify_backup(encrypted, secret)
    assert not second_restore.exists()
    assert not (tmp_path / "tampered.restored.partial").exists()


def test_backup_encryption_rejects_short_secret(tmp_path) -> None:
    source = tmp_path / "database.dump"
    source.write_bytes(b"test")
    with pytest.raises(BackupCryptoError):
        encrypt_backup(source, tmp_path / "encrypted", "too-short")
    assert not (tmp_path / "encrypted").exists()
    assert not (tmp_path / "encrypted.partial").exists()


def test_streaming_encryption_never_overwrites_destination(tmp_path) -> None:
    destination = tmp_path / "database.dump.aesgcm"
    secret = "backup-secret-with-more-than-thirty-two-characters"
    encrypt_stream(io.BytesIO(b"streamed pg_dump"), destination, secret)
    original = destination.read_bytes()

    with pytest.raises(FileExistsError):
        encrypt_stream(io.BytesIO(b"replacement"), destination, secret)

    assert destination.read_bytes() == original
    assert not (tmp_path / "database.dump.aesgcm.partial").exists()


def test_secret_file_avoids_inline_container_secret(tmp_path, monkeypatch) -> None:
    secret_file = tmp_path / "backup-key"
    secret_file.write_text("file-secret-with-more-than-thirty-two-characters\n")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY_FILE", str(secret_file))
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

    assert _load_secret().strip() == "file-secret-with-more-than-thirty-two-characters"

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "inline-secret")
    with pytest.raises(BackupCryptoError, match="only one"):
        _load_secret()
