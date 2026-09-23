"""Manually tracked Sea Explorer depth checkpoints.

These marks are deliberately independent of the bot's gameplay progress.
Reaching a depth in a run does not mark a reward as collected.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


# (checkpoint number, required depth in metres, coin reward)
CHECKPOINTS: tuple[tuple[int, int, int], ...] = (
    (2, 300, 15),
    (3, 400, 34),
    (4, 500, 48),
    (5, 600, 472),
    (6, 700, 472),
    (7, 900, 4719),
    (8, 1000, 14157),
    (9, 2000, 94380),
)

_CHECKPOINT_NUMBERS = frozenset(number for number, _, _ in CHECKPOINTS)


class CheckpointStore:
    """Persist user-marked checkpoints in a dedicated JSON file.

    Unknown or malformed saved entries are ignored. A failed save leaves both
    the previous file and the in-memory marks unchanged.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._reached = self._read()

    def _read(self) -> set[int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return set()
        if not isinstance(data, dict) or not isinstance(data.get("reached"), list):
            return set()
        return {
            number
            for number in data["reached"]
            if type(number) is int and number in _CHECKPOINT_NUMBERS
        }

    def _write(self, reached: set[int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"reached": sorted(reached)}, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @property
    def reached_numbers(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self._reached))

    @property
    def completed_count(self) -> int:
        with self._lock:
            return len(self._reached)

    @property
    def next_checkpoint(self) -> tuple[int, int, int] | None:
        with self._lock:
            return next(
                (item for item in CHECKPOINTS if item[0] not in self._reached),
                None,
            )

    def is_reached(self, number: int) -> bool:
        with self._lock:
            return type(number) is int and number in self._reached

    def set_reached(self, number: int, reached: bool) -> None:
        if type(number) is not int or number not in _CHECKPOINT_NUMBERS:
            raise ValueError(f"Unknown checkpoint number: {number!r}")
        if type(reached) is not bool:
            raise TypeError("reached must be a bool")
        with self._lock:
            updated = set(self._reached)
            if reached:
                updated.add(number)
            else:
                updated.discard(number)
            if updated != self._reached:
                self._write(updated)
                self._reached = updated

    def save(self) -> None:
        """Atomically rewrite the current marks, for explicit synchronization."""

        with self._lock:
            self._write(self._reached)
