"""A round-robin pool of Gemini API keys that rests a key while Google reports it out of quota.

Keys are identified by position ("key 2") in logs and errors; the key itself is never exposed.
State lives in the process: on a serverless host each instance keeps its own view, and a key
another instance saw exhausted costs this instance one fast 429 before it rests it too.
"""

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(eq=False)
class PooledKey:
    label: str
    client: Any
    available_at: float = 0.0
    reason: str | None = None
    requests: int = 0


class KeyPool:
    def __init__(self, clients: Sequence[Any], *, clock: Callable[[], float] = time.monotonic, start: int | None = None) -> None:
        if not clients:
            raise ValueError("A key pool needs at least one API key.")
        self._keys = [PooledKey(label=f"key {index}", client=client) for index, client in enumerate(clients, start=1)]
        self._clock = clock
        # A random first key, so concurrent cold instances don't all start on key 1.
        self._cursor = random.randrange(len(self._keys)) if start is None else start % len(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def keys(self) -> tuple[PooledKey, ...]:
        return tuple(self._keys)

    def acquire(self) -> PooledKey | None:
        """The next key in rotation that isn't resting, or None when every key is."""
        now = self._clock()
        for offset in range(len(self._keys)):
            index = (self._cursor + offset) % len(self._keys)
            key = self._keys[index]
            if key.available_at <= now:
                key.reason = None
                key.requests += 1
                self._cursor = index + 1
                return key
        return None

    def rest(self, key: PooledKey, seconds: float, reason: str) -> None:
        key.available_at = max(key.available_at, self._clock() + max(seconds, 0.0))
        key.reason = reason

    def available(self) -> int:
        now = self._clock()
        return sum(1 for key in self._keys if key.available_at <= now)

    def next_available_in(self) -> float:
        """Seconds until the first resting key can be used again; 0 if one already can."""
        now = self._clock()
        return max(0.0, min(key.available_at for key in self._keys) - now)

    def resting_reasons(self) -> set[str]:
        now = self._clock()
        return {key.reason for key in self._keys if key.available_at > now and key.reason}
