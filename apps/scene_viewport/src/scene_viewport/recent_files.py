"""Most recently used scenes and model files, kept beside the settings."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from scene_viewport.settings import SETTINGS_PATH

_log = logging.getLogger(__name__)

#: Default location, next to the settings file.
RECENT_PATH = SETTINGS_PATH.with_name("scene_viewport_recent.json")
#: How many entries File → Open Recent keeps.
MAX_RECENT = 10


class RecentFiles:
    """An ordered, de-duplicated list of recently used files, newest first.

    Entries are absolute paths of scene files and model files the user
    opened, saved, or imported. The list is persisted as a JSON array.

    Args:
        paths: Initial entries, newest first. Duplicates are dropped.
        limit: Maximum entries kept.
    """

    def __init__(self, paths: Iterable[Path] = (), *, limit: int = MAX_RECENT) -> None:
        self._limit = max(1, limit)
        self._paths: list[Path] = []
        for path in paths:
            self.add(path, _front=False)

    @property
    def paths(self) -> list[Path]:
        """Entries, newest first."""
        return list(self._paths)

    def add(self, path: Path, *, _front: bool = True) -> None:
        """Record ``path`` as the most recent entry.

        Args:
            path: File that was opened, saved, or imported. Stored absolute.
        """
        resolved = Path(path).expanduser().absolute()
        self._paths = [item for item in self._paths if item != resolved]
        if _front:
            self._paths.insert(0, resolved)
        else:
            self._paths.append(resolved)
        del self._paths[self._limit :]

    def remove(self, path: Path) -> None:
        """Forget ``path``, for example after it failed to open."""
        resolved = Path(path).expanduser().absolute()
        self._paths = [item for item in self._paths if item != resolved]

    def clear(self) -> None:
        """Forget every entry."""
        self._paths.clear()

    @classmethod
    def load(cls, path: Path | None = None) -> RecentFiles:
        """Read the list from disk; a missing or invalid file gives an empty list.

        Args:
            path: JSON file. Defaults to :data:`RECENT_PATH`.
        """
        target = path or RECENT_PATH
        if not target.is_file():
            return cls()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _log.warning("ignoring unreadable recent files list %s", target)
            return cls()
        if not isinstance(payload, list):
            return cls()
        return cls(Path(item) for item in payload if isinstance(item, str) and item)

    def save(self, path: Path | None = None) -> Path:
        """Write the list to disk.

        Args:
            path: JSON file. Defaults to :data:`RECENT_PATH`.

        Returns:
            The path written.
        """
        target = path or RECENT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps([str(item) for item in self._paths], indent=2),
            encoding="utf-8",
        )
        return target
