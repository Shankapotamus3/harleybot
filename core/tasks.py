import re, os, random, logging
from datetime import datetime, timezone
from . import venue, venice, venue_ai, contract

logger = logging.getLogger(__name__)

PROP_CHANCE = float(os.getenv("PROP_CHANCE", "0.10"))

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

KINK_TELLS = {
    "exposure": ("seen", "watching", "looking", "audience", "spotted", "noticed"),
    "humiliation": ("embarrass", "humiliat", "ashamed", "silly", "fool", "ridiculous"),
    "degradation": ("degrad", "cheap", "slut", "whore", "object", "beg", "lesser"),
    "bondage": ("tie", "rope", "bind", "cuff", "restrain", "knot"),
    "pain": ("ache", "sting", "bite", "throb", "hurt", "burn"),
    "service": ("serve", "fetch", "carry", "chore", "hold it for", "wait on"),
    "edging": ("edge", "don't come", "denial", "stop before", "hold it back"),
    "watersports": ("piss", "pee", "wet yourself"),
    "exhibitionism": ("exposed", "out in the open", "anyone could", "in public"),
    "roleplay": ("pretend", "imagine you're", "play the part", "act like"),
    "petplay": ("pet", "puppy", "kitten", "collar", "on all fours", "paw"),
    "feminization": ("panties", "skirt", "feminine", "sissy", "bra", "stockings"),
    "cbt": ("squeeze", "pull", "weight", "slap your balls", "balls"),
    "breathplay": ("breath", "choke", "hold your breath"),
    "sensory": ("blindfold", "can't see", "in the dark"),
    "temperature": ("cold", "ice", "wax", "heat", "chilled"),
    "marking": ("write", "marker", "written on", "scribble"),
    "spanking": ("slap", "spank", "smack", "strike"),
    "nipple": ("nipple", "pinch", "clamp"),
    "anal": ("plug", "inside you", "arse", "ass"),
    "gags": ("gag", "mouth full", "can't speak", "muffled"),
    "social_media": ("post", "upload", "share online", "caption"),
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
    1: "Lift your shirt and hold it under your chest so the underside shows, hip cocked, and say what "
       "you want done to you",
    2: "Ease the waistband down and hold it there, and describe the one thing you'd beg for",
    3: "Get it out and hold it clear, and say out loud what you'd let someone do to it",
    4: "Out and held, turned enough to read, and name the thing you'd let them do without asking",
    5: "Everything out and held up to the lens, and a line about what you'd do if someone walked in",
}

MAX_FEATURES = 6

BODY_LABELS = ("TASK:", "SETTING:", "EXPOSURE:", "PEOPLE:", "CONTEXT:", "COVER:",
               "RISK:", "PRIVACY:", "LOCATION:", "SKIN:", "MOVE:", "BACKDROP:",
               "ONLY:", "NOTE:", "STEP:", "RULES:")

QUICK_AT = 2

LEGWEAR_WORDS = ("trouser", "jean", "shorts", "jogger", "sweatpant", "legging",
                 "skirt", "kilt", "cargo", "chino", "tights")

LEGWEAR_TELLS = ("trouser", "fly", "zip", "pocket", "waistband of your trousers")

OUT_WORDS = ("lobby", "out into", "out onto", "outside", "car park", "pavement",
             "steps", "street", "concourse", "foyer", "reception", "stairwell")

NON_ENGLISH = ("continuar", "continuer", "verdader", "falso", "eikä", "sittemmin",
               "jätti", "perillisi", "apartamentuak", "och", "att", "med", "för",
               "är", "nous", "vous", "avec", "pour", "dans", "pero", "porque",
               "este", "esta", "muy", "mucho", "gebehen")

ANATOMY_WORDS = ("cock", "balls", "arse", "ass", "chest", "breast", "nipple", "pussy",
                 "genitals", "thigh", "hip", "bare", "naked", "nude", "strip")

MOVES = {
    1: ["hook a thumb in the waistband and ease it down an inch",
        "lift the hem to the navel and hold it there",
        "drag a fingertip down your own sternum and leave it low"],
    2: ["lift the hem to just under the nipple and hold it",
        "ease the waistband down to the top of the arse and hold it",
        "cup yourself through the fabric and squeeze once"],
    3: ["pull the hem up over one nipple and leave it there",
        "ease the waistband down and bring the cage out over it",
        "get a hand inside and work yourself slowly"],
    4: ["ease the waistband down to mid-thigh and bring the cage and balls out",
        "turn your back, ease the waistband to mid-thigh, and let the arse read",
        "get it out and work it, stopping just short"],
    5: ["ease the waistband to mid-thigh, bring the cage out and turn side on so the "
        "cage and a cheek of the arse both read",
        "lift the hem over a nipple, ease the waistband to mid-thigh, and hold both",
        "everything out, working yourself with the other hand, held at the edge"],
}

GEAR_PHYSICS = {
    "cage": "worn on the cock and invisible until whatever is over it moves. Over trousers: fly "
            "open, waistband to mid-thigh, underwear with it. Over a thong or nothing: the waistband "
            "down or aside is the whole job. A shirt off reveals nothing. It can be lifted off, but "
            "then it is an object in your hand and no longer worn - say which.",
    "chastity": "same as a cage - whatever is over it moves, waistband to mid-thigh at most. "
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

SKIN_BY_RISK = {
    1: "the item over the subject shifted aside, nothing off",
    2: "the item over the subject opened, one quick moment",
    3: "the item over the subject opened properly and pulled clear - hem up, or waistband down "
       "past the hip",
    4: "as above, and taken far enough that there is nothing in the way of the shot",
    5: "that item off entirely, or stripped right down - only if the cover allows it",
}

COVER_BEATS = {
    "alone": "no cover needed - take your time",
    "caught": "keep it low and finish before anyone comes",
    "about": "people are around and not looking - get behind {spot} and name it",
    "watched": "people are looking - get out of the eyeline, behind {spot}, and name it",
}

QUICK_COVER = {
    "about": "get out of the flow - {spot}",
    "watched": "put your back to the flow with {spot} behind you. Unnoticed beats seen and accepted",
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


def _pick_move(risk, state):
    recent = state.get("recent_moves") or []
    pool = [m for m in MOVES[risk] if m not in recent]
    move = random.choice(pool or MOVES[risk])
    state["recent_moves"] = (recent + [move])[-6:]
    return move


def _gear_notes(outfit):
    low = (outfit or "").lower()
    hits = []
    for k, v in GEAR_PHYSICS.items():
        if k not in low:
            continue
        if re.search(rf"\b(no|without|not wearing|off)\s+(the\s+|a\s+|my\s+)?{k}\b", low):
            continue
        hits.append(v)
    return " / ".join(hits[:3])


def _has_legwear(outfit):
    low = (outfit or "").lower()
    return any(w in low for w in LEGWEAR_WORDS)


def _relocates(move):
    return bool(move) and any(w in move.lower() for w in OUT_WORDS)


def worn_line(state):
    return (state.get("outfit_detail")
            or ", ".join(state.get("outfit_items") or [])
            or state.get("current_outfit") or "clothing")


def privacy_default(state):
    prof = state.get("venue_profile") or {}
    return (venue.normalise_privacy(prof.get("privacy"))
            or venue.privacy_default(state.get("location") or ""))


async def venue_profile(state, location):
    cached = state.get("venue_profile")
    if cached and cached.get("location") == location:
        return cached
    prof = await venue_ai.profile(location)
    state["venue_profile"] = prof
    return prof


def _tidy(text):
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


async def generate_task_text(state, session, *, voice, depth=0, model=None):
    from .state import LocationConstraint

    gender = state.get("gender") or "nonbinary"
    location = state.get("location") or "Unknown"
    others = state.get("context_others") or "alone"
    privacy = state.get("privacy") or ""
    intensity = state.get("intensity") or "standard"
    outfit = worn_line(state)
    gear = _gear_notes(outfit)
    legwear = _has_legwear(outfit)

    prof = await venue_profile(state, location)
    default = privacy_default(state)
    spot = (state.get("hide_spot") or "").strip() or random.choice(
        prof.get("cover") or ["the doorway"])

    blockers = session.query(LocationConstraint).filter(
        LocationConstraint.user_id == state.user_id,
        LocationConstraint.location == location,
        LocationConstraint.expires_at > datetime.now(timezone.utc)).all()
    blockers_text = "\n".join(f"- {b.constraint}" for b in blockers) if blockers else "none"
    no_cover = any(venue.is_no_cover(b.constraint) for b in blockers)

    stated = venue.normalise_privacy(privacy)
    baseline_privacy = stated or default

    present = venue.PRIVACY_DESC[baseline_privacy]
    if others == "kids":
        present = f"{present}, and children may be about"

    move, target = None, 0
    if random.random() < float(os.getenv("EXPOSURE_CHANCE", "0.35")):
        move, target = venue.pick_exposure(baseline_privacy, prof["moves"],
                                           [b.constraint for b in blockers])

    effective = venue.effective_privacy(baseline_privacy, target)
    delta = max(0, venue.exposure_rank(effective) - venue.exposure_rank(baseline_privacy))

    quick = venue.exposure_rank(effective) >= QUICK_AT
    who = contract.derive(effective, prof)

    actual_risk = venue.exposure_rank(effective) + 1 + INTENSITY_SHIFT.get(intensity, 0)
    if not quick:
        actual_risk += 2
    if no_cover:
        actual_risk -= 1
    actual_risk = max(1, min(5, actual_risk))

    needs_prop = (not quick) and random.random() < PROP_CHANCE

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
    slots_n = random.randint(1, 3) - len(selected)
    if slots_n > 0 and okay_kinks:
        avail = [k for k in okay_kinks if k not in selected]
        if avail:
            selected.extend(random.sample(avail, min(slots_n, len(avail))))

    tells = [w for k in selected for w in KINK_TELLS.get(k, ())]

    recent = (state.get("recent_settings") or [])[-8:]
    recent_text = "\n".join(f"- {r}" for r in recent) if recent else "none yet"

    guide = GENDER_GUIDELINES.get(gender, GENDER_GUIDELINES["nonbinary"])
    anatomy = ", ".join(guide["body_parts"])
    anatomy_note = f" ({guide['note']})" if guide["note"] else ""

    lower = "" if legwear else ("No legwear is worn - no fly, no zip, no pocket, nothing to push "
                                "down. The waistband alone is what moves.\n")
    travel = ""
    if not legwear and _relocates(move):
        travel = (f"They are in {outfit} and cannot walk out like that. They may pull on ONE minimal "
                  f"item for the walk and take it off at the spot - name the item, say it comes off. "
                  f"Do not dress them fully.")
    travel_line = f"Travel: {travel}\n" if travel else ""
    underdressed = ""
    if not legwear and venue.exposure_rank(default) >= 2:
        underdressed = (f"They are in {outfit} in a {location} - wildly underdressed for it. Say so "
                        f"once, then make the concealment the point: nobody expects this here, so the "
                        f"only way it works is not being spotted.\n")

    chosen_move = _pick_move(actual_risk, state)

    if quick:
        skin_line = f"Move: {chosen_move}"
        phone_line = "in your hand, out of shot - do not mention it"
        cover_beat = QUICK_COVER.get(effective, QUICK_COVER["about"]).format(spot=spot)
        if no_cover:
            cover_beat = "nowhere to hide - turn your back, keep it brief, accept being seen"
        order = """1. Get out of the main flow. Anywhere will do - do not go looking for a particular fixture.
2. Write the task around the Move line and nothing else."""
        kind_rules = """- This is a quick reveal, not a staged scene. Do not name a fixture of the place. The only
  setting detail is how out of the way the spot is.
- Three sentences. Not four.
- Adapt the Move line to what is worn and what the gear allows. Nothing is added to it.
- One hand, phone low and close. No props, no surfaces, nothing to balance on.
- No mirror unless the Move needs the front of the body.
- One item may come off, not a full strip. If the Move works with everything still on, leave it on.
- A travel garment put on for the walk comes off again at the spot.
- Do not add garments to the Move, beyond a travel garment.
- Count the verbs. More than two actions means cut one.
- One wait at most, and only if the Move needs it."""
        setting_prompt = "SETTING: <how out of the way, 2-4 words>"
    else:
        skin_line = (f"Move: {chosen_move}\n"
                     f"Skin cap for risk {actual_risk}: {SKIN_BY_RISK[actual_risk]}")
        phone_line = ("propped - both hands free, say what in three words" if needs_prop
                      else "in your hand, out of shot - do not mention it")
        cover_beat = COVER_BEATS[effective].format(spot=spot)
        if no_cover:
            cover_beat = ("no cover here - keep it to one item, shoot close to the body, and use a "
                          "wall you can face. Brief and accepted beats hidden and impossible.")
        order = """1. Pick one fitting inside this place that the Move can be done against.
2. Avoid anything blocked.
3. Write the set piece around the Move."""
        kind_rules = """- Build the set piece around the Move line. The Move is the body action; the setting is only
  where it happens. Do not invent a different action.
- You have the run of the place for the setting and nobody to dodge. Name one fitting the Move can be
  done against and write it as a scene.
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

    fittings_text = ", ".join(prof["fittings"])
    desired_text = ", ".join(yes_kinks) or "none"
    use_text = ", ".join(selected)
    forbidden_text = ", ".join(forbidden) or "nothing"
    risk_desc = RISK_LEVELS[actual_risk]["description"]
    gear_text = gear or "nothing special"
    privacy_desc = venue.PRIVACY_DESC[baseline_privacy]

    prompt = f"""Write ONE task for a {gender} sub. Stay in voice, but obey the format exactly.

This is a sexual task. It is not a wardrobe check and not a puzzle. The body is the subject; the
place is only where it happens.

Location: {location}
Fittings here: {fittings_text}
Context: {present}
Privacy: {baseline_privacy} - {privacy_desc}
{reconcile}This setting changes that: {move_line}
Cover during the task: {effective}
Cover (required): {cover_beat}
Risk: {actual_risk}/5 - {risk_desc}
{skin_line}
Phone: {phone_line}
Wearing: {outfit}
{lower}{travel_line}{underdressed}Gear physics - obey these: {gear_text}
Anatomy - only these exist{anatomy_note}: {anatomy}
DESIRED: {desired_text}
USE - at least one must actually be expressed: {use_text}

KNOWN BLOCKERS HERE - do not build around these:
{blockers_text}

RECENTLY USED HERE - prefer variety:
{recent_text}

WHO ELSE IS HERE - taken from the place, do not add to it:
{contract.block(who)}

Work in this order:
{order}

WHAT THIS IS:
- Nobody here agreed to any of it. The sub cannot explain themselves to a stranger, so the task must
  work without one.
- The aim is that nobody notices. Exposure is the thrill; being spotted is the failure state.
- Concealment is the point of the task, not a safety note. Build it so a bystander would have to
  look twice.
- Name what is between them and the nearest sightline, and why nobody will turn round.

Rules:
- The task must be sexual and must express at least one USE kink. Say the thing that makes it sexual,
  not just that something is visible.
- If the Move is only a reveal, carry the kink in how it is done - held, ordered, timed, described.
- Two steps maximum. A conditional ("if X, do Y") counts as two on its own.
- One action, one photo, photo proof required. At most two things for the sub to physically do,
  counting undressing as one. If you have three, cut one.
- No time durations
- Never use: {forbidden_text}
{kind_rules}
- The Move line is fixed content. Adapt it to what is worn and what the gear allows; never replace it
  with something of your own.
- Never name a body part the sub does not have
- Obey the gear physics. Never claim an erection in a cage, never ask a blindfolded sub to frame a shot.
- The task must expose what it photographs. If the subject is under clothing, say what moves and
  how far - never pair "shirt off" with something the shirt does not reveal.
- Underwear is not a separate layer to count. It moves with the garment above it.
- If the Wearing line names trousers or jeans, underwear is not visible unless the waistband has
  moved. Never say a thong or pants are plain to see through them.
- If gear comes off the body, say so, and stop describing it as worn.
- Only hedge about gear that is not already in Wearing. If it is listed, treat it as fact.
- Only garments named in Wearing exist. Do not add a garment, a piece of jewellery or a kind of
  underwear that is not listed. If you need underwear and none is named, say "your underwear".
- If the Wearing line names no legwear, never write trousers, fly, zip or pocket.
- A garment goes to mid-thigh or to the ankles. Pick one, never both.
- Never require the front and the back of the body in the same frame unless the stance says how.
  If both must read, say "side on", or "bent forward, looking back at the mirror".
- The Skin cap is hard. At 1-2 one garment moves and nothing comes off. "Strip to nothing but" is
  only legal at 4-5.
- English only, all of it. No foreign words, no foreign headings, no "ONLY:" or "NOTE:".
- If you are unsure of a place's own word, use the plain one - gate, not a coined one.
- Open with one short clause about who is there, not about the place being quiet. At "nobody around"
  just start. At the other levels say why you are not being seen.
- Only the people in the WHO ELSE IS HERE block exist. Do not invent a group, a passer-by or a
  staff member, and never call the place empty at "about" or "watched" - name who is about instead.
- When people are looking, someone must actually be looking, listening or talking to the sub. Name
  them and what they are doing.
- Nobody may be asked for anything. No holding, no taking the photo, no counting out loud.
- Never stand on furniture unless the block says there is something to stand on.
- Above "caught", reveal first. One item may then come off if the Move needs it - never a full strip.
- At "about" and above, the task must name its cover: what is between you and them, or what you are
  angling away from. "Turned away" alone is not cover for a reveal.
- Cover must match the skin. If it cannot, reduce by one band rather than dropping the cover.
- Do not put the sub fully outside. One step is enough.
- Transit is not the task. If the Travel line says they need to cover up, they put one thing on for
  the walk and it comes off at the spot. Name both, briefly.
- Do not write a task that only works if somebody sees.
- If what they are wearing is far barer than the place expects, say so once and make the
  concealment the point.
- Waiting is described as an event, not a duration: "until the group at the counter turns away".
- The phone is the camera, not a prop. Never write it into the task text. It is never in the shot
  unless the task is a mirror, and then the mirror is what gets named.
- If the task is a mirror shot, name the mirror in the first sentence. The phone is in the frame,
  aimed at the glass, and the other hand is the free one.
- Never say "both hands". One hand holds the camera; say "your free hand".
- Undress FIRST, then take the photo. Never both at once.
- If the Phone line says propped, both hands are free - do not also tell them to hold something.
- Only use a prop when the thing being photographed is in both hands, or the shot needs distance.
  One prop maximum, and it is seasoning - if the task still makes sense without it, cut it.
- Prep comes first. If the prop needs sizing, cutting, filling or arranging, that happens before the
  action, not as an afterthought.
- Be accurate about what a prop physically does. A green bean snaps, it does not split and drip.
- If a fitting has a part that may not exist on theirs, say what to look for.
- A mirror is a place, not a prop. A mirror shot is taken standing at the basin or in front of the
  glass, body facing it. If the shot is in a mirror there is no stall and no closed door.
- If the privacy needs a door between them and people, drop the mirror and shoot in the stall. Never
  write both a mirror and a stall in one task.
- Say what the photo must show, concretely - the thing, and how much of the body is in frame.
- The last sentence states what is bare and what the photo shows. Put nothing else in it.
- At risk 3 and above an anatomy word appears somewhere in the task, or the task is wrong.
- Direction must be physically true. Kneeling means looking down at yourself.
- If a word means two things in this place, qualify it - "bowling balls", not "balls".
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

    response = await venice.chat([{"role": "user", "content": prompt}], voice,
                                 temperature=0.95, model=model or getattr(venice, "TASK_TEXT_MODEL", None))
    if response:
        response = response.strip()
        low = response.lower()
        for kink, words in FORBIDDEN_KEYWORDS.items():
            if depth < 3 and kink in forbidden and any(w in low for w in words):
                logger.warning(f"forbidden kink '{kink}' leaked, retrying")
                return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and gender == "male" and "breast" in low:
            logger.warning("male sub got 'breasts', retrying")
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and not legwear and not travel and any(w in low for w in LEGWEAR_TELLS):
            logger.warning("no legwear but legwear named, retrying")
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and any(f" {w} " in f" {low} " for w in NON_ENGLISH):
            logger.warning("language drift, retrying")
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and actual_risk >= 3 and not any(
                re.search(rf"\b{w}", low) for w in ANATOMY_WORDS):
            logger.warning("no anatomy at risk %s, retrying", actual_risk)
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and tells and not any(w in low for w in tells):
            logger.warning("no kink expressed, retrying")
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and quick and any(w in low for w in ("phone", "camera")):
            logger.warning("phone written into a quick task, retrying")
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and venue.exposure_rank(effective) >= 2 and any(
                w in low for w in ("nobody", "no one", "deserted", "empty", "quiet")):
            logger.warning("called quiet at %s, retrying", effective)
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3 and quick and any(f.lower() in low for f in prof["fittings"] if len(f) > 4):
            logger.warning("fixture named in a quick task, retrying")
            return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
        if depth < 3:
            broke = contract.guard_interaction(low) or contract.guard_standing(low, who)
            if not broke and who["must_name_cover"]:
                broke = contract.guard_cover(low, who, spot)
            if broke:
                logger.warning("contract guard: %s, retrying", broke)
                return await generate_task_text(state, session, voice=voice, depth=depth + 1, model=model)
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