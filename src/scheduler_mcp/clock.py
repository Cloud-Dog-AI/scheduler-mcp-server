"""Injectable clock for deterministic tick + retry testing (NFR-012).

Every time read in service code should go through ``Clock.now()`` so tests
can freeze time. The default implementation returns canonical UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Clock:
    """Default real-time UTC clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class FixedClock(Clock):
    """A clock that always returns a fixed instant. For tests only."""

    instant: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    def now(self) -> datetime:
        return self.instant


_default_clock: Clock = Clock()


def get_clock() -> Clock:
    return _default_clock


def set_clock(clock: Clock) -> None:
    """Replace the module-level clock. Tests use this via a fixture."""
    global _default_clock
    _default_clock = clock
