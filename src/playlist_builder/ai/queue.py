"""Applied album-plan store (no inbox/outbox queue)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AiQueuePaths:
    root: Path

    @property
    def applied_dir(self) -> Path:
        return self.root / "applied"

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive"


class AiQueue:
    """Filesystem helper for applied album plans under data/ai/applied."""

    def __init__(self, paths: AiQueuePaths) -> None:
        self._paths = paths

    @classmethod
    def from_env(cls, *, root: Path | None = None) -> "AiQueue":
        import os

        from playlist_builder.persistence.env import project_root

        default = project_root() / "data" / "ai"
        queue_root = Path(root or os.environ.get("PLAYLIST_AI_QUEUE_DIR", default))
        return cls(AiQueuePaths(root=queue_root))

    def ensure_dirs(self) -> None:
        self._paths.root.mkdir(parents=True, exist_ok=True)
        self._paths.applied_dir.mkdir(parents=True, exist_ok=True)
        self._paths.archive_dir.mkdir(parents=True, exist_ok=True)
