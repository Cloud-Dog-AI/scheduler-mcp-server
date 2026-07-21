"""Trigger engine — W28K-1413.

Computes the next fire instant for cron / interval / one_shot / manual triggers.
Pure functions; no I/O. Time always comes via scheduler_mcp.clock so tests
freeze it (NFR-012). Service callers persist the returned next_fire_at onto
the Schedule row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter  # type: ignore[import-untyped]

from scheduler_mcp.clock import get_clock


class TriggerSpecError(ValueError):
    """The trigger_spec JSON is malformed for its trigger_type."""


def _resolve_zone(timezone_name: str | None) -> ZoneInfo | None:
    """Return a ZoneInfo for ``timezone_name`` or None for UTC/empty.

    W28K-1409 F-1409-9 — cron next-fire is computed in the schedule's IANA
    timezone so DST transitions are honoured, then converted to UTC for
    storage. ``UTC``/empty short-circuits to None (legacy UTC-only path).
    """
    if not timezone_name or timezone_name.upper() == "UTC":
        return None
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise TriggerSpecError(f"invalid timezone: {timezone_name!r}") from e


@dataclass(frozen=True)
class TriggerEvaluation:
    next_fire_at: datetime | None
    is_due: bool


def compute_next_fire(
    trigger_type: str,
    trigger_spec: dict,
    *,
    from_time: datetime | None = None,
    last_fire_at: datetime | None = None,
    timezone_name: str | None = None,
) -> datetime | None:
    """Return the next fire instant (UTC) strictly AFTER `from_time` (or now()).

    `cron`: trigger_spec={"cron": "*/5 * * * *"}
    `interval`: trigger_spec={"every_seconds": 60, "start_at": "ISO8601?"}
    `one_shot`: trigger_spec={"fire_at": "ISO8601"} — returns the instant only
                until last_fire_at has passed it; then None.
    `condition_watch`: polling trigger using trigger_spec.every_seconds,
                poll_seconds, or interval_seconds.
    `manual`: no scheduler-driven next_fire — returns None.

    `timezone_name` (W28K-1409 F-1409-9): IANA zone (e.g. ``America/New_York``)
    for cron evaluation. Cron expressions are wall-clock; when a zone is given
    the next fire is computed in that zone (so a "30 2 * * *" daily job fires at
    02:30 *local*, surviving DST) then converted to UTC. **DST disambiguation:**
    ambiguous fall-back wall times resolve to the earlier instant (zoneinfo
    fold=0 / pre-transition offset); non-existent spring-forward wall times are
    emitted with the post-transition offset (the wall value is preserved). UTC
    or empty keeps the legacy UTC-only behaviour. Interval/one_shot are absolute
    instants and are unaffected by the zone.
    """
    base = from_time or get_clock().now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    if trigger_type == "cron":
        expr = trigger_spec.get("cron")
        if not expr:
            raise TriggerSpecError("cron trigger requires trigger_spec.cron")
        zone = _resolve_zone(timezone_name)
        try:
            cron_base = base.astimezone(zone) if zone is not None else base
            it = croniter(expr, cron_base)
            return it.get_next(datetime).astimezone(timezone.utc)
        except (ValueError, KeyError) as e:
            raise TriggerSpecError(f"invalid cron expression: {expr!r}") from e

    if trigger_type == "interval":
        every = trigger_spec.get("every_seconds")
        if not every or int(every) <= 0:
            raise TriggerSpecError("interval trigger requires trigger_spec.every_seconds > 0")
        every_s = int(every)
        anchor_raw = trigger_spec.get("start_at")
        if anchor_raw:
            try:
                anchor = datetime.fromisoformat(str(anchor_raw).replace("Z", "+00:00"))
                if anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise TriggerSpecError(f"invalid start_at: {anchor_raw!r}") from e
        else:
            anchor = last_fire_at or base
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor > base:
            return anchor
        # smallest k such that anchor + k*every > base
        delta_s = (base - anchor).total_seconds()
        k = int(delta_s // every_s) + 1
        return anchor + timedelta(seconds=k * every_s)

    if trigger_type == "one_shot":
        raw = trigger_spec.get("fire_at")
        if not raw:
            raise TriggerSpecError("one_shot trigger requires trigger_spec.fire_at")
        try:
            fire_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise TriggerSpecError(f"invalid fire_at: {raw!r}") from e
        if last_fire_at and last_fire_at >= fire_at:
            return None
        return fire_at if fire_at > base else None

    if trigger_type == "condition_watch":
        every = (
            trigger_spec.get("every_seconds")
            or trigger_spec.get("poll_seconds")
            or trigger_spec.get("interval_seconds")
        )
        if not every or int(every) <= 0:
            raise TriggerSpecError("condition_watch trigger requires trigger_spec.every_seconds > 0")
        every_s = int(every)
        anchor_raw = trigger_spec.get("start_at")
        if anchor_raw:
            try:
                anchor = datetime.fromisoformat(str(anchor_raw).replace("Z", "+00:00"))
                if anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise TriggerSpecError(f"invalid start_at: {anchor_raw!r}") from e
        else:
            anchor = last_fire_at or base
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor > base:
            return anchor
        delta_s = (base - anchor).total_seconds()
        k = int(delta_s // every_s) + 1
        return anchor + timedelta(seconds=k * every_s)

    if trigger_type == "manual":
        return None

    raise TriggerSpecError(f"unknown trigger_type: {trigger_type!r}")


def evaluate(
    trigger_type: str,
    trigger_spec: dict,
    *,
    now: datetime | None = None,
    last_fire_at: datetime | None = None,
    timezone_name: str | None = None,
) -> TriggerEvaluation:
    """Combined `next_fire_at` + `is_due` evaluation. `is_due` is True iff a
    fire instant exists at or before `now`.
    """
    base = now or get_clock().now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    nxt = compute_next_fire(
        trigger_type,
        trigger_spec,
        from_time=base,
        last_fire_at=last_fire_at,
        timezone_name=timezone_name,
    )
    is_due = nxt is not None and nxt <= base
    return TriggerEvaluation(next_fire_at=nxt, is_due=is_due)
