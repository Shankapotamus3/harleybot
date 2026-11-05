# core/tasks.py
import re, os, random, logging
from datetime import datetime, timezone
from . import venue, venice
from .state import LocationConstraint

logger = logging.getLogger(__name__)

PROP_CHANCE = float(os.getenv("PROP_CHANCE", "0.15"))

RISK_LEVELS = {
    1: {"name": "Comfortable", "description": "Nothing comes off, clothing shifted aside"},
    2: {"name": "Mild", "description": "A hem or waistband moved, one quick moment"},
    3: {"name": "Committed", "description": "Whatever is over the subject, opened properly"},
    4: {"name": "Bold", "description": "That, taken far enough that nothing is in the way of the shot"},
    5: {"name": "Brazen", "description": "Off entirely, or stripped right down - only if the cover carries it"},
}

RISK_WEIGHTS = {1: 5, 2: 5, 3: 20, 4: 30, 5: 40}

INTENSITY_SHIFT = {"gentle": -1, "standard": 0, "harsh": 1}

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

# What worn gear physically permits, including how it is reached.
GEAR_PHYSICS = {
    "cage": "worn on the cock - nothing shows until the trousers are open and pulled below it, "
            "underwear with them. A shirt off reveals nothing.",
    "chastity": "same as a cage - trousers open and down past it, underwear pulled below. "
                "Shirt is irrelevant to the shot.",
    "blindfold": "a blindfolded sub cannot see the phone - give a timer, a count, or a mirror",
    "rope": "roped arms cannot hold the phone",
    "cuffs": "cuffed wrists mean the phone is propped or on a timer",
    "plug": "sitting means the weight is on the plug",
    "collar": "a collar means a leash is available",
    "heels": "heels change the stance and the balance",
}

# How much comes off, by risk. Above "caught" this is only legal if the cover in
# the task can carry it.
SKIN_BY_RISK = {
    1: "the item over the subject shifted aside, nothing off",
    2: "the item over the subject opened, one quick moment",
    3: "the item over the subject opened properly and pulled clear - shirt hem up, or trousers "
       "and underwear down past the hip",
    4: "as above, and taken far enough that there is nothing in the way of the shot",
    5: "that item off entirely, or stripped right down - only if the cover allows it",
}

COVER_BEATS = {
    "alone": "no cover needed - take your time",
    "caught": "keep it quick and low, phone close to the body, have something to hand",
    "about": "turn away from the room or put something solid between you and them - name it",
    "watched": "cover is compulsory: behind something solid, in an alcove, facing the wall, or wait "
               "for the sightline to clear. Name it, and say what you are waiting for",
}


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


def _gear_notes(outfit):
    low = (outfit or "").lower()
    hits = [v for k, v in GEAR_PHYSICS.items() if k in low]
    return " / ".join(hits[:3])


def _claim_exposure(response, effective, baseline):
    """Let the model raise the exposure it wrote. Never lower it."""
    claimed = None
    for line in response.splitlines():
        if line.strip().upper().startswith("EXPOSURE:"):
            claimed = line.split(":", 1)[1].strip().lower().strip(" .")
    final = effective
    claimed = venue.normalise_privacy(claimed)
    if claimed:
        final = venue.EXPOSURE_LADDER[max(venue.exposure_rank(claimed),
                                          venue.exposure_rank(effective))]
    kept = "\n".join(l for l in response.splitlines()
                     if not l.strip().upper().startswith("EXPOSURE:"))
    return kept.strip(), final


def _tidy(text):
    """Strip the template artefacts the model leaves behind."""
    text = re.sub(r"_+", "", text)
    text = re.sub(r"[ \t]*\([ \t]*\)", "", text)
    text = re.sub(r"[ \t]*\)[ \t]*(?=[;,.])", "", text)
    text = re.sub(r"[ \t]+([;,.])", r"\1", text)
    text = re.sub(r";[ \t]*$", ".", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


async def generate_task_text(state, session, *, voice, depth=0):
    gender = state.get("gender") or "nonbinary"
    location = state.get("location") or "Unknown"
    rooms = state.get("rooms") or []
    others = state.get("context_others") or "alone"
    privacy = state.get("privacy") or ""
    intensity = state.get("intensity") or "standard"
    outfit = state.get("outfit_detail") or state.get("current_outfit") or "clothing"
    gear = _gear_notes(outfit)

    blockers = session.query(LocationConstraint).filter(
        LocationConstraint.user_id == state.user_id,
        LocationConstraint.location == location,
        LocationConstraint.expires_at > datetime.now(timezone.utc)).all()
    blockers_text = "\n".join(f"- {b.constraint}" for b in blockers) if blockers else "none"
    no_cover = any(venue.is_no_cover(b.constraint) for b in blockers)

    stated = venue.normalise_privacy(privacy)
    default = venue.privacy_default(location)
    baseline_privacy = stated or default

    # Occupancy comes from the privacy level. context_others only adds children.
    present = venue.PRIVACY_DESC[baseline_privacy]
    if others == "kids":
        present = f"{present}, and children may be about"

    move, target = None, 0
    if random.random() < float(os.getenv("EXPOSURE_CHANCE", "0.35")):
        move, target = venue.pick_exposure(state, baseline_privacy,
                                           [b.constraint for b in blockers])

    effective = venue.effective_privacy(baseline_privacy, target)
    delta = max(0, venue.exposure_rank(effective) - venue.exposure_rank(baseline_privacy))

    actual_risk = venue.exposure_rank(effective) + 1 + INTENSITY_SHIFT.get(intensity, 0)
    if venue.exposure_rank(effective) >= 2:
        actual_risk += 1
    if no_cover:
        actual_risk -= 1
    actual_risk = max(1, min(5, actual_risk))
    if no_cover:
        actual_risk = min(4, actual_risk)

    needs_prop = random.random() < PROP_CHANCE

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

    cover_beat = COVER_BEATS[effective]
    cover_spot = venue.cover_hint(location) if venue.exposure_rank(effective) >= 2 else ""
    if no_cover:
        cover_beat = ("no cover here - keep it to one item, shoot close to the body, and use a wall "
                      "you can face. Brief and accepted beats hidden and impossible.")

    move_line = (f"{move} - you are now {effective} "
                 f"({venue.PRIVACY_DESC[effective].lower()})" if move else "stays where it is")

    reconcile = ""
    if stated and stated != default:
        if venue.exposure_rank(stated) > venue.exposure_rank(default):
            reconcile = (f"Note: they say {venue.PRIVACY_DESC[stated].lower()}, busier than a "
                         f"{location} normally is. Put someone actually about, and looking.\n")
        else:
            reconcile = (f"Note: they say {venue.PRIVACY_DESC[stated].lower()}, but a {location} is "
                         f"normally {venue.PRIVACY_LABELS[default].lower()}. Have the task say why "
                         f"it is quiet - time of day, closed, empty lane - or let someone appear.\n")

    no_cover_line = ("No cover at this location. Write so nobody sees, or let it be brief and seen.\n"
                     if no_cover else "")
    phone_line = ("propped, timed, or on a mirror - say what, in three words" if needs_prop
                  else "in one hand, nothing put down")

    prompt = f"""Write ONE task for a {gender} sub. Stay in voice, but obey the format exactly.

Location: {location}{rooms_line}{detail_block}{feature_block}
Context: {present}
Privacy: {baseline_privacy} - {venue.PRIVACY_DESC[baseline_privacy]}
{reconcile}{no_cover_line}This setting changes that: {move_line}
Cover during the task: {effective}
Cover (required): {cover_beat}{f" - available here: {cover_spot}" if cover_spot else ""}
Risk: {actual_risk}/5 - {RISK_LEVELS[actual_risk]['description']}
Skin for risk {actual_risk}: {SKIN_BY_RISK[actual_risk]}
Phone: {phone_line}
Wearing: {outfit}
Gear physics - obey these: {gear or "nothing special"}
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
- Single action, one photo, photo proof required
- No time durations
- Never use: {', '.join(forbidden) or 'nothing'}
- Name the chosen setting. Mention ONE supporting detail at most. Do not enumerate other features.
- If the best setting is not in the sample, use it anyway as long as the venue supports it
- Use a listed detail only if the task needs it. The sample is a guide, not a menu.
- Never name a body part the sub does not have
- Obey the gear physics. Never claim an erection in a cage, never ask a blindfolded sub to frame a shot.
- The task must expose what it photographs. If the subject is under clothing, say what moves and
  how far - never pair "shirt off" with something the shirt does not reveal.
- Underwear is not a separate layer to count. It moves with the garment above it.
- Only hedge about gear that is not already in Wearing. If it is listed, treat it as fact.
- Stay inside the rooms in play
- Open with one clause grounding the privacy claim: "the place is dead", "it is early", "the door is locked"
- The Context line is the truth about who is there. Never call the place empty, dead, quiet or
  deserted at "a few people around" or "people are looking at you" - name who is about instead.
- When people are looking, someone must actually be looking, listening or talking to the sub. Name
  them and what they are doing.
- Above "caught", the task must name its cover: what is between you and them, or what you are
  waiting for. "Turned away" alone is not cover for a strip.
- Cover must match the skin. If it cannot, reduce the skin by one band rather than dropping the cover.
- If the person is stripped, the cover has to sit between them and every approach, not just the
  one you can see.
- Do not put the sub fully outside. One step is enough.
- Waiting is described as an event, not a duration: "until the group at the counter turns away".
- The phone is in one hand by default. Undress FIRST, then take the photo - do not try to do both.
- Only use a prop when the thing being photographed is in both hands, or the shot needs distance.
- If you use a prop, say what it is in three words or fewer. No elaborate rigs.
- A mirror counts as a prop. Do not reach for one unless the shot needs the front of the body.
- Say what the photo must show, concretely - the thing, and how much of the body is in frame
- Direction must be physically true. Kneeling means looking down at yourself.
- If you name a surface, name a real material: "the tiled floor", "the lane boards".
- Never leave a blank, underscore, bracket or dangling punctuation. If a detail is unknown, invent
  a plausible one and name it.
- Say "the photo", never "photos"
- Trust the outfit description over the outfit label
- Favour the specific over the generic

Reply exactly:
TASK: <the task, naming the setting>
SETTING: <the setting, 2-5 words>
EXPOSURE: <alone|caught|about|watched>"""

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
        response = _tidy(response)
        if setting:
            state["recent_settings"] = (recent + [setting])[-8:]
        if response:
            return (response, actual_risk,
                    max(0, venue.exposure_rank(final) - venue.exposure_rank(baseline_privacy)),
                    final)
    return (FALLBACKS[actual_risk].format(loc=location), actual_risk, delta, effective)