"""Where runs keep their files: the uploads as they arrived, the report, the result.

Everything lives under ``settings.run_storage_dir``. The database records paths
relative to that folder, so the folder can move (a new volume, a restore from
backup) without rewriting a single row.
"""

import hashlib
from pathlib import Path

from app.core.config import settings

CHUNK_SIZE = 1024 * 1024


def storage_root() -> Path:
    return settings.run_storage_dir


def run_dir(run_id: int) -> Path:
    return storage_root() / "runs" / f"{run_id:06d}"


def relative(path: Path) -> str:
    return path.relative_to(storage_root()).as_posix()


def resolve(stored: str) -> Path:
    """The file a database row points at. Refuses anything outside the store."""
    root = storage_root().resolve()
    path = (root / stored).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{stored!r} is outside the run store")
    return path


def copy_with_digest(source: Path, target: Path) -> tuple[int, str]:
    """Copy a file and return its size and sha256, reading it once."""
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as src, target.open("wb") as dst:
        while chunk := src.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
            dst.write(chunk)
    return size, digest.hexdigest()


def file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()
