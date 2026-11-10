import json, logging
from . import venue, venice

logger = logging.getLogger(__name__)

PROMPT = """Assess this place for covert exhibitionism. The sub is underdressed and does not want
to be noticed by strangers.

Place: {location}

JSON only:
{{
  "privacy": "alone|caught|about|watched",
  "cover": ["4-6 short spots inside it where nobody would look"],
  "moves": [["one thing that raises exposure", 1-3]],
  "fittings": ["4-6 ordinary things a place like this has"]
}}

privacy = how exposed simply standing there is:
alone = nobody can see you, nobody can walk in
caught = alone for now, someone could appear
about = people around, none watching you
watched = people around and looking

moves: 2-4 entries, the number is the level that thing forces. Only things that cost cover.
Use the place's own furniture. No prose."""

FALLBACK = {"privacy": "caught",
            "cover": ["a corner", "just outside the door"],
            "moves": [["footsteps nearby", 1], ["just outside the door", 2]],
            "fittings": ["door", "corner", "window", "floor"]}


def _clamp(raw, location):
    if not isinstance(raw, dict):
        return dict(FALLBACK, location=location)
    prof = {"location": location}
    prof["privacy"] = venue.normalise_privacy(raw.get("privacy")) or FALLBACK["privacy"]
    prof["cover"] = [str(c)[:40] for c in (raw.get("cover") or [])][:6] or FALLBACK["cover"]
    prof["fittings"] = [str(f)[:40] for f in (raw.get("fittings") or [])][:6] or FALLBACK["fittings"]
    moves = []
    for item in (raw.get("moves") or [])[:4]:
        try:
            move, target = item[0], int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        moves.append([str(move)[:80], max(1, min(3, target))])
    prof["moves"] = moves or FALLBACK["moves"]
    return prof


async def profile(location):
    try:
        raw = await venice.chat([{"role": "user", "content": PROMPT.format(location=location)}],
                                "You classify places. JSON only.", temperature=0.2)
        text = (raw or "").strip()
        start, end = text.find("{"), text.rfind("}")
        return _clamp(json.loads(text[start:end + 1]) if start >= 0 else {}, location)
    except Exception:
        logger.exception("venue profile failed for %s", location)
        return dict(FALLBACK, location=location)