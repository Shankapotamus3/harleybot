# core/tasks.py
import re, random, logging
from datetime import datetime, timezone
from . import venue, venice
from .state import LocationConstraint

logger = logging.getLogger(__name__)

RISK_LEVELS = {
    1: {"name": "Safe", "description": "Private, no exposure, completely controlled environment"},
    2: {"name": "Low", "description": "Private with minor vulnerability (unlocked door, thin walls)"},
    3: {"name": "Medium", "description": "Semi-private (near windows, balcony, could be heard/seen)"},
    4: {"name": "High", "description": "Semi-public (visible areas, shared spaces, immediate exposure risk)"},
    5: {"name": "Extreme", "description": "Public-adjacent (hallways, stairwells, high exposure possible)"},
}

RISK_WEIGHTS = {1: 5, 2: 5, 3: 20, 4: 30, 5: 40}

GENDER_GUIDELINES = {
    "male": {"body_parts": ["flat chest", "abs", "cock", "balls", "ass"],
             "note": "male — say chest, never breasts"},
    "female": {"body_parts": ["cleavage", "breasts", "nipples", "pussy", "ass"],
               "note": "female — no cock"},
    "trans": {"body_parts": ["chest", "breasts", "cock", "pussy", "ass"],
              "note": "breasts and cock both present"},
    "nonbinary": {"body_parts": ["chest", "breasts", "genitals", "ass"],
                  "note": ""},
}

KINK_CATEGORIES = {
    "kink_exposure": ("Exposure", "Being seen/photographed"),
    "kink_humiliation": ("Humiliation", "Verbal degradation"),
    "kink_degradation": ("Degradation", "Being treated as inferior"),
    "kink_bondage": ("Bondage", "Restraint"),
    "kink_pain": ("Pain", "Impact play"),
    "kink_service": ("Service", "Serving, chores"),
    "kink_edging": ("Edging", "Orgasm denial"),
    "kink_watersports": ("Watersports", "Pee play"),
    "kink_exhibitionism": ("Exhibitionism", "Public exposure"),
    "kink_roleplay": ("Roleplay", "Scenarios"),
    "kink_petplay": ("Pet Play", "Animal roleplay"),
    "kink_feminization": ("Feminization", "Forced fem"),
    "kink_cbt": ("CBT", "Cock/ball torture"),
    "kink_breathplay": ("Breath Play", "Choking"),
    "kink_sensory": ("Sensory", "Blindfolds"),
    "kink_temperature": ("Temperature", "Ice/heat/wax"),
    "kink_marking": ("Marking", "Body writing"),
    "kink_spanking": ("Spanking", "Slapping"),
    "kink_nipple": ("Nipple", "Nipple play"),
    "kink_anal": ("Anal", "Ass play"),
    "kink_gags": ("Gags", "Mouth restriction"),
    "kink_social_media": ("Social Media", "Online exposure"),
}

KINK_LEVELS = {
    "no": {"emoji": "X", "name": "No", "desc": "Hard limit"},
    "okay": {"emoji": "O", "name": "Okay", "desc": "Allowed"},
    "yes": {"emoji": "Y", "name": "Yes", "desc": "Desired"},
}

OUTFIT_OPTIONS = {
    "casual": {"name": "Casual", "male_items": ["t-shirt", "jeans", "boxers"], "female_items": ["t-shirt", "leggings", "panties"]},
    "formal": {"name": "Formal", "male_items": ["button-up", "dress pants", "tie"], "female_items": ["blouse", "skirt", "bra"]},
    "athletic": {"name": "Athletic", "male_items": ["tank", "shorts", "compression"], "female_items": ["sports bra", "shorts", "panties"]},
    "lounge": {"name": "Lounge", "male_items": ["hoodie", "sweatpants", "boxers"], "female_items": ["hoodie", "leggings", "panties"]},
    "minimal": {"name": "Underwear Only", "male_items": ["boxers"], "female_items": ["bra", "panties"]},
    "custom": {"name": "Custom", "male_items": [], "female_items": []},
}

CONTEXT_OPTIONS = {
    "alone": {"name": "Alone", "bonus": 0},
    "partner": {"name": "Partner Home", "bonus": 1},
    "roommates": {"name": "Roommates", "bonus": 1},
    "parents": {"name": "Family", "bonus": 2},
    "kids": {"name": "Kids Present", "bonus": 3},
    "friends": {"name": "Friends Over", "bonus": 1},
    "public": {"name": "Public/Strangers", "bonus": 0},
}

FORBIDDEN_KEYWORDS = {
    "marking": ["write", "marker", "sharpie", "draw on", "body writing", "written", "label"],
    "watersports": ["pee", "piss", "urine", "wet yourself"],
    "breathplay": ["choke", "strangle", "suffocate"],
    "social_media": ["post", "upload", "instagram", "twitter", "facebook", "share online"],
}

FALLBACKS = {
    1: "Strip completely at {loc} and photograph your reflection",
    2: "Strip at {loc} near the unlocked door and take a photo",
    3: "Expose yourself at {loc} near a window and photograph the view",
    4: "Step just outside the {loc} doorway, expose yourself, and take a photo",
    5: "Walk to the end of the {loc} hallway, expose yourself briefly, photograph the empty hall behind you",
}

MAX_FEATURES = 6


def weighted_risk(max_risk=5):
    levels = [l for l in RISK_WEIGHTS if l <= max(1, max_risk)]
    return random.choices(levels, weights=[RISK_WEIGHTS[l] for l in levels], k=1)[0]


def get_user_kinks(state, level=None):
    out = []
    for key in KINK_CATEGORIES:
        val = state.get(key) or "okay"
        if level is None and val in ("yes", "okay"):
            out.append(key.replace("kink_", ""))
        elif level and val == level:
            out.append(key.replace("kink_", ""))
    return out


def reward_threshold(streak):
    return max(3, random.randint(5, 10) - min(3, streak // 5))


def _features(state, location):
    """A short candidate list, not the whole venue dump."""
    notes = (state.get("location_notes") or "").strip()
    if notes:
        return ""
    raw = venue.venue_baseline(state, location)
    items = [i.strip() for i in raw.split(",") if i.strip()]
    random.shuffle(items)
    return ", ".join(items[:MAX_FEATURES])


async def generate_task_text(state, session, *, voice, depth=0):
    gender = state.get("gender") or "nonbinary"
    max_risk = state.get("risk_level") or 5
    location = state.get("location") or "Unknown"
    rooms = state.get("rooms") or []
    others = state.get("context_others") or "alone"
    privacy = state.get("privacy") or "private"
    actual_risk = weighted_risk(max_risk)
    outfit = state.get("outfit_detail") or state.get("current_outfit") or "clothing"

    yes_kinks = get_user_kinks(state, "yes")
    okay_kinks = get_user_kinks(state, "okay")
    forbidden = [k.replace("kink_", "") for k in KINK_CATEGORIES
                 if (state.get(k) or "okay") == "no"]
    if not yes_kinks and not okay_kinks:
        return FALLBACKS[actual_risk].format(loc=location), actual_risk

    selected = []
    if yes_kinks:
        selected.append(random.choice(yes_kinks))
        if len(yes_kinks) > 1 and random.random() < 0.5:
            rest = [k for k in yes_kinks if k not in selected]
            if rest:
                selected.append(random.choice(rest))
    slots = random.randint(1, 3) - len(selected)
    if slots > 0 and okay_kinks:
        avail = [k for k in okay_kinks if k not in selected]
        if avail:
            selected.extend(random.sample(avail, min(slots, len(avail))))

    recent = (state.get("recent_settings") or [])[-8:]
    recent_text = "\n".join(f"- {r}" for r in recent) if recent else "none yet"
    blockers = session.query(LocationConstraint).filter(
        LocationConstraint.user_id == state.user_id,
        LocationConstraint.location == location,
        LocationConstraint.expires_at > datetime.now(timezone.utc)).all()
    blockers_text = "\n".join(f"- {b.constraint}" for b in blockers) if blockers else "none"

    guide = GENDER_GUIDELINES.get(gender, GENDER_GUIDELINES["nonbinary"])
    anatomy = ", ".join(guide["body_parts"])
    anatomy_note = f" ({guide['note']})" if guide["note"] else ""
    notes = (state.get("location_notes") or "").strip()
    features = _features(state, location)
    detail_block = f"\nWhere exactly: {notes}" if notes else ""
    feature_block = f"\nFeatures in reach (choose ONE, do not list them): {features}" if features else ""
    rooms_line = f"\nRooms in play: {', '.join(rooms)}" if rooms else ""

    prompt = f"""Write ONE task for a {gender} sub. Stay in voice, but obey the format exactly.

Location: {location}{rooms_line}{detail_block}{feature_block}
Context: {others}, {privacy} privacy
Risk: {actual_risk}/5 - {RISK_LEVELS[actual_risk]['description']}
Wearing: {outfit}
Anatomy - only these exist{anatomy_note}: {anatomy}
DESIRED: {', '.join(yes_kinks) or 'none'}
USE: {', '.join(selected)}

KNOWN BLOCKERS HERE - do not build around these:
{blockers_text}

RECENTLY USED HERE - prefer variety:
{recent_text}

Work in this order:
1. Silently pick ONE setting from what is listed above.
2. Cross off anything blocked.
3. Check it fits risk {actual_risk}/5.
4. Write the task around that single setting.

Rules:
- Single action, photo proof required
- No time durations
- Never use: {', '.join(forbidden) or 'nothing'}
- Name the chosen setting. Mention ONE supporting detail at most. Do not enumerate other features.
- Never name a body part the sub does not have
- Stay inside the rooms in play
- The sub photographs one-handed. If the action needs both hands, state how to prop the phone.
- Trust the outfit description over the outfit label
- Favour the specific over the generic

Reply exactly:
TASK: <the task, naming the setting>
SETTING: <the setting, 2-5 words>"""

    response = await venice.chat([{"role": "user", "content": prompt}], voice, temperature=0.95)
    if response:
        response = response.strip()
        for kink, words in FORBIDDEN_KEYWORDS.items():
            if depth < 3 and kink in forbidden and any(w in response.lower() for w in words):
                logger.warning(f"forbidden kink '{kink}' leaked, retrying")
                return await generate_task_text(state, session, voice=voice, depth=depth + 1)
        if depth < 3 and gender == "male" and "breast" in response.lower():
            logger.warning("male sub got 'breasts', retrying")
            return await generate_task_text(state, session, voice=voice, depth=depth + 1)
        setting, kept = None, []
        for line in response.splitlines():
            s = line.strip()
            m = re.match(r"SETTING:\s*(.+)", s, re.IGNORECASE)
            if m:
                setting = m.group(1).strip()
                continue
            m = re.match(r"TASK:\s*(.+)", s, re.IGNORECASE)
            kept.append(m.group(1).strip() if m else line)
        response = "\n".join(l for l in kept if l.strip()).strip()
        if setting:
            state["recent_settings"] = (recent + [setting])[-8:]
        if response:
            return response, actual_risk
    return FALLBACKS[actual_risk].format(loc=location), actual_risk