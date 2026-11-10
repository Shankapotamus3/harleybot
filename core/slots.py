from datetime import datetime, timezone, timedelta
from . import venue, tasks as tk

STALE = {"outfit": timedelta(hours=8), "location": timedelta(hours=6),
         "hide_spot": timedelta(hours=24)}
ORDER = ["gender", "location", "outfit", "privacy", "hide_spot"]


def _filled(state, key):
    if key == "gender":
        return bool(state.get("gender"))
    if key == "location":
        return bool(state.get("location"))
    if key == "outfit":
        return tk.worn_line(state) != "clothing"
    if key == "privacy":
        return bool(venue.normalise_privacy(state.get("privacy")))
    if key == "hide_spot":
        return bool(state.get("hide_spot"))
    return True


def _stale(state, key, now):
    window = STALE.get(key)
    if not window:
        return False
    asked = (state.get("asked") or {}).get(key)
    if not asked:
        return False
    try:
        return now - datetime.fromisoformat(asked) > window
    except ValueError:
        return False


def next_gap(state, now=None):
    now = now or datetime.now(timezone.utc)
    skipped = state.get("slot_skipped") or []
    for key in ORDER:
        stale = _stale(state, key, now)
        if _filled(state, key) and not stale:
            continue
        if key in skipped and not stale:
            continue
        return key
    return None


def mark_asked(state, key):
    state.setdefault("asked", {})[key] = datetime.now(timezone.utc).isoformat()


def mark_skipped(state, key):
    skipped = state.setdefault("slot_skipped", [])
    if key not in skipped:
        skipped.append(key)