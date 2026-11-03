# core/venue.py
import os
import random

MAX_PROBES = 10

VENUE_ALIASES = {
    "Home": ["home", "house", "flat", "apartment", "bedroom", "living room", "kitchen"],
    "Hotel": ["hotel", "motel", "hostel", "b&b", "airbnb"],
    "Bar": ["bar", "pub", "tavern", "cocktail", "wine bar", "brewery", "nightclub"],
    "Restaurant": ["restaurant", "diner", "bistro", "cafe", "café", "coffee shop"],
    "Gym": ["gym", "fitness", "weights room"],
    "Cinema": ["cinema", "movie", "theatre", "theater", "screening"],
    "Supermarket": ["supermarket", "grocery", "shopping", "mall"],
    "Park": ["park", "garden", "common", "woods", "forest"],
    "Beach": ["beach", "shore", "seafront", "coast"],
    "Train": ["train", "railway", "carriage", "platform"],
    "Bus": ["bus", "coach"],
    "Plane": ["plane", "aircraft", "flight"],
    "Airport": ["airport", "terminal"],
    "Library": ["library"],
    "Museum": ["museum", "gallery"],
    "Pool": ["pool", "swimming", "lido"],
    "Sauna": ["sauna", "steam room"],
    "Work": ["work", "office", "workplace", "cubicle"],
    "Vehicle": ["car", "vehicle", "van", "motorhome"],
    "Retail Store": ["shop", "retail", "boutique", "store"],
    "Church": ["church", "cathedral", "chapel"],
    "Doctor": ["doctor", "clinic", "dentist", "hospital", "waiting room"],
}

_ORDER = sorted(((a, k) for k, al in VENUE_ALIASES.items() for a in al), key=lambda x: -len(x[0]))

VENUE_BASELINE = {
    "Home": "kitchen counter and table, bathroom mirror and shower, bed, sofa, dining chairs, wardrobe mirror, hallway, stairs, windows",
    "Hotel": "bed, bathroom mirror, shower, bathtub, wardrobe, desk and chair, window, corridor, lift",
    "Bar": "bar counter and stools, high tables, corner booths, corridor to the toilets, fire door at the back, optics mirror, coat hooks",
    "Restaurant": "table and chairs, booth seating, cutlery and napkins, corridor to the toilets, cloakroom, doorway to the street",
    "Gym": "changing room benches, lockers, shower cubicles, mirror wall, squat rack, treadmill, water fountain",
    "Cinema": "back row seating, aisle steps, screen glow, corridor to the toilets, foyer, poster wall, stairwell",
    "Supermarket": "aisle shelving, changing room, toilets, loading bay, car park, trolley, freezer aisle",
    "Park": "bench, tree trunk, hedges, bandstand, path, gate, bins, bushes",
    "Beach": "dune grass, breakwater rocks, beach hut, lifeguard post, shoreline, promenade railings",
    "Train": "carriage vestibule, fold-down table, window, luggage rack, toilet cubicle, platform bench",
    "Bus": "back seat, window, handrail, upper deck, bus stop shelter",
    "Plane": "window seat, tray table, cabin aisle, blanket, toilet cubicle",
    "Airport": "gate seating, boarding desk, toilets, moving walkway, window over the apron",
    "Library": "stack aisle, reading table, study carrel, returns trolley, window seat",
    "Museum": "gallery bench, plinth, alcove, stairwell, cloakroom, roped-off corner",
    "Pool": "changing cubicle, poolside lounger, lane ropes, showers, spectator bench",
    "Sauna": "wooden bench, towel hook, glass door, water bucket",
    "Work": "desk, office chair, meeting room table, stairwell, kitchen, photocopier, window, lift",
    "Vehicle": "driver seat, back seat, boot, wing mirror, dashboard, car park, roadside",
    "Retail Store": "changing room, shop floor rails, mirror, stockroom, trolley, till counter, car park",
    "Church": "pew, side aisle, stone pillar, porch, font",
    "Doctor": "waiting room chair, examination couch, curtain rail, sink, corridor",
    "Generic": "door, doorway, mirror, window, chair, table, floor, wall, corridor, stairs, lift, bin, coat hooks, sink",
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
    },
    "Hotel": {
        "bedroom": "bed, wardrobe, desk chair, window, bedside table",
        "bathroom": "mirror, shower, bath, towel rail, sink",
        "corridor": "doorway, lift, window at the end",
        "lobby": "armchairs, reception desk, lift doors",
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


def baseline_chips(location):
    raw = VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])
    return [c.strip() for c in raw.split(",") if c.strip()]


def rooms_for_venue(venue):
    return list((ROOM_OPTIONS.get(get_probe_key(venue)) or {}).keys())


def present_features(venue, room):
    table = ROOM_OPTIONS.get(get_probe_key(venue)) or {}
    return [f.strip() for f in (table.get(room) or "").split(",") if f.strip()]


def probe_options(venue, rooms):
    out = []
    for room in rooms:
        for feature in present_features(venue, room):
            if feature not in out:
                out.append(feature)
    return (out or baseline_chips(venue))[:MAX_PROBES]


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
