# core/tasks.py
import re, os, random, logging
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


def reward_threshold(streak, exposure=0):
    return max(3, random.randint(5, 10) - min(3, streak // 5) - min(2, exposure))


def _features(state, location):
    """A short shuffled sample of what a place like this has. Never the full list."""
    return venue.venue_typical_features(state, location)


def _claim_exposure(response, effective, baseline):
    """Let the model raise the exposure it wrote. Never lower it."""
    claimed = None
    for line in response.splitlines():
        if line.strip().upper().startswith("EXPOSURE:"):
            claimed = line.split(":", 1)[1].strip().lower().strip(" .")
    final = effective
    if claimed in venue.EXPOSURE_LADDER:
        final = venue.EXPOSURE_LADDER[max(venue.exposure_rank(claimed),
                                          venue.exposure_rank(effective))]
    kept = "\n".join(l for l in response.splitlines()
                     if not l.strip().upper().startswith("EXPOSURE:"))
    return kept.strip(), final


async def generate_task_text(state, session, *, voice, depth=0):
    gender = state.get("gender") or "nonbinary"
    max_risk = state.get("risk_level") or 5
    location = state.get("location") or "Unknown"
    rooms = state.get("rooms") or []
    others = state.get("context_others") or "alone"
    privacy = state.get("privacy") or "private"
    actual_risk = weighted_risk(max_risk)
    outfit = state.get("outfit_detail") or state.get("current_outfit") or "clothing"

    blockers = session.query(LocationConstraint).filter(
        LocationConstraint.user_id == state.user_id,
        LocationConstraint.location == location,
        LocationConstraint.expires_at > datetime.now(timezone.utc)).all()
    blockers_text = "\n".join(f"- {b.constraint}" for b in blockers) if blockers else "none"

    baseline_privacy = privacy
    move, delta = None, 0
    if (venue.exposure_rank(baseline_privacy) < 2
            and random.random() < float(os.getenv("EXPOSURE_CHANCE", "0.35"))):
        move, delta = venue.pick_exposure(state, [b.constraint for b in blockers])

    effective = venue.effective_privacy(baseline_privacy, delta)
    actual_risk = min(5, actual_risk + delta)
    if venue.exposure_rank(effective) >= 2:
        actual_risk = max(actual_risk, 2)

    yes_kinks = get_user_kinks(state, "yes")
    okay_kinks = get_user_kinks(state, "okay")
    forbidden = [k.replace("kink_", "") for k in KINK_CATEGORIES
                 if (state.get(k) or "okay") == "no"]
    if not yes_kinks and not okay_kinks:
        return (FALLBACKS[actual_risk].format(loc=location), actual_risk, delta, effective)

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

    guide = GENDER_GUIDELINES.get(gender, GENDER_GUIDELINES["nonbinary"])
    anatomy = ", ".join(guide["body_parts"])
    anatomy_note = f" ({guide['note']})" if guide["note"] else ""

    notes = venue.notes_for_prompt(state, location)
    features = _features(state, location)
    detail_block = (f"\nWhat she says is there - a sample of what is definitely available, "
                    f"not a checklist: {notes}") if notes else ""
    feature_block = (f"\nTypical of a place like this, some of which she has not mentioned: "
                     f"{features}") if features else ""
    rooms_line = f"\nRooms in play: {', '.join(rooms)}" if rooms else ""
    move_line = f"{move} (+{delta} exposure)" if move else "stays where it is"
    ceiling = (" - past your usual ceiling, and worth it"
               if delta and actual_risk > max_risk else "")

    prompt = f"""Write ONE task for a {gender} sub. Stay in voice, but obey the format exactly.

Location: {location}{rooms_line}{detail_block}{feature_block}
Context: {others}, {privacy} privacy
Baseline privacy: {baseline_privacy}
This one steps out: {move_line}
Effective exposure if you use it: {effective}
Risk: {actual_risk}/5 - {RISK_LEVELS[actual_risk]['description']}{ceiling}
Wearing: {outfit}
Anatomy - only these exist{anatomy_note}: {anatomy}
DESIRED: {', '.join(yes_kinks) or 'none'}
USE: {', '.join(selected)}

KNOWN BLOCKERS HERE - do not build around these:
{blockers_text}

RECENTLY USED HERE - prefer variety:
{recent_text}

Work in this order:
1. Silently list 8 settings. Draw on the sample, the typical features, and one or two things
   a place like this would plausibly have that nobody mentioned.
2. Cross off anything blocked.
3. Choose the setting that best supports risk {actual_risk}/5, preferring a room used least recently.
4. Write the task around that single setting.

Rules:
- Single action, photo proof required
- No time durations
- Never use: {', '.join(forbidden) or 'nothing'}
- Name the chosen setting. Mention ONE supporting detail at most. Do not enumerate other features.
- If the best setting is not in the sample, use it anyway as long as the venue supports it
- Use a listed detail only if the task needs it. The sample is a guide, not a menu.
- Never name a body part the sub does not have
- Stay inside the rooms in play
- If a way out is offered, the task happens at the threshold - one step past the door,
  one foot on the balcony. Name the exit and what is on the other side of it.
- Do not put the sub fully outside. One step is enough.
- The sub photographs one-handed. If the action needs both hands, state how to prop the phone.
- Trust the outfit description over the outfit label
- Favour the specific over the generic

Reply exactly:
TASK: <the task, naming the setting>
SETTING: <the setting, 2-5 words>
EXPOSURE: <private|semi|exposed|public>"""

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
        response, final = _claim_exposure(response, effective, baseline_privacy)
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
            return (response, actual_risk,
                    max(0, venue.exposure_rank(final) - venue.exposure_rank(baseline_privacy)),
                    final)
    return (FALLBACKS[actual_risk].format(loc=location), actual_risk, delta, effective)