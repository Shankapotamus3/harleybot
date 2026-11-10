import random

MAX_PROBES = 10
TYPICAL_SAMPLE = 4

TASK_PROBES = {
    "bend": "something to bend over",
    "anchor": "something to hold or tie to",
    "kneel": "something to kneel on",
    "mirror": "a mirror at a usable angle",
    "door": "a door that shuts",
    "overhead": "something overhead",
    "surface": "a hard or cold surface",
    "height": "something at hip height",
    "noise": "something that rattles or spills",
    "witness": "a window or gap to be seen through",
}

VENUE_ALIASES = {
    "Home": ["home", "house", "flat", "apartment", "bedroom", "living room", "kitchen"],
    "Hotel": ["hotel", "motel", "hostel", "b&b", "airbnb"],
    "Bar": ["bar", "pub", "tavern", "cocktail", "wine bar", "brewery", "nightclub", "club"],
    "Restaurant": ["restaurant", "diner", "bistro", "cafe", "café", "coffee shop"],
    "Gym": ["gym", "fitness", "weights room"],
    "Pool": ["pool", "swimming", "leisure centre", "leisure center"],
    "Cinema": ["cinema", "movie", "theatre", "theater", "screening"],
    "Supermarket": ["supermarket", "grocery", "shopping", "mall"],
    "Park": ["park", "garden", "common", "woods", "forest"],
    "Beach": ["beach", "shore", "seafront", "sand", "coast"],
    "Bowling": ["bowling", "ten pin", "tenpin", "concourse"],
    "Arcade": ["arcade", "amusement"],
    "Library": ["library"],
    "Museum": ["museum", "gallery"],
    "Sauna": ["sauna", "steam room"],
    "Airport": ["airport", "terminal"],
    "Church": ["church", "chapel", "cathedral"],
    "Doctor": ["doctor", "clinic", "surgery", "dentist"],
    "Launderette": ["launderette", "laundrette", "laundromat"],
    "Work": ["work", "office", "workplace"],
    "Vehicle": ["car", "vehicle", "van", "back seat"],
    "Retail Store": ["store", "shop", "retail"],
    "Studio": ["studio"],
    "Rooftop": ["rooftop", "roof terrace"],
    "Casino": ["casino", "gaming floor", "slots", "poker room", "betting shop"],
    "Restroom": ["restroom", "toilet", "toilets", "washroom", "loo", "public convenience",
                 "men's room", "gents", "ladies"],
}

_ORDER = [(alias, key) for key, aliases in VENUE_ALIASES.items() for alias in aliases]

VENUE_BASELINE = {
    "Home": "door, doorway, mirror, window, chair, table, floor, wall, stairs, coat hooks, sink, counter edge",
    "Hotel": "door, doorway, mirror, window, chair, desk, wardrobe, sink, towel rail, bed",
    "Bar": "bar stool, bar edge, low table, booth, doorway, mirror behind the bar, window",
    "Restaurant": "table edge, chair, booth, doorway, window, counter",
    "Gym": "bench, rack, mirror wall, machine frame, mat, doorway",
    "Pool": "changing cubicle, poolside lounger, lane rope, showers, bench",
    "Cinema": "seat, armrest, aisle, doorway, screen glow, back row wall",
    "Supermarket": "trolley, shelf edge, freezer door, aisle end, checkout counter, mirror",
    "Park": "bench, tree trunk, railing, path, gate, hedge",
    "Beach": "railing, breakwater, dunes, beach hut, shoreline, promenade wall",
    "Bowling": "ball return, scoring console, foul line, lane boards, concourse, vinyl seating, shoe rack, arcade cabinet",
    "Arcade": "cabinet edge, change machine, stool, counter, doorway, corner",
    "Library": "stack aisle, reading table, study carrel, returns trolley, window seat",
    "Museum": "gallery bench, plinth, alcove, stairwell, cloakroom, roped-off corner",
    "Sauna": "wooden bench, towel hook, glass door, water bucket",
    "Airport": "gate seating, boarding desk, toilets, moving walkway, window over the apron",
    "Church": "pew, side aisle, stone pillar, porch, font",
    "Doctor": "waiting room chair, examination couch, curtain rail, sink, corridor",
    "Launderette": "washing machine, dryer door, folding table, plastic chair, sink",
    "Work": "desk, office chair, meeting room table, stairwell, kitchen, photocopier, window, lift",
    "Vehicle": "driver seat, back seat, boot, wing mirror, dashboard, car park, roadside",
    "Retail Store": "changing room, shop floor rail, mirror, stockroom, trolley, till counter",
    "Studio": "backdrop roll, stool, light stand, seamless paper, floor mark",
    "Rooftop": "parapet, vent housing, aerial mast, stair hut, gravel",
    "Casino": "slot machine bank, card table edge, chair, pillar, aisle end, cashier counter, bar rail, carpet",
    "Restroom": "cubicle, cubicle door, coat hook, cistern, mirror, sink, hand dryer, tiled floor, partition",
    "Generic": "door, doorway, mirror, window, chair, table, floor, wall, corner, counter edge, sink",
}

ROOM_OPTIONS = {
    "Home": {
        "kitchen": "counter edge, table, sink, dishwasher door, chair back, drawer handle",
        "bathroom": "mirror, shower screen, towel rail, sink edge, bath rim, door handle",
        "bedroom": "bed, wardrobe mirror, bedside table, chair, windowsill, door",
        "living room": "sofa, coffee table, curtains, doorway, floor lamp, fireplace",
        "office": "desk, office chair, filing cabinet, bookshelf, window",
        "laundry room": "washing machine, dryer door, airing rack, worktop",
        "hallway": "coat hooks, shoe rack, front door, stairs, radiator",
        "balcony": "railing, patio door, folding chair, planters",
        "deck": "railing, lounger, steps, outdoor table",
    },
    "Hotel": {
        "bedroom": "bed, wardrobe, desk chair, window, bedside table, minibar, suitcase",
        "bathroom": "mirror, shower, bath, towel rail, sink, bath mat",
        "corridor": "doorway, lift, window at the end, ice machine",
        "lobby": "armchairs, reception desk, lift doors",
        "balcony": "railing, patio door, folding chair",
    },
    "Bowling": {
        "lane": "foul line, lane boards, gutter, pin deck",
        "approach": "approach dots, ball rack, seating behind",
        "ball return": "return rails, ball cradle, console edge",
        "concourse": "tiled floor, shoe rack, bench, bin",
        "seating": "vinyl bench, low table, coat hook",
        "arcade": "cabinet edge, change machine, stool",
        "shoe counter": "counter edge, rack, bench",
        "toilets": "cubicle, mirror, sink, hand dryer",
    },
    "Work": {
        "desk": "desk, chair, monitor, drawer, under-desk",
        "meeting room": "table, chairs, whiteboard, glass wall, door",
        "kitchen": "counter, kettle, fridge door, sink",
        "stairwell": "landing, handrail, fire door",
        "toilets": "cubicle, mirror, sink, hand dryer",
    },
    "Restroom": {
        "cubicle": "cubicle door, hook, cistern, tiled floor",
        "basins": "mirror, sink, hand dryer, bin",
    },
    "Casino": {
        "floor": "slot bank, aisle, chair, pillar, carpet",
        "tables": "card table edge, chair back, rail, dealer gap",
        "bar": "bar rail, stool, pillar",
        "toilets": "cubicle, mirror, sink, hand dryer",
        "foyer": "pillar, cashier counter, doorway, plant",
    },
}


def get_probe_key(location):
    low = (location or "").lower()
    for alias, key in _ORDER:
        if alias in low:
            return key
    return "Generic"


def is_known(location):
    return get_probe_key(location) != "Generic"


def baseline_chips(location):
    raw = VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])
    return [c.strip() for c in raw.split(",") if c.strip()]


def rooms_for_venue(venue):
    return list((ROOM_OPTIONS.get(get_probe_key(venue)) or {}).keys())


def present_features(venue, room):
    table = ROOM_OPTIONS.get(get_probe_key(venue)) or {}
    return [f.strip() for f in (table.get(room) or "").split(",") if f.strip()]


def task_probe_options(venue, rooms, limit=MAX_PROBES):
    caps = list(TASK_PROBES.values())
    random.shuffle(caps)

    grounded = []
    for room in rooms or []:
        grounded.extend(present_features(venue, room))
    if not grounded:
        grounded = baseline_chips(venue)
    random.shuffle(grounded)

    out = caps[: max(4, limit - 3)]
    for item in grounded:
        if len(out) >= limit:
            break
        if item not in out:
            out.append(item)
    return out[:limit]


def probe_options(venue, rooms):
    return task_probe_options(venue, rooms)


def probe_question(rooms):
    if not rooms:
        return "What here could a task actually use?\nPick anything true, skip the rest."
    return (f"What in the {', '.join(rooms)} could a task actually use?\n"
            "Pick anything true, skip the rest.")


def room_chips(state, location):
    picked = []
    for room in state.get("rooms") or []:
        picked.extend(present_features(location, room))
    return [p for p in picked if p]


def venue_baseline(state, location):
    picked = room_chips(state, location)
    if picked:
        return ", ".join(picked)
    stored = state.get("probes") or []
    if stored:
        return ", ".join(stored)
    return VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])


def venue_typical_features(state, location, limit=TYPICAL_SAMPLE):
    raw = (state.get("venue_typical") or "").strip()
    if not raw:
        raw = venue_baseline(state, location)
    items = [i.strip() for i in raw.split(",") if i.strip()]
    random.shuffle(items)
    return ", ".join(items[:limit])


def notes_for_prompt(state, location, limit=8):
    raw = (state.get("location_notes") or "").replace("\n", ", ")
    items = [i.strip() for i in raw.split(",") if i.strip()]
    if len(items) > limit:
        random.shuffle(items)
        items = items[:limit]
    return ", ".join(items)


# ============ PRIVACY ============
PRIVACY_LEVELS = [
    ("alone", "Nobody around", "Nobody can see you and nobody can walk in."),
    ("caught", "Could be caught", "You're alone for now, but someone could appear at any moment."),
    ("about", "People about", "People are around, but none of them are watching you."),
    ("watched", "Being watched", "People are looking at you or talking to you. Nowhere to hide without moving."),
]
EXPOSURE_LADDER = [k for k, _, _ in PRIVACY_LEVELS]
PRIVACY_LABELS = {k: short for k, short, _ in PRIVACY_LEVELS}
PRIVACY_DESC = {k: desc for k, _, desc in PRIVACY_LEVELS}

LEGACY_PRIVACY = {"private": "alone", "semi": "caught",
                  "exposed": "about", "public": "watched"}

# Fallback only. The live default comes from the derived venue profile.
VENUE_PRIVACY = {
    "Home": "alone", "Hotel": "alone", "Studio": "alone",
    "Vehicle": "caught", "Work": "caught", "Sauna": "caught",
    "Doctor": "caught", "Generic": "caught",
    "Gym": "about", "Pool": "about", "Bar": "about", "Restaurant": "about",
    "Cinema": "about", "Rooftop": "about", "Bowling": "about",
    "Arcade": "about", "Beach": "about", "Park": "about",
    "Supermarket": "watched", "Retail Store": "watched", "Library": "watched",
    "Museum": "watched", "Airport": "watched", "Launderette": "watched",
    "Church": "watched", "Restroom": "watched", "Casino": "watched",
}

# Fallback only.
EXPOSURE_MOVES = {
    "Home": [("the door left on the latch", 1),
             ("step onto the balcony the street can see", 2),
             ("out to the front step", 2),
             ("someone knocks at the front door", 2)],
    "Hotel": [("the corridor door left ajar, footsteps going past", 1),
              ("out onto the balcony", 2), ("down to the lobby", 2)],
    "Bowling": [("a staff member walks the concourse behind you", 2),
                ("back to the car park", 2)],
    "Gym": [("someone comes into the changing corridor", 2),
            ("out to the car park", 2)],
    "Bar": [("someone comes out for a smoke", 2)],
    "Work": [("someone takes the stairwell", 1), ("out to the car park", 2)],
    "Library": [("someone walks the stack aisle", 1), ("out onto the steps", 2)],
    "Launderette": [("someone comes in with a basket", 2),
                    ("out to the pavement", 2)],
    "Vehicle": [("someone parks alongside", 2)],
    "Restroom": [("the door left on the latch with footsteps in the corridor", 1),
                 ("out into the corridor", 2)],
    "Casino": [("the aisle between two slot banks", 2), ("out to the foyer", 2)],
    "Generic": [("footsteps in the corridor", 1), ("just outside the door", 2)],
}

# Fallback only.
COVER_SPOTS = {
    "Home": ["the hallway", "the far corner of the room", "behind the door"],
    "Hotel": ["the bathroom", "the lift alcove", "the end of the corridor"],
    "Bowling": ["the arcade corner", "the far end of the concourse", "the toilets"],
    "Bar": ["the far end of the bar", "outside", "the toilets"],
    "Gym": ["the changing corridor", "the corner behind the racks", "the car park"],
    "Supermarket": ["the far aisle", "the stockroom door", "the car park"],
    "Retail Store": ["the changing room", "the back of the shop", "the car park"],
    "Library": ["the far stack aisle", "behind the returns trolley", "the foyer"],
    "Museum": ["the roped-off corner", "the stairwell", "the cloakroom"],
    "Pool": ["the changing cubicle", "the far end of the poolside", "the showers"],
    "Cinema": ["the back row", "the aisle", "the foyer"],
    "Airport": ["the toilets", "the far gate seating", "the window over the apron"],
    "Launderette": ["behind the open machine door", "the corner by the sink", "the pavement"],
    "Work": ["the stairwell", "the toilets", "the car park"],
    "Park": ["behind the hedge", "the far path", "behind a tree"],
    "Beach": ["the dunes", "behind the breakwater", "the beach hut"],
    "Church": ["a side aisle", "behind a pillar", "the porch"],
    "Rooftop": ["behind the stair hut", "the far parapet", "behind the vent housing"],
    "Restroom": ["a closed cubicle", "the end cubicle", "the far basin against the wall"],
    "Casino": ["between two slot banks", "behind a pillar", "the far end of the aisle", "the toilets"],
    "Generic": ["a corner", "the corridor", "just outside"],
}

COVER_WORDS = ("no cover", "nowhere", "can't hide", "cant hide", "no hiding",
               "open plan", "open space", "nowhere private", "nowhere to hide",
               "in the open", "nowhere to stand")

FILLER = {"with", "that", "this", "from", "into", "over", "your", "you're", "left",
          "onto", "past", "comes", "walks", "someone", "there", "where", "behind",
          "under", "another"}


def is_no_cover(text):
    low = (text or "").lower()
    return any(w in low for w in COVER_WORDS)


def normalise_privacy(value):
    value = (value or "").strip().lower()
    value = LEGACY_PRIVACY.get(value, value)
    return value if value in PRIVACY_DESC else ""


def privacy_default(location):
    return VENUE_PRIVACY.get(get_probe_key(location), VENUE_PRIVACY["Generic"])


def exposure_rank(level):
    try:
        return EXPOSURE_LADDER.index(level)
    except ValueError:
        return 0


def effective_privacy(baseline, target):
    return EXPOSURE_LADDER[max(exposure_rank(baseline), min(3, target))]


def pick_exposure(baseline, moves, blockers=()):
    """Only moves that actually cost cover. (None, 0) if none do."""
    here = exposure_rank(baseline)
    live = []
    for move, target in moves:
        if target <= here:
            continue
        words = [w.strip(".,") for w in move.split()
                 if len(w) > 3 and w.lower() not in FILLER]
        if any(w in (b or "").lower() for b in blockers for w in words):
            continue
        live.append((move, target))
    if not live:
        return None, 0
    return random.choice(live)


def cover_hint(location):
    spots = COVER_SPOTS.get(get_probe_key(location), COVER_SPOTS["Generic"])
    return random.choice(spots)