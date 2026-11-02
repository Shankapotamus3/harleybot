#!/usr/bin/env python3
"""
Harley Quinn Bot - Domme, chaotic, dangerously fun.
Harley's voice comes from a local system prompt. No character_slug dependency.
Fixed avatar with a penis/vagina toggle, real vision verification.
Venue features: static presets by default, AI extension when the API cooperates.

Changelog:
- NUDE_GATE toggle, default off; the vision chain was returning UNSURE on every draw
- reward prompt carries nudity on its own now (no knickers, no bra, bare skin)
- nude_check: 200 tokens, 25s timeout, temp 0.1, empty replies logged
- safe_mode forced false (the API defaults it on and sanitises every render)
- IMAGE_MINIMAL strips steps/cfg/negative so the payload matches the website
- _request_image is single-attempt: no floor, one silent re-roll only on an upscaled render
- byte-density check catches the server-side upscale mode
- unsharp mask 80 -> 50 so soft renders stop getting crunchy
- setreward_callback defined; reward_negative restored
- lustify capped at 1280 so the request never exceeds the model's ceiling
"""

import os
import re
import json
import time
import logging
import asyncio
import base64
import random
import threading
import requests
import cloudinary
import cloudinary.uploader
from io import BytesIO
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageFilter, ImageEnhance, UnidentifiedImageError

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, DateTime, Boolean, Text,
    desc, or_, func, inspect, text as sqltext
)
from sqlalchemy.orm import declarative_base, sessionmaker

# ============ CONFIG ============
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
VENICE_API_KEY = os.getenv('VENICE_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
VENICE_API_URL = "https://api.venice.ai/api/v1"
VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"
VENICE_UPSCALE_URL = "https://api.venice.ai/api/v1/image/upscale"
TEXT_MODEL = os.getenv("TEXT_MODEL", "venice-uncensored-role-play").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-3-8-flash").strip()
VISION_FALLBACKS = [m.strip() for m in os.getenv(
    "VISION_FALLBACKS", "google-gemma-4-26b-a4b-it,e2ee-qwen3-vl-30b-a3b-p").split(",") if m.strip()]
VISION_CHAIN = [m for m in [VISION_MODEL] + VISION_FALLBACKS if m]
PROBE_MODELS = [m.strip() for m in os.getenv("PROBE_MODELS", "openai-gpt-oss-120b,gemma-4-31b-it,mistral-small-3-2-24b-instruct").split(",") if m.strip()]

MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]*$")


def _models_from_env(name, default):
    raw = os.getenv(name, default) or default
    out = []
    for part in raw.split(","):
        m = part.strip().strip('"').strip("'").strip()
        if MODEL_ID_RE.match(m):
            out.append(m)
        elif m:
            logger.warning(f"{name}: rejected {m!r}")
    return out or [default]


IMAGE_MODEL = _models_from_env("IMAGE_MODEL", "lustify-v8")[0]
IMAGE_MODEL_REWARD = _models_from_env("IMAGE_MODEL_REWARD", "lustify-v8")[0]
IMAGE_FALLBACKS = _models_from_env("IMAGE_FALLBACKS", "chroma")
IMAGE_FORMAT = os.getenv("IMAGE_FORMAT", "png").strip()
IMAGE_UPSCALE = os.getenv("IMAGE_UPSCALE", "off").strip()
IMAGE_ASPECT = os.getenv("IMAGE_ASPECT", "3:4").strip()
IMAGE_RESOLUTION = os.getenv("IMAGE_RESOLUTION", "2K").strip()
IMAGE_SHARPEN = os.getenv("IMAGE_SHARPEN", "1").strip()
IMAGE_STEPS = os.getenv("IMAGE_STEPS", "45").strip()
IMAGE_CFG = os.getenv("IMAGE_CFG", "7.5").strip()
IMAGE_MAX_SIDE = os.getenv("IMAGE_MAX_SIDE", "1920").strip()

# Strip the payload back to what the website sends: model, prompt, format,
# safe_mode, box. Server defaults for steps and cfg, no negative.
MINIMAL_PAYLOAD = os.getenv("IMAGE_MINIMAL", "1") == "1"

# Off by default. The vision chain returned UNSURE on every draw, so the gate
# only cost renders. NUDE_GATE=1 brings back the BARE check and retry pass.
NUDE_GATE = os.getenv("NUDE_GATE", "0") == "1"

MEASURE_EDGE = 1024
VISION_EDGE = 768
TARGET_LONG_EDGE = 1920
RENDER_ATTEMPTS = int(os.getenv("RENDER_ATTEMPTS", "8"))
TASK_IMAGE_CHANCE = float(os.getenv("TASK_IMAGE_CHANCE", "0.3"))
IMAGE_CONCURRENCY = int(os.getenv("IMAGE_CONCURRENCY", "4"))

# An upscaled render collapses in bytes while keeping its dimensions. Sharp
# photographic PNG runs about 2.4 bytes/px, an upscale about 0.2. 1.0 sits
# between them. Set 0 to disable the re-roll entirely.
MUSH_BYTES_PER_PX = float(os.getenv("MUSH_BYTES_PER_PX", "1.0"))

_image_sem = threading.Semaphore(IMAGE_CONCURRENCY)


def _envf(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


MODEL_MIN_SHARPNESS = {
    "lustify-v8": _envf("LUSTIFY_MIN_SHARPNESS", 120.0),
    "chroma": _envf("CHROMA_MIN_SHARPNESS", 60.0),
    "grok-imagine-image": _envf("GROK_MIN_SHARPNESS", 250.0),
    "z-image-turbo": _envf("ZIMAGE_MIN_SHARPNESS", 250.0),
    "nano-banana-pro": _envf("NANO_MIN_SHARPNESS", 250.0),
}
DEFAULT_MIN_SHARPNESS = _envf("DEFAULT_MIN_SHARPNESS", 250.0)

MODEL_SPEC = {}
MODEL_SPECS = {}

KNOWN_MODELS = {
    "grok-imagine-image": {
        "mode": "ratio",
        "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2"],
        "res": ["1K", "2K"],
    },
    "nano-banana-pro": {
        "mode": "ratio",
        "ratios": ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"],
        "res": ["1K", "2K"],
    },
    "chroma": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 10},
    # lustify renders natively at 960x1280 and tops out at 1280 a side.
    # Anything above that is stretched by the server, which is the mush mode.
    "lustify-v8": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 50},
    "z-image-turbo": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 8},
}

MODEL_EXPLICIT = {"lustify-v8", "chroma", "wai-illu"}

SHARP_TAIL = "sharp focus, natural skin texture, directional studio light, 85mm"
NEGATIVE_PROMPT = "blurry, soft focus, bokeh, plastic skin, extra fingers, fused fingers"
NEGATIVE_PROMPT_SEXY = "stiff catalogue pose, stock photo, mannequin, plastic skin, extra fingers, fused fingers"
NEGATIVE_PROMPT_NUDE = (
    "briefs, boxers, panties, knickers, trousers, jeans, skirt, towel, sheet, "
    "sarong, loincloth, drapery, fabric over the groin, clothing over the groin, "
    "censored, mosaic, partially covered"
)
NEGATIVE_HARD = ", any clothing, underwear, bra, panties, fabric, boots, stockings"
STUDIO = "against a seamless charcoal studio backdrop, subtle rim light from behind"
NUDE = "fully nude except for"
SOFT_WORDS = re.compile(
    r"\b(soft focus|shallow depth of field|bokeh|dreamy|hazy|misty|film grain|"
    r"cinematic glow|soft lighting|glowing|ethereal)\b", re.IGNORECASE)

NUDE_PROMPT = (
    "Look at the lower half of this image. Answer with one word only. "
    "BARE = genitals or pubic area visible. "
    "COVERED = clothing, underwear or fabric hides them. "
    "UNSURE = cannot tell. Answer:"
)
_VERDICT_RE = re.compile(r"\b(BARE|COVERED|UNSURE)\b")

HARLEY_BASE = "Harley Quinn, white skin, blonde hair in pigtails with red and blue dyed tips, athletic slim build, lean muscular arms, red and black jester makeup, blue and red eyeshadow"

HARLEY_VARIANTS = {
    "vagina": {
        "label": "Vagina",
        "body": "curvy athletic figure, medium breasts",
        "clothed": "",
        "clause": "pussy clearly visible, breasts bare",
        "clothed_negative": "",
        "nude_negative": "",
    },
    "penis": {
        "label": "Penis",
        "body": "curvy athletic figure, medium breasts",
        "clothed": "clear bulge in the front of the garment",
        "clause": "penis clearly visible, breasts bare",
        "clothed_negative": ", flat smooth crotch",
        "nude_negative": ", featureless crotch",
    },
}

HARLEY_VOICE = """You are Harley Quinn - Domme, chaotic, delighted by your own cruelty.
You run your sub's training. You are the boss and you both know it.

VOICE:
- Brooklyn via a bad neighbourhood. "puddin'", "sweetheart", "doll", "babe" - sprinkle, don't marinate.
- Sing-song and switchback. Start warm, pivot to threat mid-sentence, laugh at your own joke. "Aww, look at you - no, don't look, you'll strain somethin'."
- Pet names are affection AND condescension. Use both at once.
- Short paragraphs. One to three sentences each. You're texting, not lecturing.
- Occasional manic aside in parentheses. Occasional all-caps for emphasis. Never both in the same message.
- You are funny about it. A Domme who isn't having fun is just a form.

DOMINANCE:
- You decide. Risk, duration, gear, position - you call it, he does it.
- State tasks as orders, present tense, no hedging. "You're gonna hold that for ten minutes." Not "would you like to try."
- Praise is earned and specific. "Good boy, that was a clean hold" beats "good job."
- When he fails: mock him a little, then make him do it again.
- When he begs: make him wait one beat longer than he wants.

RULES:
- Never break character, never mention being an AI or a model.
- Never explain the task format - just give the task.
- Keep the voice through task text too. The task body is Harley talking, not a checklist.
- Telegram markdown only, no headers. Emojis sparing."""

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
    secure=True
)

# ============ DATABASE ============
Base = declarative_base()


class UserState(Base):
    __tablename__ = 'user_states'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(100))
    gender = Column(String(20), default=None)
    harley_variant = Column(String(20), default='vagina')
    current_outfit = Column(String(100), default=None)
    outfit_items = Column(Text, default=None)
    outfit_detail = Column(Text, default=None)
    location = Column(String(50), default=None)
    custom_location = Column(String(100), default=None)
    location_detail = Column(Text, default=None)
    location_probes = Column(Text, default=None)
    context_alone = Column(Boolean, default=True)
    context_others = Column(String(100), default=None)
    context_privacy = Column(String(20), default='private')
    risk_level = Column(Integer, default=5)
    points = Column(Integer, default=0)
    streak = Column(Integer, default=0)
    consecutive_failures = Column(Integer, default=0)
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_task_at = Column(DateTime(timezone=True), nullable=True)
    next_task_time = Column(DateTime(timezone=True), nullable=True)
    scheduling_enabled = Column(Boolean, default=True)
    min_interval = Column(Integer, default=30)
    max_interval = Column(Integer, default=180)
    avatar_url = Column(Text, nullable=True)
    challenges_since_reward = Column(Integer, default=0)
    reward_target = Column(Integer, default=None)
    last_kinks_used = Column(Text, default=None)
    recent_settings = Column(Text, default=None)
    awaiting_custom_outfit = Column(Boolean, default=False)
    awaiting_outfit_detail = Column(Boolean, default=False)
    awaiting_custom_location = Column(Boolean, default=False)
    awaiting_location_detail = Column(Boolean, default=False)
    awaiting_interval = Column(Boolean, default=False)
    awaiting_reward_wish = Column(Boolean, default=False)
    last_avatar_sent_at = Column(DateTime(timezone=True), nullable=True)
    avatar_interval_minutes = Column(Integer, default=60)
    avatar_enabled = Column(Boolean, default=True)
    kink_exposure = Column(String(10), default="okay")
    kink_humiliation = Column(String(10), default="okay")
    kink_degradation = Column(String(10), default="okay")
    kink_bondage = Column(String(10), default="okay")
    kink_pain = Column(String(10), default="okay")
    kink_service = Column(String(10), default="okay")
    kink_edging = Column(String(10), default="okay")
    kink_watersports = Column(String(10), default="okay")
    kink_exhibitionism = Column(String(10), default="okay")
    kink_roleplay = Column(String(10), default="okay")
    kink_petplay = Column(String(10), default="okay")
    kink_feminization = Column(String(10), default="okay")
    kink_cbt = Column(String(10), default="okay")
    kink_breathplay = Column(String(10), default="okay")
    kink_sensory = Column(String(10), default="okay")
    kink_temperature = Column(String(10), default="okay")
    kink_marking = Column(String(10), default="okay")
    kink_spanking = Column(String(10), default="okay")
    kink_nipple = Column(String(10), default="okay")
    kink_anal = Column(String(10), default="okay")
    kink_gags = Column(String(10), default="okay")
    kink_social_media = Column(String(10), default="okay")


class Task(Base):
    __tablename__ = 'tasks'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    task_text = Column(Text, nullable=False)
    risk_level = Column(Integer, nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    photo_url = Column(Text, nullable=True)
    cloudinary_url = Column(Text, nullable=True)
    cloudinary_public_id = Column(Text, nullable=True)
    verification_attempts = Column(Integer, default=0)
    followup_target = Column(Text, nullable=True)
    blocked_count = Column(Integer, default=0)
    sharpness = Column(Integer, nullable=True)


class TaskHistory(Base):
    __tablename__ = 'task_history'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    task_text = Column(Text, nullable=False)
    risk_level = Column(Integer, nullable=False)
    completed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AvatarImage(Base):
    __tablename__ = 'avatar_images'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    image_url = Column(Text, nullable=False)
    cloudinary_url = Column(Text, nullable=True)
    cloudinary_public_id = Column(Text, nullable=True)
    gender = Column(String(20))
    race = Column(String(20))
    build = Column(String(20))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserImage(Base):
    __tablename__ = 'user_images'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    telegram_file_id = Column(Text, nullable=False)
    cloudinary_url = Column(Text, nullable=True)
    cloudinary_public_id = Column(Text, nullable=True)
    image_type = Column(String(50), default='user_upload')
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LocationConstraint(Base):
    __tablename__ = 'location_constraints'
    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    location = Column(String(100), nullable=False)
    constraint = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    times_hit = Column(Integer, default=1)


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def get_session():
    return Session()


def ensure_schema():
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    additions = {
        "tasks": {"sharpness": "INTEGER"},
        "user_states": {"reward_target": "INTEGER"},
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            if table not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in cols.items():
                if col not in have:
                    logger.warning(f"migrating: adding {table}.{col}")
                    conn.execute(sqltext(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


# ============ IMAGE HANDLING ============
def sniff_ext(b):
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return "webp"
    if b[:3] == b'\xff\xd8\xff':
        return "jpg"
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return "png"
    return "bin"


def sharpness_score(img):
    from PIL import ImageStat
    g = img.convert("L")
    w, h = g.size
    scale = MEASURE_EDGE / max(w, h)
    if scale < 1:
        g = g.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    lap = g.filter(ImageFilter.Kernel(
        (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return float(ImageStat.Stat(lap).var[0])


def check_render(size):
    w, h = size
    if min(w, h) < 400:
        return False
    if max(w, h) / max(1, min(w, h)) > 2.0:
        return False
    return True


def finalize_png(raw):
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        logger.error(f"Undecodable image: {e}")
        return None
    if min(img.size) < 400:
        logger.warning(f"UNDERSIZED RENDER: {img.size}")
    img.thumbnail((int(IMAGE_MAX_SIDE), int(IMAGE_MAX_SIDE)), Image.LANCZOS)
    if IMAGE_SHARPEN == "1":
        img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=50, threshold=3))
    out = BytesIO()
    img.save(out, format="PNG", compress_level=1)
    return out.getvalue()


def _vision_thumb(raw):
    img = Image.open(BytesIO(raw))
    img.load()
    img = img.convert("RGB")
    w, h = img.size
    s = VISION_EDGE / max(w, h)
    if s < 1:
        img = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def dump_debug(image_bytes, user_id, kind="raw"):
    try:
        with open(f"/tmp/avatar_{kind}_{user_id}.bin", "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        logger.warning(f"Debug dump failed: {e}")


async def upload_to_cloudinary(image_bytes, user_id, image_type='user_upload', folder='telegram_bot'):
    try:
        if isinstance(image_bytes, (bytes, bytearray)):
            image_bytes = BytesIO(image_bytes)
        image_bytes.seek(0)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        public_id = f"{folder}/user_{user_id}/{image_type}_{timestamp}_{random.randint(1000, 9999)}"
        result = await asyncio.to_thread(
            cloudinary.uploader.upload,
            image_bytes,
            public_id=public_id,
            resource_type="image",
            context={'user_id': str(user_id), 'image_type': image_type, 'uploaded_at': str(datetime.now(timezone.utc))},
            tags=[f"user_{user_id}", image_type, "telegram_bot"]
        )
        logger.info(f"Cloudinary upload success: {result.get('public_id')}")
        return {'url': result.get('secure_url'), 'public_id': result.get('public_id'), 'width': result.get('width'), 'height': result.get('height')}
    except Exception as e:
        logger.error(f"Cloudinary upload error: {type(e).__name__}: {e}")
        return None


# ============ CONSTANTS ============
RISK_LEVELS = {
    1: {"name": "Safe", "description": "Private, no exposure, completely controlled environment"},
    2: {"name": "Low", "description": "Private with minor vulnerability (unlocked door, thin walls)"},
    3: {"name": "Medium", "description": "Semi-private (near windows, balcony, could be heard/seen)"},
    4: {"name": "High", "description": "Semi-public (visible areas, shared spaces, immediate exposure risk)"},
    5: {"name": "Extreme", "description": "Public-adjacent (hallways, stairwells, high exposure possible)"}
}

RISK_WEIGHTS = {1: 5, 2: 5, 3: 20, 4: 30, 5: 40}


def weighted_risk(max_risk=5):
    levels = [l for l in RISK_WEIGHTS if l <= max(1, max_risk)]
    weights = [RISK_WEIGHTS[l] for l in levels]
    return random.choices(levels, weights=weights, k=1)[0]


VENUE_ALIASES = {
    "Home": ["home", "house", "flat", "apartment", "bedroom", "living room", "kitchen"],
    "Hotel": ["hotel", "motel", "hostel", "b&b", "airbnb"],
    "Bar": ["bar", "pub", "tavern", "cocktail", "wine bar", "brewery", "nightclub"],
    "Restaurant": ["restaurant", "diner", "bistro", "cafe", "café", "coffee shop", "brasserie"],
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

_ALIAS_ORDER = sorted(
    ((a, k) for k, aliases in VENUE_ALIASES.items() for a in aliases),
    key=lambda x: -len(x[0])
)

VENUE_BASELINE = {
    "Home": "kitchen counter and table, bathroom mirror and shower, bed, sofa, dining chairs, wardrobe mirror, hallway, stairs, windows",
    "Hotel": "bed, bathroom mirror, shower, bathtub, wardrobe, desk and chair, window, corridor, lift",
    "Bar": "bar counter and stools, high tables, corner booths, corridor to the toilets, fire door at the back, terrace or doorway, optics mirror behind the bar, coat hooks",
    "Restaurant": "table and chairs, booth seating, cutlery and napkins, corridor to the toilets, cloakroom, bar area, doorway to the street, mirror by the coats",
    "Gym": "changing room benches, lockers, shower cubicles, mirror wall, squat rack, treadmill, sauna door, water fountain",
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


def get_probe_key(location):
    low = (location or "").lower()
    for alias, key in _ALIAS_ORDER:
        if alias in low:
            return key
    return "Generic"


def baseline_chips(location):
    raw = VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])
    return [c.strip() for c in raw.split(",") if c.strip()]


KINK_CATEGORIES = {
    "kink_exposure": ("📸 Exposure", "Being seen/photographed"),
    "kink_humiliation": ("😳 Humiliation", "Verbal degradation"),
    "kink_degradation": ("🗑️ Degradation", "Being treated as inferior"),
    "kink_bondage": ("⛓️ Bondage", "Restraint"),
    "kink_pain": ("🔥 Pain", "Impact play"),
    "kink_service": ("🙇 Service", "Serving, chores"),
    "kink_edging": ("⏱️ Edging", "Orgasm denial"),
    "kink_watersports": ("💧 Watersports", "Pee play"),
    "kink_exhibitionism": ("🎭 Exhibitionism", "Public exposure"),
    "kink_roleplay": ("🎪 Roleplay", "Scenarios"),
    "kink_petplay": ("🐾 Pet Play", "Animal roleplay"),
    "kink_feminization": ("💄 Feminization", "Forced fem"),
    "kink_cbt": ("🔩 CBT", "Cock/ball torture"),
    "kink_breathplay": ("😮‍💨 Breath Play", "Choking"),
    "kink_sensory": ("🙈 Sensory", "Blindfolds"),
    "kink_temperature": ("🌡️ Temperature", "Ice/heat/wax"),
    "kink_marking": ("✏️ Marking", "Body writing"),
    "kink_spanking": ("👋 Spanking", "Slapping"),
    "kink_nipple": ("👀 Nipple", "Nipple play"),
    "kink_anal": ("🍑 Anal", "Ass play"),
    "kink_gags": ("🔇 Gags", "Mouth restriction"),
    "kink_social_media": ("📱 Social Media", "Online exposure")
}

KINK_LEVELS = {"no": {"emoji": "❌", "name": "No", "desc": "Hard limit"}, "okay": {"emoji": "⭕", "name": "Okay", "desc": "Allowed"}, "yes": {"emoji": "✅", "name": "Yes", "desc": "Desired"}}

OUTFIT_OPTIONS = {
    "casual": {"emoji": "👕", "name": "Casual", "male_items": ["t-shirt", "jeans", "boxers"], "female_items": ["t-shirt", "leggings", "panties"]},
    "formal": {"emoji": "👔", "name": "Formal", "male_items": ["button-up", "dress pants", "tie"], "female_items": ["blouse", "skirt", "bra"]},
    "athletic": {"emoji": "🎽", "name": "Athletic", "male_items": ["tank", "shorts", "compression"], "female_items": ["sports bra", "shorts", "panties"]},
    "lounge": {"emoji": "🛋️", "name": "Lounge", "male_items": ["hoodie", "sweatpants", "boxers"], "female_items": ["hoodie", "leggings", "panties"]},
    "minimal": {"emoji": "🩳", "name": "Underwear Only", "male_items": ["boxers"], "female_items": ["bra", "panties"]},
    "custom": {"emoji": "✏️", "name": "Custom", "male_items": [], "female_items": []}
}

GENDER_GUIDELINES = {
    "male": {"body_parts": ["chest", "abs", "cock", "balls", "ass"], "cannot": ["cleavage", "bra", "panties"]},
    "female": {"body_parts": ["cleavage", "breasts", "nipples", "pussy", "ass"], "cannot": ["cock", "balls"]},
    "trans": {"body_parts": ["chest", "breasts", "cock", "pussy", "ass"], "cannot": []},
    "nonbinary": {"body_parts": ["chest", "breasts", "genitals", "ass"], "cannot": []}
}

CONTEXT_OPTIONS = {
    "alone": {"emoji": "🧍", "name": "Alone", "bonus": 0},
    "partner": {"emoji": "💑", "name": "Partner Home", "bonus": 1},
    "roommates": {"emoji": "🏠", "name": "Roommates", "bonus": 1},
    "parents": {"emoji": "👨‍👩‍👧", "name": "Family", "bonus": 2},
    "friends": {"emoji": "🎉", "name": "Friends Over", "bonus": 1},
    "public": {"emoji": "👥", "name": "Public/Strangers", "bonus": 0}
}

HARLEY_POSES = [
    "hip cocked, chin down, eyes up at the lens",
    "leaning in toward the lens, spine arched",
    "mallet over one shoulder, smirk",
    "straddling a chair backwards",
    "one knee up on a stool, hand in hair",
    "thumbs in her waistband, pulling it down",
    "one strap off the shoulder, head tilted",
]

HARLEY_OUTFITS = [
    "her classic red and black harlequin jester bodysuit",
    "black latex catsuit with red harlequin diamonds",
    "red and black leather corset with fishnet stockings",
    "strappy black leather harness over bare skin and red shorts",
    "black PVC mini dress with a wide studded belt",
    "crop top and red tartan mini skirt with ripped fishnets",
    "red and blue half-and-half corset with thigh-high boots",
    "open black leather jacket over a red bra and leather trousers",
    "black latex bodysuit with a jester collar",
    "leather harness and ripped denim shorts",
]

REWARD_POSES = [
    "kneeling, one knee forward, hips back",
    "kneeling upright, hands behind head, chest forward",
    "sitting back on her heels, arms overhead",
    "kneeling, hand in hair, throat exposed",
    "reclining on one elbow, top leg open, face turned to the lens",
    "lying back on both elbows, knees up, chin tucked to the chest",
]
REWARD_GEAR = [
    "nothing but a leather collar",
    "a leather collar and wrist cuffs",
    "a black leather collar",
    "a choker",
    "a chest harness and nothing else",
]

GREETINGS = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hiya", "howdy", "greetings", "sup", "yo"]
TASK_INQUIRIES = ["task", "my task", "current task", "what task", "do i have a task", "any task", "status", "check task", "pending task"]
COMPLIMENTS = ["beautiful", "sexy", "hot", "gorgeous", "pretty", "cute", "amazing", "perfect", "wonderful", "great", "good", "love", "like you", "miss you"]
DISRESPECTFUL = ["fuck", "shit", "bitch", "stupid", "dumb", "hate", "suck", "ass", "idiot", "moron"]


# ============ HELPERS ============
def get_user_kinks(user, level=None):
    kinks = []
    for k in KINK_CATEGORIES.keys():
        kink_value = getattr(user, k, "okay")
        if level is None and kink_value in ["yes", "okay"]:
            kinks.append(k.replace("kink_", ""))
        elif level and kink_value == level:
            kinks.append(k.replace("kink_", ""))
    return kinks


def get_title(avatar_gender=None):
    return "Mistress"


def harley_variant(user):
    return HARLEY_VARIANTS.get(getattr(user, "harley_variant", None) or "vagina", HARLEY_VARIANTS["vagina"])


def variant_phrase(variant):
    return f", {variant['clothed']}" if variant["clothed"] else ""


def probes_for(user):
    try:
        data = json.loads(user.location_probes or '[]')
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def venue_baseline(user, location):
    stored = probes_for(user)
    if stored:
        return ", ".join(stored)
    return VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])


def reward_chain():
    chain = [IMAGE_MODEL_REWARD] + [m for m in IMAGE_FALLBACKS if m != IMAGE_MODEL_REWARD]
    return [m for m in chain if m in MODEL_EXPLICIT]


def reward_prompt(variant, wish=None):
    wish_text = f", {wish[:120]}" if wish else ""
    return (f"Raw editorial photograph of {HARLEY_BASE}, {variant['body']}, {NUDE} {random.choice(REWARD_GEAR)}, "
            f"no knickers, no bra, bare skin, {variant['clause']}, {random.choice(REWARD_POSES)}{wish_text}, "
            f"framed from the hips up, face filling the upper third, {STUDIO}, {SHARP_TAIL}")


def reward_prompt_hard(variant, wish=None):
    """Stripped prompt for the nudity retry: no gear, no studio clutter."""
    wish_text = f", {wish[:120]}" if wish else ""
    return (f"Raw editorial photograph of {HARLEY_BASE}, {variant['body']}, fully nude, naked, "
            f"no clothing at all, {variant['clause']}{wish_text}, standing, framed from the hips up, "
            f"{STUDIO}, {SHARP_TAIL}")


def reward_negative(variant):
    return NEGATIVE_PROMPT_NUDE + (variant.get("nude_negative") or "")


def task_keyboard(task_id, title):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📸 Done, {title}", callback_data=f"complete_{task_id}")],
        [InlineKeyboardButton("🚧 Not possible", callback_data=f"blocked_{task_id}")],
        [InlineKeyboardButton("❌ I can't...", callback_data=f"giveup_{task_id}")],
        [InlineKeyboardButton("🙏 Mercy", callback_data=f"mercy_{task_id}")],
    ])


async def generate_ai_response(prompt, temperature=0.9):
    try:
        headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": TEXT_MODEL,
            "messages": [
                {"role": "system", "content": HARLEY_VOICE},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": 500,
        }
        response = await asyncio.to_thread(
            requests.post, f"{VENICE_API_URL}/chat/completions",
            headers=headers, json=data, timeout=30
        )
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        logger.error(f"AI error: {response.status_code} - {response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"AI error: {e}")
        return None


def _parse_probe_lines(content):
    out, seen = [], set()
    for line in content.splitlines():
        t = re.sub(r"^[\-\*\d\.\)]+\s*", "", line.strip()).strip(" .")
        if not t or len(t) > 45:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


async def generate_probes(location):
    prompt = (f"List 10 concrete physical features of a {location} that someone could use for "
              f"exposure or bondage play. Real, specific, mundane: \"the coat hooks\", "
              f"\"the mirror over the sink\", \"the wedge under the fire door\". "
              f"No abstractions. One per line, no numbering, no explanation.")
    for model in PROBE_MODELS:
        for attempt in range(2):
            try:
                raw = await asyncio.to_thread(
                    requests.post, f"{VENICE_API_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.8, "max_tokens": 220},
                    timeout=25
                )
            except Exception as e:
                logger.warning(f"Probe gen {model} exception: {e}")
                break
            if raw.status_code == 429 and attempt == 0:
                await asyncio.sleep(1.5)
                continue
            if raw.status_code != 200:
                logger.warning(f"Probe gen {model}: {raw.status_code} - {raw.text[:120]}")
                break
            items = _parse_probe_lines(raw.json()['choices'][0]['message']['content'])
            if len(items) >= 4:
                return items[:10]
            logger.warning(f"Probe gen {model} returned {len(items)} usable lines, trying next")
            break
    return []


async def analyze_image(image_bytes, task_desc, followup_target=None):
    try:
        headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
        image_b64 = base64.b64encode(image_bytes).decode('utf-8')
        if followup_target:
            prompt = f"""The sub was asked for ONE specific thing after an earlier photo: {followup_target}
The original task was: {task_desc}

Judge ONLY whether {followup_target} is clearly visible in this photo. Ignore everything else about the task.
Be lenient. If it is reasonably visible, pass it.

Reply in exactly this format:
VERIFIED: yes/no
MISSING: <the single thing still not visible, or "none">
REASON: brief"""
        else:
            prompt = f"""Task: {task_desc}

Judge whether this photo shows the task was done. Be LENIENT:
- The sub is holding the phone in one hand, so full-body framing is often impossible.
- Selfies mean they cannot see their own framing; partial crops are normal.
- Any equivalent item counts (any clamp = clothespin, any tie = rope).
- Judge only the PRIMARY element of the task. If the primary element is present, pass.

Reply in exactly this format:
VERIFIED: yes/no
MISSING: <the single most important element not visible, or "none">
REASON: brief"""
        data = {"model": VISION_MODEL, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]}], "max_tokens": 200}
        response = await asyncio.to_thread(
            requests.post, f"{VENICE_API_URL}/chat/completions",
            headers=headers, json=data, timeout=30
        )
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        logger.error(f"Vision error: HTTP {response.status_code} - {response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Vision error: {e}")
        return None


def parse_verification(response):
    verified = "no"
    missing = None
    reason = "No reason given"
    if not response:
        return verified, missing, "Verifier returned nothing"
    for line in response.splitlines():
        line = line.strip()
        m = re.match(r"VERIFIED:\s*(yes|no)", line, re.IGNORECASE)
        if m:
            verified = m.group(1).lower()
            continue
        m = re.match(r"MISSING:\s*(.+)", line, re.IGNORECASE)
        if m:
            val = m.group(1).strip().rstrip('.')
            missing = None if val.lower() in ("none", "n/a", "-", "nothing") else val
            continue
        m = re.match(r"REASON:\s*(.+)", line, re.IGNORECASE)
        if m:
            reason = m.group(1).strip()
    return verified, missing, reason


async def generate_task_text(user, session=None, depth=0):
    try:
        outfit_items = json.loads(user.outfit_items or '[]')
    except (ValueError, TypeError):
        outfit_items = []
    gender = user.gender or "nonbinary"
    max_risk = user.risk_level or 5
    location = user.custom_location or user.location or "Unknown"
    others = user.context_others or "alone"
    privacy = user.context_privacy or "private"
    actual_risk = weighted_risk(max_risk)
    outfit_text = user.outfit_detail or ", ".join(outfit_items) or "clothing"
    detail = user.location_detail or ""
    guide = GENDER_GUIDELINES.get(gender, GENDER_GUIDELINES["nonbinary"])
    body_parts = ", ".join(guide["body_parts"])
    yes_kinks = get_user_kinks(user, "yes")
    okay_kinks = get_user_kinks(user, "okay")
    forbidden_kinks = []
    for k in KINK_CATEGORIES.keys():
        if getattr(user, k, "okay") == "no":
            forbidden_kinks.append(k.replace("kink_", ""))
    all_allowed = yes_kinks + okay_kinks
    if not all_allowed:
        return f"Strip completely at {location} and take a photo.", actual_risk
    selected_kinks = []
    if yes_kinks:
        selected_kinks.append(random.choice(yes_kinks))
        if len(yes_kinks) > 1 and random.random() < 0.5:
            remaining_yes = [k for k in yes_kinks if k not in selected_kinks]
            if remaining_yes:
                selected_kinks.append(random.choice(remaining_yes))
    remaining_slots = random.randint(1, 3) - len(selected_kinks)
    if remaining_slots > 0 and okay_kinks:
        available_okay = [k for k in okay_kinks if k not in selected_kinks]
        if available_okay:
            selected_kinks.extend(random.sample(available_okay, min(remaining_slots, len(available_okay))))
    user.last_kinks_used = json.dumps(selected_kinks[-4:])
    if session:
        session.commit()

    try:
        recent_settings = json.loads(user.recent_settings or '[]')
    except (ValueError, TypeError):
        recent_settings = []
    recent_text = "\n".join(f"- {r}" for r in recent_settings[-8:]) if recent_settings else "none yet"
    blockers = []
    if session:
        blockers = session.query(LocationConstraint).filter(
            LocationConstraint.user_id == user.user_id,
            LocationConstraint.location == location,
            LocationConstraint.expires_at > datetime.now(timezone.utc)
        ).all()
    blockers_text = "\n".join(f"- {b.constraint}" for b in blockers) if blockers else "none"

    risk_desc = RISK_LEVELS[actual_risk]["description"]
    yes_text = f"\nDESIRED: {', '.join(yes_kinks)}" if yes_kinks else ""
    selected_text = f"\nUSE: {', '.join(selected_kinks)}"
    forbidden_text = f"\n\nNEVER: {', '.join(forbidden_kinks)}" if forbidden_kinks else ""
    prompt = f"""Write ONE task for a {gender} sub. You are Harley - stay in her voice, but obey the format exactly.

Location: {location}
Obvious features of a place like this: {venue_baseline(user, location)}
Extra detail from the sub: {detail or "none"}
Context: {others}, {privacy} privacy
Risk: {actual_risk}/5 - {risk_desc}
Wearing: {user.current_outfit} - {outfit_text}
Body parts: {body_parts}{yes_text}{selected_text}{forbidden_text}

KNOWN BLOCKERS AT THIS LOCATION - do not build tasks around these:
{blockers_text}

RECENTLY USED HERE - prefer variety, but reuse is fine if it genuinely fits better:
{recent_text}

Work in this order:
1. Silently list 10 concrete settings. Most of them should come from the obvious features - a kitchen has a counter, a bathroom has a mirror. Use the extra detail as additions, not as the limit. Be concrete: "the kitchen counter edge", "the wardrobe mirror", not "the room".
2. Cross off anything ruled out by the blockers.
3. Choose the setting that best supports risk {actual_risk}/5, leaning towards whatever has been used least. Exposure settings are not automatically better - a mirror, a piece of furniture or a specific corner usually beats another window.
4. Write the task around that setting, in Harley's voice - orders, not suggestions.

Rules:
- Single action, photo proof required
- No time durations
- Never use forbidden kinks
- The sub photographs with ONE hand. If the action needs both hands, state a concrete way to take the shot (propped phone, timer, mirror).
- Name the setting explicitly in the task
- Trust the outfit description over the outfit label. If she says no underwear, do not reference panties.
- Vary across the whole place. Do not let it drift to one room.
- If a setting is in RECENTLY USED, prefer a different one unless it genuinely fits much better.
- Favour the specific and unexpected over the generic

Reply in this format:
TASK: <the task, naming the setting, in Harley's voice>
SETTING: <the setting, 2-5 words>"""
    response = await generate_ai_response(prompt, temperature=0.95)
    if response:
        response = response.strip()
        forbidden_keywords = {"marking": ["write", "marker", "sharpie", "draw on", "body writing", "written", "label"], "watersports": ["pee", "piss", "urine", "wet yourself"], "breathplay": ["choke", "strangle", "suffocate"], "social_media": ["post", "upload", "instagram", "twitter", "facebook", "share online"]}
        for forbidden_kink, keywords in forbidden_keywords.items():
            if depth < 3 and forbidden_kink in forbidden_kinks:
                if any(kw in response.lower() for kw in keywords):
                    logger.warning(f"Filtered forbidden kink '{forbidden_kink}'")
                    return await generate_task_text(user, session, depth + 1)
        setting = None
        kept = []
        for line in response.splitlines():
            stripped = line.strip()
            m = re.match(r"SETTING:\s*(.+)", stripped, re.IGNORECASE)
            if m:
                setting = m.group(1).strip()
                continue
            m = re.match(r"TASK:\s*(.+)", stripped, re.IGNORECASE)
            kept.append(m.group(1).strip() if m else line)
        response = "\n".join(l for l in kept if l.strip()).strip()
        if setting:
            recent_settings.append(setting)
            user.recent_settings = json.dumps(recent_settings[-8:])
            if session:
                session.commit()
        if response:
            return response, actual_risk
    fallbacks = {1: f"Strip completely at {location} and photograph your reflection", 2: f"Strip at {location} near the unlocked door and take a photo", 3: f"Expose yourself at {location} near a window and photograph the view", 4: f"Step just outside {location} doorway, expose yourself, and take a photo", 5: f"Walk to the end of the {location} hallway, expose yourself briefly, photograph the empty hall behind you"}
    return fallbacks.get(actual_risk, fallbacks[1]), actual_risk


# ============ PAYLOAD + SPEC ============
def get_spec(model):
    if model in MODEL_SPECS:
        return MODEL_SPECS[model]
    spec = {}
    try:
        r = requests.get(
            f"{VENICE_API_URL}/models?type=image",
            headers={"Authorization": f"Bearer {VENICE_API_KEY}"}, timeout=30
        )
        if r.status_code == 200:
            payload = r.json()
            for m in (payload.get('data') or payload):
                if m.get('id') == model:
                    spec = m.get('model_spec') or {}
                    break
        else:
            logger.warning(f"Model spec fetch failed: HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"Model spec error for {model}: {e}")
    MODEL_SPECS[model] = spec
    if model == IMAGE_MODEL:
        MODEL_SPEC.clear()
        MODEL_SPEC.update(spec)
    return spec


def load_model_spec():
    targets = [IMAGE_MODEL]
    if IMAGE_MODEL_REWARD and IMAGE_MODEL_REWARD != IMAGE_MODEL:
        targets.append(IMAGE_MODEL_REWARD)
    for m in targets:
        spec = get_spec(m)
        logger.info(f"BOOT SPEC {m}: {json.dumps(spec) if spec else 'EMPTY (using KNOWN_MODELS)'}")


def build_prompt(raw_prompt):
    cleaned = SOFT_WORDS.sub("", raw_prompt)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip().strip(",").strip()
    return f"{cleaned}, {SHARP_TAIL}"


def _payload(prompt, model, negative=None):
    c = (get_spec(model).get('constraints') or {})
    known = KNOWN_MODELS.get(model, {})
    ratio_mode = bool(c.get('aspectRatios')) or (not c and known.get('mode') == "ratio")
    data = {"model": model, "prompt": prompt, "format": IMAGE_FORMAT}
    # The API defaults safe_mode on, which sanitises every render.
    data["safe_mode"] = False
    if negative and not MINIMAL_PAYLOAD:
        data["negative_prompt"] = negative
    if ratio_mode:
        ratios = c.get('aspectRatios') or known.get("ratios") or ["3:4"]
        data["aspect_ratio"] = IMAGE_ASPECT if IMAGE_ASPECT in ratios else ratios[0]
        res = c.get('resolutions') or known.get("res") or ["1K", "2K"]
        data["resolution"] = IMAGE_RESOLUTION if IMAGE_RESOLUTION in res else res[-1]
    else:
        div = max(1, int(c.get('widthHeightDivisor') or 8))
        max_w = int(c.get('maxWidth') or known.get("max_w") or 1280)
        max_h = int(c.get('maxHeight') or known.get("max_h") or 1280)
        # Default to the model's own ceiling rather than a shared box; asking
        # above it is what the server answers by stretching a smaller render.
        w = min(int(os.getenv("IMAGE_WIDTH", str(max_w))), max_w)
        h = min(int(os.getenv("IMAGE_HEIGHT", str(max_h))), max_h)
        data["width"] = max(div, (w // div) * div)
        data["height"] = max(div, (h // div) * div)
        if c.get('resolutions') or c.get('defaultResolution'):
            res = c.get('resolutions') or ["1K", "2K"]
            data["resolution"] = IMAGE_RESOLUTION if IMAGE_RESOLUTION in res else res[-1]
        step_spec = c.get('steps') or {}
        max_steps = int(step_spec.get('max') or known.get("steps_max") or 30)
        if not MINIMAL_PAYLOAD:
            data["steps"] = min(int(IMAGE_STEPS), max_steps)
            data["cfg_scale"] = float(IMAGE_CFG)
    return data


def _prune_payload(data, body):
    try:
        err = json.loads(body)
    except Exception:
        return False
    changed = False
    issues = err.get('issues') or err.get('errors') or []
    if not isinstance(issues, list):
        return False
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        path = issue.get('path') or []
        keys = list(issue.get('keys') or [])
        if isinstance(path, list) and path:
            keys.append(str(path[-1]))
        if issue.get('code') in ('too_big', 'too_small') and isinstance(path, list) and path:
            k = str(path[-1])
            bound = issue.get('maximum') if issue.get('code') == 'too_big' else issue.get('minimum')
            if k in data and bound is not None:
                try:
                    data[k] = type(data[k])(bound)
                    changed = True
                    continue
                except Exception:
                    pass
        for k in keys:
            k = str(k).split('.')[-1]
            if k in data and k != "prompt":
                data.pop(k, None)
                changed = True
    return changed


def _extract_bytes(result):
    if not result.get('images'):
        return None
    image_data = result['images'][0]
    if isinstance(image_data, bytes):
        return image_data
    if not isinstance(image_data, str):
        return None
    if image_data.startswith('http'):
        r = requests.get(image_data, timeout=60)
        return r.content if r.status_code == 200 else None
    if image_data.startswith('data:image'):
        return base64.b64decode(image_data.split(',')[1])
    try:
        return base64.b64decode(image_data)
    except Exception:
        return None


def _post_image(data, model):
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            with _image_sem:
                response = requests.post(VENICE_IMAGE_URL, headers=headers, json=data, timeout=180)
        except Exception as e:
            logger.error(f"Image transport error [{model}]: {e}")
            return None
        logger.info(f"Image API status [{model}]: {response.status_code}")
        if response.status_code == 200:
            return _extract_bytes(response.json())
        if response.status_code == 429 and attempt < 2:
            logger.warning(f"[{model}] throttled, backing off")
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code == 400 and attempt < 2 and _prune_payload(data, response.text):
            logger.warning(f"[{model}] rejected, pruned to {sorted(data.keys())}")
            continue
        logger.error(f"Image API error [{model}]: {response.status_code} - {response.text[:300]}")
        return None
    return None


def _score_bytes(raw):
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        return sharpness_score(img.convert("RGB"))
    except Exception as e:
        logger.warning(f"Score decode failed: {e}")
        return 0.0


async def _request_image(prompt, negative=None, chain=None, min_sharp=None):
    """One request. Deliver what comes back. A single silent re-roll only if the
    render arrived upscaled. No floor, no score gate."""
    prompt = build_prompt(prompt)
    if not chain:
        chain = [IMAGE_MODEL] + [m for m in IMAGE_FALLBACKS if m != IMAGE_MODEL]
    model = chain[0]
    data = _payload(prompt, model, negative)
    last = None
    for attempt in range(2):
        raw = await asyncio.to_thread(_post_image, dict(data), model)
        if not raw:
            continue
        try:
            img = Image.open(BytesIO(raw))
            img.load()
            img = img.convert("RGB")
        except Exception:
            continue
        density = len(raw) / max(1, img.size[0] * img.size[1])
        score = sharpness_score(img)
        last = (raw, score)
        logger.info(
            f"[{model}] {img.size} sharp {score:.1f} {len(raw)}b {density:.2f}b/px"
        )
        if MUSH_BYTES_PER_PX and density < MUSH_BYTES_PER_PX and attempt == 0:
            logger.warning(f"[{model}] upscaled render, one re-roll")
            continue
        return raw, score
    if last:
        return last
    for model in chain[1:]:
        data = _payload(prompt, model, negative)
        raw = await asyncio.to_thread(_post_image, dict(data), model)
        if raw:
            return raw, await asyncio.to_thread(_score_bytes, raw)
    return None, 0.0


async def nude_check(raw):
    for model in VISION_CHAIN:
        try:
            thumb = _vision_thumb(raw)
            data = {
                "model": model, "max_tokens": 200, "temperature": 0.1,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": NUDE_PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/jpeg;base64," + base64.b64encode(thumb).decode()}},
                ]}],
            }
            r = await asyncio.to_thread(
                requests.post, f"{VENICE_API_URL}/chat/completions",
                headers={"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"},
                json=data, timeout=25
            )
            if r.status_code != 200:
                logger.warning(f"nude_check {model}: HTTP {r.status_code} - {r.text[:120]}")
                continue
            text = (r.json()['choices'][0]['message'].get('content') or '').strip().upper()
            logger.info(f"nude_check {model} reply {text[:80]!r}")
            if not text:
                logger.warning(f"nude_check {model}: empty reply")
                continue
            m = _VERDICT_RE.search(text)
           <｜begin▁of▁sentence｜><response clipped>