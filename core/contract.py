"""What the task is allowed to assume about other people.

Nobody in a public place agreed to anything, so the only safe rule is that the task works
without a word being said to anyone. This module turns the venue profile into a short,
deterministic statement of that, so the prompt can be told it and drafts can be checked
against it.
"""

import re

RANK = {"alone": 0, "caught": 1, "about": 2, "watched": 3}

SHELTER_TEXT = {
    "none": "nothing - open ground, the sightline is theirs",
    "thin": "one thing to get behind, and only if they move",
    "good": "something solid to put their back to",
}

STANDABLE = ("bench", "seat", "chair", "stool", "ledge", "step", "plinth", "lounger",
             "kerb", "wall", "block", "table", "counter", "bed", "deck", "rail")

ASK_PATTERNS = (
    r"\bask (someone|somebody|a (stranger|passer[- ]by|member of staff|staff member))\b",
    r"\bask them to\b",
    r"\bhand (your |the )?phone (to|over)\b",
    r"\b(has|have|get|gets) (someone|somebody|a stranger) (hold|take)\b",
    r"\bsomeone (holds|takes|shoots|frames) (it|the photo)\b",
)

STAND_PATTERNS = (
    r"\bstand(ing)? on\b",
    r"\bclimb(ing)? (up )?on(to)?\b",
    r"\bperch(ed|ing)? on\b",
    r"\bup on (the|a) \w+\b",
)

COVER_WORDS = ("behind", "back to", "back against", "facing the wall", "face the wall",
               "corner", "alcove", "partition", "pillar", "column", "shelving", "shelf",
               "out of the eyeline", "out of sight", "away from")


def derive(privacy, prof):
    """The per-task contract. Deterministic from the profile - no randomness."""
    rank = RANK.get(privacy, 1)
    shelter = str((prof or {}).get("shelter") or "none").strip().lower()
    if shelter not in SHELTER_TEXT:
        shelter = "none"
    people = str((prof or {}).get("bystanders") or "").strip()
    if rank == 0:
        people = people or "nobody - and nobody can walk in"
    elif not people:
        people = ("people about, not looking" if rank == 2
                  else "people about, and looking")

    stand_on = [f for f in ((prof or {}).get("fittings") or [])
                if any(w in f.lower() for w in STANDABLE)]

    return {
        "rank": rank,
        "people": people,
        "staff": bool((prof or {}).get("staff")),
        "shelter": shelter,
        "stand_on": stand_on[:2],
        "may_wait": rank >= 2,
        "may_step_out": rank >= 2,
        "may_stand": bool(stand_on) and not (rank >= 3 and shelter == "none"),
        "max_interactions": 0,
        "must_name_cover": rank >= 2,
        "must_stay_put": rank <= 1,
    }


def block(c):
    """Prompt text. Short, and stated as fact rather than advice."""
    if c["max_interactions"] == 0:
        asked = "nothing asked of anyone - no holding, no taking the photo, no counting"
    else:
        asked = f"one thing, once - {c['max_interactions']} at most"

    lines = [
        f"- Who is about: {c['people']}",
        f"- Staff: {'one, working, not watching you' if c['staff'] else 'none'}",
        f"- Between you and them: {SHELTER_TEXT[c['shelter']]}",
        f"- Asked to help: {asked}",
    ]
    if c["may_wait"]:
        lines.append("- Waiting is allowed, described as an event - until a group turns away")
    if c["may_step_out"]:
        lines.append("- One step out of the flow is allowed. Not fully outside.")
    else:
        lines.append("- Nothing leaves the room")
    if c["may_stand"]:
        lines.append(f"- They may stand on: {', '.join(c['stand_on'])}")
    else:
        lines.append("- Nothing to stand on. Keep them on their feet")
    if c["must_name_cover"]:
        lines.append("- The cover is compulsory and must be named in the task")
    else:
        lines.append("- No cover needed")
    return "\n".join(lines)


def guard_interaction(low):
    for pattern in ASK_PATTERNS:
        if re.search(pattern, low):
            return "asked a bystander"
    return None


def guard_standing(low, c):
    if c["may_stand"]:
        return None
    for pattern in STAND_PATTERNS:
        if re.search(pattern, low):
            return "stood on something"
    return None


def guard_cover(low, c, spot=""):
    if not c["must_name_cover"]:
        return None
    if spot and spot.lower() in low:
        return None
    if any(w in low for w in COVER_WORDS):
        return None
    return "no cover named"