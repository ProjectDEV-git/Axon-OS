"""Spaces management for Axon OS Intent Bar."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AXON_DIR: Path = Path.home() / ".axon"
SPACES_FILE: Path = AXON_DIR / "spaces.json"
_CURRENT_SPACE_FILE: Path = AXON_DIR / "current_space"

_DEFAULT_COLOR: str = "#a78bfa"


@dataclass
class Space:
    """Represents a single Axon OS workspace."""

    id: str
    name: str
    color: str = _DEFAULT_COLOR
    app_ids: list[str] = field(default_factory=list)
    last_active: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Space":
        """Construct a Space from a plain dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            color=data.get("color", _DEFAULT_COLOR),
            app_ids=data.get("app_ids", []),
            last_active=data.get("last_active", time.time()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this Space to a plain dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "app_ids": self.app_ids,
            "last_active": self.last_active,
        }


class SpacesManager:
    """Persist and manage Axon OS spaces on disk."""

    def __init__(self) -> None:
        AXON_DIR.mkdir(parents=True, exist_ok=True)
        self._spaces: dict[str, Space] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load spaces from disk; create a default space if none exist."""
        if SPACES_FILE.exists():
            try:
                raw: list[dict[str, Any]] = json.loads(SPACES_FILE.read_text())
                for item in raw:
                    space = Space.from_dict(item)
                    self._spaces[space.id] = space
            except (json.JSONDecodeError, KeyError):
                pass

        if not self._spaces:
            default = Space(
                id=str(uuid.uuid4()),
                name="My Space",
                color=_DEFAULT_COLOR,
            )
            self._spaces[default.id] = default
            self._save()

    def _save(self) -> None:
        """Persist the current spaces list to disk."""
        data = [s.to_dict() for s in self._spaces.values()]
        SPACES_FILE.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_spaces(self) -> list[Space]:
        """Return all spaces sorted by last_active (most recent first)."""
        return sorted(
            self._spaces.values(), key=lambda s: s.last_active, reverse=True
        )

    def get_current_space(self) -> Space | None:
        """Return the currently active space, or None if unset."""
        if not _CURRENT_SPACE_FILE.exists():
            spaces = self.get_spaces()
            return spaces[0] if spaces else None
        try:
            space_id = _CURRENT_SPACE_FILE.read_text().strip()
            return self._spaces.get(space_id)
        except OSError:
            return None

    def set_current_space(self, space_id: str) -> None:
        """Write *space_id* to the current-space tracking file."""
        _CURRENT_SPACE_FILE.write_text(space_id)
        if space_id in self._spaces:
            self._spaces[space_id].last_active = time.time()
            self._save()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create_space(self, name: str, color: str = _DEFAULT_COLOR) -> Space:
        """Create and persist a new space; return it."""
        space = Space(
            id=str(uuid.uuid4()),
            name=name,
            color=color,
        )
        self._spaces[space.id] = space
        self._save()
        return space

    def update_space(self, space_id: str, **kwargs: Any) -> Space | None:
        """Update arbitrary fields of a space by keyword arguments."""
        space = self._spaces.get(space_id)
        if space is None:
            return None
        for key, value in kwargs.items():
            if hasattr(space, key):
                setattr(space, key, value)
        self._save()
        return space

    def delete_space(self, space_id: str) -> bool:
        """Delete a space by id; returns True on success."""
        if space_id not in self._spaces:
            return False
        del self._spaces[space_id]
        self._save()
        return True

    def add_app_to_space(self, space_id: str, app: str) -> bool:
        """Append *app* to a space's app list if not already present."""
        space = self._spaces.get(space_id)
        if space is None:
            return False
        if app not in space.app_ids:
            space.app_ids.append(app)
            self._save()
        return True
