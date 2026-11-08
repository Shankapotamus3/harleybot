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
    5: {"name": "Brazen", "description": "Everything that needs to be out is out, and held there"},
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

# Lines the model sometimes writes as headings. They are dropped from the body.
BODY_LABELS = ("TASK:", "SETTING:", "EXPOSURE:", "PEOPLE:", "CONTEXT:", "COVER:",
               "RISK:", "PRIVACY:", "LOCATION:", "SKIN:", "MOVE:", "BACKDROP:")

# From this exposure up, the place stops being a scene and becomes a backdrop.
QUICK_AT = 2

# Portable reveals. Above "caught" the task is built from one of these instead of
# from the venue, so nothing has to be found, reached or leaned on.
QUICK_MOVES = {
    1: ["hook a thumb in the waistband and ease it down an inch",
        "lift the hem to the navel and hold it there"],
    2: ["lift the hem to just under the nipple and hold it",
        "ease the waistband down to the top of the arse and hold it"],
    3: ["pull the hem up over one nipple and leave it there",
        "open the fly and ease the cage out through it"],
    4: ["open the fly wide, ease the waistband to mid-thigh, and bring the cage and balls out",
        "turn your back, ease the waistband to mid-thigh, and let the arse read"],
    5: ["open the fly, ease the waistband to mid-thigh, bring the cage out and turn side on so the "
        "cage and a cheek of the arse both read",
        "lift the hem over a nipple, ease the waistband to mid-thigh, and hold both"],
}

# What worn gear physically permits, including how it is reached.
GEAR_PHYSICS = {
    "cage": "worn on the cock - nothing shows until the fly is open and the trousers eased down to "
            "mid-thigh at most, underwear with them. A shirt off reveals nothing. It can be lifted "
            "off, but then it is an object in your hand and no longer worn - say which.",
    "chastity": "same as a cage - fly open, trousers eased to mid-thigh, underwear with them. "
                "Shirt is irrelevant to the shot.",
    "plug": "a plug is inside - it is invisible from the front. It needs either a rear view with "
            "the phone held low behind you, or your back turned to a mirror. It cannot share a "
            "frame with something held out in front.",
    "blindfold": "a blindfolded sub cannot see the phone - give a timer, a count, or a mirror",
    "rope": "roped arms cannot hold the phone",
    "cuffs": "cuffed wrists mean the phone is propped or on a timer",
    "collar": "a collar means a leash is available",
    "heels": "heels change the stance and the balance",
}

# Only used for staged tasks now. Above "caught" the move sets the skin.
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

QUICK_COVER = {
    "about": "get out of the main flow - a dead corner, a wall you can face, the far end of the aisle",
    "watched": "put your back to the main flow somewhere out of the way - a dead corner, a wall, the "
               "far end of the aisle. Brief and accepted beats hidden and impossible",
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


def _tidy(text):
    """Drop heading lines and the template artefacts the model leaves behind."""
    kept = [l for l in text.splitlines()
            if not any(l.strip().upper().startswith(p) for p in BODY_LABELS)]
    text = "\n".join(kept)
    text = re.sub(r"_+", "", text)
    text = re.sub(r"[ \t]*\([ \t]*\)", "", text)
    text = re.sub(r"[ \t]*\)[ \t]*(?=[;,.])", "", text)
    text = re.sub(r"[ \t]+([;,.])", r"\1", text)
    text = re.sub(r";[ \t]*$", ".", text)
    text = re.sub(r"\.(?=[A-Za-z])", ". ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


async def generate_task_text(state, session, *, voice, depth=0):
    gender = state.get("gender") or "nonbinary"
    location = state.get("location") or "Unknown"
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

    quick = venue.exposure_rank(effective) >= QUICK_AT

    # One band per level. Staged tasks get two bands on top because nothing has to
    # stay quick: alone at home, a full strip is the obvious task.
    actual_risk = venue.exposure_rank(effective) + 1 + INTENSITY_SHIFT.get(intensity, 0)
    if not quick:
        actual_risk += 2
    if no_cover:
        actual_risk -= 1
    actual_risk = max(1, min(5, actual_risk))

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

    if quick:
        chosen_move = random.choice(QUICK_MOVES[actual_risk])
        skin_line = f"Move: {chosen_move}"
        phone_line = "in your hand, out of shot - do not mention it"
        cover_beat = QUICK_COVER.get(effective, QUICK_COVER["about"])
        if no_cover:
            cover_beat = "nowhere to hide - turn your back, keep it brief, accept being seen"
        order = """1. Get out of the main flow. Anywhere will do - do not go looking for a particular fixture.
2. Write the task around the Move line and nothing else."""
        kind_rules = """- This is a quick reveal, not a staged scene. Do not name a fixture of the place. The only
  setting detail is how out of the way the spot is.
- Three or four sentences. Shorter than usual.
- Adapt the Move line to what is worn and what the gear allows. Nothing is added to it.
- One hand, phone low and close. No props, no surfaces, nothing to balance on.
- No mirror unless the Move needs the front of the body.
- Trousers go no lower than mid-thigh and nothing comes off. Undressing is what makes a quick
  task feel like getting caught.
- Do not add garments to the Move. If the Move says the fly, the trousers stay up.
- Count the verbs. More than two actions means cut one.
- One wait at most, and only if the Move needs it."""
        setting_prompt = "SETTING: <how out of the way, 2-4 words>"
    else:
        skin_line = f"Skin for risk {actual_risk}: {SKIN_BY_RISK[actual_risk]}"
        phone_line = ("propped - both hands free, say what in three words" if needs_prop
                      else "in your hand, out of shot - do not mention it")
        cover_beat = COVER_BEATS[effective]
        if no_cover:
            cover_beat = ("no cover here - keep it to one item, shoot close to the body, and use a "
                          "wall you can face. Brief and accepted beats hidden and impossible.")
        order = """1. Silently choose one setting inside this place. Nothing is listed for you - pick what a
   place like this would plausibly have and what best supports risk {actual_risk}/5.
2. Avoid anything blocked.
3. Write the task around that one setting."""
        kind_rules = """- You have the run of the place and nobody to dodge. Use two or three of its ordinary
  fittings if the task needs them - a surface, a mirror, a door - and write it as a set piece.
- Assume a place like this has the fittings its kind normally has. You do not need to be told.
- If they do not have the room or the thing, they will say so and you rewrite."""
        setting_prompt = "SETTING: <the setting, 2-5 words>"

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

    prompt = f"""Write ONE task for a {gender} sub. Stay in voice, but obey the format exactly.

Location: {location}
Context: {present}
Privacy: {baseline_privacy} - {venue.PRIVACY_DESC[baseline_privacy]}
{reconcile}This setting changes that: {move_line}
Cover during the task: {effective}
Cover (required): {cover_beat}
Risk: {actual_risk}/5 - {RISK_LEVELS[actual_risk]['description']}
{skin_line}
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
{order}

Rules:
- One action, one photo, photo proof required. At most two things for the sub to physically do,
  counting undressing as one. If you have three, cut one.
- No time durations
- Never use: {', '.join(forbidden) or 'nothing'}
{kind_rules}
- Never name a body part the sub does not have
- Obey the gear physics. Never claim an erection in a cage, never ask a blindfolded sub to frame a shot.
- The task must expose what it photographs. If the subject is under clothing, say what moves and
  how far - never pair "shirt off" with something the shirt does not reveal.
- Underwear is not a separate layer to count. It moves with the garment above it.
- If gear comes off the body, say so, and stop describing it as worn.
- Only hedge about gear that is not already in Wearing. If it is listed, treat it as fact.
- Only garments named in Wearing exist. Do not add a garment, a piece of jewellery or a kind of
  underwear that is not listed. If you need underwear and none is named, say "your underwear".
- The Skin line is a hard cap, not a suggestion. At 1-2 one garment moves and nothing comes off.
  "Strip to nothing but" is only legal at 4-5.
- English only, all of it. Do not drift into another language.
- Open with one short clause about who is there, not about the place being quiet. At "nobody around"
  just start. At the other levels say why you are not being seen.
- At "nobody around", do not invent people. A group, a passer-by or a staff member only belongs if
  they are in the venue description.
- The Context line is the truth about who is there. Never call the place empty, dead, quiet or
  deserted at "a few people around" or "people are looking at you" - name who is about instead.
- When people are looking, someone must actually be looking, listening or talking to the sub. Name
  them and what they are doing.
- Above "caught", nothing is stripped. Reveal, do not undress.
- At "about" and above, the task must name its cover: what is between you and them, or what you are
  angling away from. "Turned away" alone is not cover for a reveal.
- Cover must match the skin. If it cannot, reduce by one band rather than dropping the cover.
- Do not put the sub fully outside. One step is enough.
- Waiting is described as an event, not a duration: "until the group at the counter turns away".
- The phone is the camera, not a prop. Never write it into the task text. It is never in the shot
  unless the task is a mirror, and then the mirror is what gets named.
- If the task is a mirror shot, name the mirror in the first sentence. The phone is in the frame,
  aimed at the glass.
- Never say "both hands". One hand holds the camera; say "your free hand".
- Undress FIRST, then take the photo. Never both at once.
- If the Phone line says propped, both hands are free - do not also tell them to hold something.
- Only use a prop when the thing being photographed is in both hands, or the shot needs distance.
- If you use a prop, say what it is in three words or fewer. No elaborate rigs.
- A mirror is a place, not a prop. A mirror shot is taken standing at the basin, body facing the
  glass. If the shot is in a mirror there is no stall and no closed door.
- If the privacy needs a door between them and people, drop the mirror and shoot in the stall. Never
  write both a mirror and a stall in one task.
- Say what the photo must show, concretely - the thing, and how much of the body is in frame
- Direction must be physically true. Kneeling means looking down at yourself.
- If you name a surface, name a real material: "the tiled floor", "the lane boards".
- Never leave a blank, underscore, bracket or dangling punctuation. If a detail is unknown, invent
  a plausible one and name it.
- Write prose only. No headings, no labels, no bullet points in the body - fold who is there and
  what the cover is into the sentences.
- Say "the photo", never "photos"
- Trust the outfit description over the outfit label
- Favour the specific over the generic

Reply exactly:
TASK: <the task>
{setting_prompt}
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
        final = effective
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