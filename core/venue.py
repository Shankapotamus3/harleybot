# core/venue.py
import random

MAX_PROBES = 10
TYPICAL_SAMPLE = 4

# Task needs, not furniture. True of most spaces regardless of venue, which is
# what keeps an unlisted place like a bowling alley workable.
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
    "Bowling": ["bowling", "ten pin", "tenpin"],
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
    "Bowling": "ball return, scoring console, foul line, lane, vinyl seating, shoe rack, arcade cabinet",
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
    "Generic": "door, doorway, mirror, window, chair, table, floor, wall, corner, counter edge, sink",
}

# Venues with rooms get a picker; anything absent gets AI suggestions instead.
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
    """Capability chips first, grounded with a few real features from the rooms."""
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
    """A short shuffled sample of what a place like this has. Never the full list."""
    raw = (state.get("venue_typical") or "").strip()
    if not raw:
        raw = venue_baseline(state, location)
    items = [i.strip() for i in raw.split(",") if i.strip()]
    random.shuffle(items)
    return ", ".join(items[:limit])


def notes_for_prompt(state, location, limit=8):
    """The committed notes are a sample, so sample them again per task."""
    raw = (state.get("location_notes") or "").replace("\n", ", ")
    items = [i.strip() for i in raw.split(",") if i.strip()]
    if len(items) > limit:
        random.shuffle(items)
        items = items[:limit]
    return ", ".join(items)


# ============ EXPOSURE ============
EXPOSURE_LADDER = ["private", "semi", "exposed", "public"]

# Ways a task can step outside the room. Second value is how many rungs up.
EXPOSURE_MOVES = {
    "Home": [("step onto the balcony", 2), ("out the front door onto the step", 2),
             ("into the hallway past your own door", 1),
             ("up to the window with the curtain open", 1)],
    "Hotel": [("into the corridor with the door left ajar", 2),
              ("out onto the balcony", 2), ("down to the lobby", 2),
              ("by the window with the curtain open", 1)],
    "Gym": [("out to the car park", 2), ("into the changing corridor", 1)],
    "Bar": [("out the front for a moment", 1), ("into the smoking area", 1)],
    "Bowling": [("back to the car park", 2), ("into the arcade where it is darker", 1)],
    "Work": [("into the stairwell", 1), ("out to the car park", 2)],
    "Library": [("into the entrance foyer", 1), ("out onto the steps", 2)],
    "Launderette": [("out to the pavement", 2)],
    "Vehicle": [("out of the car", 1)],
    "Generic": [("into the corridor", 1), ("just outside the door", 2)],
}


def exposure_rank(level):
    try:
        return EXPOSURE_LADDER.index(level)
    except ValueError:
        return 0


def effective_privacy(baseline, delta):
    return EXPOSURE_LADDER[min(3, exposure_rank(baseline) + delta)]


def pick_exposure(state, blockers=()):
    """One way out of this venue, or (None, 0). Respects active blockers."""
    place = state.get("venue") or state.get("location") or ""
    moves = EXPOSURE_MOVES.get(get_probe_key(place), EXPOSURE_MOVES["Generic"])
    live = []
    for move, delta in moves:
        words = [w for w in move.split() if len(w) > 3]
        if any(w in (b or "").lower() for b in blockers for w in words):
            continue
        live.append((move, delta))
    if not live:
        return None, 0
    return random.choice(live)