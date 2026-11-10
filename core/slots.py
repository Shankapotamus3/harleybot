import json
from datetime import datetime, timezone, timedelta
from . import venue, tasks as tk

STALE = {"outfit": timedelta(hours=8), "location": timedelta(hours=6)}
ORDER = ["gender", "location", "outfit", "privacy", "hide_spot"]


def _asked(state):
    raw = state.get("asked")
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def _skipped(state):
    raw = state.get("slot_skipped")
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except (TypeError, ValueError):
        return []


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
    asked = _asked(state).get(key)
    if not asked:
        return False
    try:
        return now - datetime.fromisoformat(asked) > window
    except (TypeError, ValueError):
        return False


def next_gap(state, now=None):
    now = now or datetime.now(timezone.utc)
    skipped = _skipped(state)
    for key in ORDER:
        stale = _stale(state, key, now)
        if _filled(state, key) and not stale:
            continue
        if key in skipped and not stale:
            continue
        return key
    return None


def mark_asked(state, key):
    asked = dict(_asked(state))
    asked[key] = datetime.now(timezone.utc).isoformat()
    state["asked"] = asked


def mark_skipped(state, key):
    skipped = list(_skipped(state))
    if key not in skipped:
        skipped.append(key)
    state["slot_skipped"] = skipped