from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"AVBK1"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


class BackupCryptoError(ValueError):
    pass


def _derive_key(secret: str) -> bytes:
    normalized = secret.strip()
    if len(normalized) < 32:
        raise BackupCryptoError("BACKUP_ENCRYPTION_KEY must contain at least 32 characters")
    return hashlib.sha256(normalized.encode()).digest()


def _load_secret() -> str:
    secret_file = os.environ.get("BACKUP_ENCRYPTION_KEY_FILE", "").strip()
    inline_secret = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
    if secret_file and inline_secret:
        raise BackupCryptoError(
            "configure only one of BACKUP_ENCRYPTION_KEY_FILE or BACKUP_ENCRYPTION_KEY"
        )
    if secret_file:
        try:
            return Path(secret_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise BackupCryptoError("backup encryption key file cannot be read") from exc
    return inline_secret


def _open_private_file(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def _publish_partial(partial: Path, destination: Path) -> None:
    os.link(partial, destination)
    partial.unlink()
    directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def encrypt_stream(source: BinaryIO, destination: Path, secret: str) -> None:
    nonce = os.urandom(NONCE_SIZE)
    encryptor = Cipher(algorithms.AES(_derive_key(secret)), modes.GCM(nonce)).encryptor()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    partial = destination.with_name(destination.name + ".partial")
    try:
        with _open_private_file(partial) as output_file:
            output_file.write(MAGIC + nonce)
            while chunk := source.read(CHUNK_SIZE):
                output_file.write(encryptor.update(chunk))
            output_file.write(encryptor.finalize())
            output_file.write(encryptor.tag)
            output_file.flush()
            os.fsync(output_file.fileno())
        _publish_partial(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def encrypt_backup(source: Path, destination: Path, secret: str) -> None:
    with source.open("rb") as input_file:
        encrypt_stream(input_file, destination, secret)


def _decrypt_backup(source: Path, secret: str, output_file: BinaryIO | None) -> None:
    size = source.stat().st_size
    minimum_size = len(MAGIC) + NONCE_SIZE + TAG_SIZE
    if size < minimum_size:
        raise BackupCryptoError("backup file is truncated")
    with source.open("rb") as input_file:
        if input_file.read(len(MAGIC)) != MAGIC:
            raise BackupCryptoError("backup file header is invalid")
        nonce = input_file.read(NONCE_SIZE)
        input_file.seek(-TAG_SIZE, os.SEEK_END)
        tag = input_file.read(TAG_SIZE)
        encrypted_size = size - minimum_size
        input_file.seek(len(MAGIC) + NONCE_SIZE)
        decryptor = Cipher(
            algorithms.AES(_derive_key(secret)),
            modes.GCM(nonce, tag),
        ).decryptor()
        remaining = encrypted_size
        while remaining:
            chunk = input_file.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise BackupCryptoError("backup ciphertext is truncated")
            decrypted = decryptor.update(chunk)
            if output_file is not None:
                output_file.write(decrypted)
            remaining -= len(chunk)
        final = decryptor.finalize()
        if output_file is not None:
            output_file.write(final)


def decrypt_backup(source: Path, destination: Path, secret: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    partial = destination.with_name(destination.name + ".partial")
    try:
        with _open_private_file(partial) as output_file:
            _decrypt_backup(source, secret, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        _publish_partial(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def verify_backup(source: Path, secret: str) -> None:
    _decrypt_backup(source, secret, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Encrypt or decrypt an Avatar database backup")
    parser.add_argument("operation", choices=("encrypt", "decrypt", "verify"))
    parser.add_argument("source")
    parser.add_argument("destination", nargs="?", type=Path)
    args = parser.parse_args()
    secret = _load_secret()
    if args.operation == "encrypt":
        if args.destination is None:
            parser.error("encrypt requires a destination")
        if args.source == "-":
            encrypt_stream(sys.stdin.buffer, args.destination, secret)
        else:
            encrypt_backup(Path(args.source), args.destination, secret)
    elif args.operation == "decrypt":
        if args.destination is None or args.source == "-":
            parser.error("decrypt requires source and destination paths")
        decrypt_backup(Path(args.source), args.destination, secret)
    else:
        if args.destination is not None or args.source == "-":
            parser.error("verify requires one encrypted backup path")
        verify_backup(Path(args.source), secret)


if __name__ == "__main__":
    main()
