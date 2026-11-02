#!/usr/bin/env python3
"""
Harley Quinn Bot - Domme, chaotic, dangerously fun.
Harley's voice comes from a local system prompt. No character_slug dependency.
Fixed avatar with a penis/vagina toggle, real vision verification.
Venue features: static presets by default, AI extension when the API cooperates.
"""

import os
import re
import json
import logging
import asyncio
import base64
import random
import requests
import cloudinary
import cloudinary.uploader
from io import BytesIO
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageFilter, ImageEnhance, ImageStat, UnidentifiedImageError

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, DateTime, Boolean, Text, desc, or_, func
from sqlalchemy.orm import declarative_base, sessionmaker

# ============ CONFIG ============
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
VENICE_API_KEY = os.getenv('VENICE_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
VENICE_API_URL = "https://api.venice.ai/api/v1"
VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"
VENICE_UPSCALE_URL = "https://api.venice.ai/api/v1/image/upscale"
TEXT_MODEL = os.getenv("TEXT_MODEL", "venice-uncensored-role-play").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "claude-opus-4-8-fast").strip()
PROBE_MODELS = [m.strip() for m in os.getenv("PROBE_MODELS", "openai-gpt-oss-120b,gemma-4-31b-it,mistral-small-3-2-24b-instruct").split(",") if m.strip()]
# Tasks: sharp, R-rated. Rewards: explicit. Sharp models first, chroma last - it never blurs
# less than 1440x1920 and it is what every policy rejection was landing on.
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "grok-imagine-image-quality").strip()
IMAGE_MODEL_REWARD = "nano-banana-pro"
IMAGE_FALLBACKS = ["nano-banana-pro", "chroma"]
IMAGE_FORMAT = os.getenv("IMAGE_FORMAT", "png").strip()
# Literal, not env - the Railway variable layer has proved unreliable and the upscaler
# only ever existed for chroma. Re-enable by changing these two lines.
IMAGE_UPSCALE = "off"
IMAGE_UPSCALE_CREATIVITY = "0.2"
IMAGE_ASPECT = os.getenv("IMAGE_ASPECT", "3:4").strip()
IMAGE_RESOLUTION = os.getenv("IMAGE_RESOLUTION", "2K").strip()
IMAGE_SHARPEN = os.getenv("IMAGE_SHARPEN", "1").strip()
IMAGE_STEPS = os.getenv("IMAGE_STEPS", "30").strip()
IMAGE_CFG = os.getenv("IMAGE_CFG", "5").strip()
IMAGE_MAX_SIDE = os.getenv("IMAGE_MAX_SIDE", "1280").strip()
# Mean edge energy below this counts as a bad render and gets regenerated.
MIN_SHARPNESS = 12.0
MODEL_SPEC = {}
MODEL_SPECS = {}

# Used when the live spec is empty, so a failed /models fetch cannot cripple the payload.
KNOWN_MODELS = {
    "grok-imagine-image-quality": {
        "mode": "ratio",
        "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2"],
        "res": ["1K", "2K"],
    },
    "grok-imagine-image-2-0": {
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
    "z-image-turbo": {"mode": "pixels", "max_w": 1280, "max_h": 1280},
}

# Models that render near 1K and merely upscale when `resolution` is present.
# They get their real maximum box instead, so the pixels are native rather than interpolated.
NATIVE_CAPS = {"chroma": (960, 1280)}

SHARP_TAIL = "sharp focus, natural skin texture, directional studio light, 85mm"
NEGATIVE_PROMPT = "blurry, soft focus, bokeh, plastic skin, extra fingers, fused fingers"
NEGATIVE_PROMPT_SEXY = "stiff catalogue pose, stock photo, mannequin, plastic skin, extra fingers, fused fingers"
NEGATIVE_PROMPT_NUDE = "briefs, boxers, panties, knickers, trousers, jeans, skirt, towel, sheet, sarong, loincloth, drapery, fabric over the groin, clothing over the groin, censored, mosaic, partially covered, cropped at the hips"
STUDIO = "against a seamless charcoal studio backdrop, subtle rim light from behind"
# Bare "nude" rather than "fully nude except for" - nano-banana's policy trips on the longer claims.
NUDE = "nude"
SOFT_WORDS = re.compile(
    r"\b(soft focus|shallow depth of field|bokeh|dreamy|hazy|misty|film grain|"
    r"cinematic glow|soft lighting|glowing|ethereal)\b", re.IGNORECASE)

# Look only. Interpolated into every avatar prompt - keep it to physical description.
HARLEY_BASE = "Harley Quinn, white skin, blonde hair in pigtails with red and blue dyed tips, athletic slim build, lean muscular arms, red and black jester makeup, blue and red eyeshadow"

HARLEY_VARIANTS = {
    "vagina": {
        "label": "Vagina",
        "body": "curvy athletic figure, medium breasts",
        "clothed": "",
        "clause": "genitals clearly visible, breasts bare",
        "clothed_negative": "",
        "nude_negative": "",
    },
    "penis": {
        "label": "Penis",
        "body": "curvy athletic figure, medium breasts",
        "clothed": "clear bulge in the front of the garment",
        "clause": "genitals clearly visible, breasts bare",
        "clothed_negative": ", flat smooth crotch",
        "nude_negative": ", featureless crotch",
    },
}

# Voice only. Fed to the text model. Never goes near an image prompt.
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

# ============ IMAGE HANDLING ============
def sniff_ext(b):
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return "webp"
    if b[:3] == b'\xff\xd8\xff':
        return "jpg"
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return "png"
    return "bin"

def image_size(image_bytes):
    try:
        with Image.open(BytesIO(image_bytes)) as im:
            return im.size
    except Exception:
        return (0, 0)

def sharpness_score(image_bytes):
    """Mean edge energy on a downscaled greyscale copy. Low means mush or a black frame."""
    try:
        img = Image.open(BytesIO(image_bytes)).convert("L")
        img.thumbnail((512, 512))
        return ImageStat.Stat(img.filter(ImageFilter.FIND_EDGES)).stddev[0]
    except Exception:
        return 0.0

def check_render(size, data):
    """Compare the returned render against what was asked for. Catches silent upscaling."""
    w, h = size
    req_w, req_h = data.get("width"), data.get("height")
    if req_w and req_h:
        if w > req_w * 1.2 or h > req_h * 1.2:
            logger.warning(f"UPSCALED RENDER: asked {req_w}x{req_h}, got {w}x{h}")
        elif w < req_w * 0.8 or h < req_h * 0.8:
            logger.warning(f"UNDERSIZED RENDER: asked {req_w}x{req_h}, got {w}x{h}")
    elif data.get("resolution") == "2K" and max(w, h) < 1500:
        logger.warning(f"UNDERSIZED 2K RENDER: {w}x{h}")

def normalize_for_telegram(image_bytes, max_side=1280, sharpen=True):
    """Decode any image format, cap the long edge, sharpen, re-encode as baseline JPEG."""
    try:
        img = Image.open(BytesIO(image_bytes))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        logger.error(f"Undecodable image: {e}")
        return None
    try:
        logger.info(f"Decoded image size: {img.size}")
        if min(img.size) < 400:
            logger.warning(f"THUMBNAIL RENDER: {img.size} - model returned something tiny")
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        if sharpen:
            img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=140, threshold=3))
            img = ImageEnhance.Contrast(img).enhance(1.04)
            img = ImageEnhance.Sharpness(img).enhance(1.10)
        out = BytesIO()
        img.save(out, format="JPEG", quality=90, progressive=False, optimize=True)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Image normalize error: {e}")
        return None

def dump_debug(image_bytes, user_id, kind="raw"):
    try:
        with open(f"/tmp/avatar_{kind}_{user_id}.bin", "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        logger.warning(f"Debug dump failed: {e}")

# ============ CLOUDINARY HELPERS ============
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

# Rolled against max_risk, then renormalised to whatever ceiling the user set.
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
    "kids": {"emoji": "👶", "name": "Kids Present", "bonus": 3},
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
    "a black leather chest harness",
    "a leather harness and thigh-high boots",
    "leather wrist cuffs and a collar",
    "a strappy leather body harness",
    "nothing but a leather collar",
    "a leather harness and sheer stockings",
    "ankle boots",
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
    return f", {variant['clothed']}" if variant.get("clothed") else ""

def probes_for(user):
    return json.loads(user.location_probes or '[]')

def venue_baseline(user, location):
    stored = probes_for(user)
    if stored:
        return ", ".join(stored)
    return VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])

def reward_chain():
    if IMAGE_MODEL_REWARD:
        return [IMAGE_MODEL_REWARD] + [m for m in IMAGE_FALLBACKS if m != IMAGE_MODEL_REWARD]
    return None

def reward_prompt(variant, wish=None):
    wish_text = f", {wish[:120]}" if wish else ""
    clause = variant.get("clause") or "genitals clearly visible"
    return (f"Raw editorial photograph of {HARLEY_BASE}, {variant.get('body', '')}, {NUDE} {random.choice(REWARD_GEAR)}, "
            f"{clause}, {random.choice(REWARD_POSES)}{wish_text}, framed from the hips up, "
            f"face filling the upper third, {STUDIO}, {SHARP_TAIL}")

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
    """Extra venue features. Empty list on failure - callers already have the static baseline."""
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
    """Returns raw verifier text, or None on transport failure."""
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
    """Line-based parse. Tolerates case, spacing and field reordering."""
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
    outfit_items = json.loads(user.outfit_items or '[]')
    gender = user.gender or "nonbinary"
    max_risk = user.risk_level or 5
    location = user.custom_location or user.location or "Unknown"
    others = user.context_others or "alone"
    privacy = user.context_privacy or "private"
    actual_risk = weighted_risk(max_risk)
    if others == "kids":
        actual_risk = min(2, actual_risk)
        max_risk = min(2, max_risk)
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

    recent_settings = json.loads(user.recent_settings or '[]')
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

# ============ AVATAR ============
def get_spec(model):
    """Per-model constraints, fetched once and cached."""
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
    """Strip soft-vocabulary, then append the sharpness tail. Single entry point."""
    cleaned = SOFT_WORDS.sub("", raw_prompt)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip().strip(",").strip()
    return f"{cleaned}, {SHARP_TAIL}"

def _apply_native_caps(data, model):
    """Models that render at ~1K and merely upscale get their real box, not a resolution hint."""
    native = NATIVE_CAPS.get(model)
    if not native:
        return data
    data.pop("resolution", None)
    data["width"], data["height"] = native
    return data

def _payload(prompt, model, negative=None):
    """Use live constraints when present, KNOWN_MODELS when the spec fetch failed."""
    c = (get_spec(model).get('constraints') or {})
    known = KNOWN_MODELS.get(model, {})
    ratio_mode = bool(c.get('aspectRatios')) or (not c and known.get('mode') == "ratio")
    data = {"model": model, "prompt": prompt, "format": IMAGE_FORMAT}
    if negative:
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
        w = min(int(os.getenv("IMAGE_WIDTH", "960")), max_w)
        h = min(int(os.getenv("IMAGE_HEIGHT", "1280")), max_h)
        data["width"] = max(div, (w // div) * div)
        data["height"] = max(div, (h // div) * div)
        # Chroma declares defaultResolution but no resolutions array - without this it renders at 1K
        # and the API upscales. Sending resolution is what stops that.
        if c.get('resolutions') or c.get('defaultResolution'):
            res = c.get('resolutions') or ["1K", "2K"]
            data["resolution"] = IMAGE_RESOLUTION if IMAGE_RESOLUTION in res else res[-1]
        step_spec = c.get('steps') or {}
        max_steps = int(step_spec.get('max') or known.get("steps_max") or 30)
        data["steps"] = min(int(IMAGE_STEPS), max_steps)
        data["cfg_scale"] = float(IMAGE_CFG)
        # Chroma ignores the box while resolution is present and upscales instead.
        # Drop resolution and pin its real maximum dimensions so the pixels are native.
        _apply_native_caps(data, model)
    return data

def _prune_payload(data, body):
    """Drop or clamp whatever the endpoint rejected. True if the payload changed."""
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

async def _request_image(prompt, negative=None, chain=None):
    headers = {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}
    prompt = build_prompt(prompt)
    if not chain:
        chain = [IMAGE_MODEL] + [m for m in IMAGE_FALLBACKS if m != IMAGE_MODEL]
    for model in chain:
        data = _payload(prompt, model, negative)
        for attempt in range(3):
            logger.info(f"Image payload [{model}]: {json.dumps({k: v for k, v in data.items() if k not in ('prompt', 'negative_prompt')})}")
            response = await asyncio.to_thread(
                requests.post, VENICE_IMAGE_URL, headers=headers, json=data, timeout=180
            )
            logger.info(f"Image API status [{model}]: {response.status_code}")
            if response.status_code == 200:
                result = response.json()
                raw = await asyncio.to_thread(_extract_bytes, result)
                if not raw:
                    logger.error(f"No image data from {model}: {str(result)[:300]}")
                    break
                logger.info(f"Raw image from {model}: {len(raw)} bytes, header {raw[:12]!r}")
                await asyncio.to_thread(check_render, image_size(raw), data)
                score = await asyncio.to_thread(sharpness_score, raw)
                logger.info(f"Sharpness score [{model}]: {score:.1f}")
                if score < MIN_SHARPNESS:
                    if attempt < 2:
                        logger.warning(f"[{model}] bad render (score {score:.1f}), regenerating")
                        continue
                    logger.warning(f"[{model}] still bad after 3 attempts, trying next model")
                    break
                if model != chain[0]:
                    logger.warning(f"FALLBACK SERVED: {model} (primary {chain[0]} failed)")
                return raw
            if response.status_code == 400 and attempt < 2 and _prune_payload(data, response.text):
                logger.warning(f"[{model}] rejected, pruned to {sorted(data.keys())}: {response.text[:200]}")
                continue
            logger.error(f"Image API error [{model}]: {response.status_code} - {response.text[:300]}")
            break
    logger.error(f"All image models failed: {chain}")
    return None

async def _upscale(raw):
    """Redraw detail server-side before any Pillow work. Falls back to the raw bytes."""
    if IMAGE_UPSCALE in ("off", "0", ""):
        return raw
    try:
        payload = {
            "image": base64.b64encode(raw).decode("utf-8"),
            "scale": int(IMAGE_UPSCALE),
            "creativity": float(IMAGE_UPSCALE_CREATIVITY),
        }
        r = await asyncio.to_thread(
            requests.post, VENICE_UPSCALE_URL,
            headers={"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=180
        )
        logger.info(f"Upscale status: {r.status_code}")
        if r.status_code != 200:
            logger.warning(f"Upscale failed, keeping raw: {r.status_code} - {r.text[:200]}")
            return raw
        logger.info(f"Upscaled {len(raw)}b -> {len(r.content)}b ({r.headers.get('content-type')})")
        return r.content
    except Exception as e:
        logger.warning(f"Upscale error, keeping raw: {e}")
        return raw

async def _build_and_send(user, prompt, user_id=None, negative=None, chain=None, upscale=True):
    raw = await _request_image(prompt, negative, chain)
    if not raw:
        return None
    if user_id:
        await asyncio.to_thread(dump_debug, raw, user_id, "raw")
    if upscale:
        raw = await _upscale(raw)
    cap = min(int(IMAGE_MAX_SIDE), 1280)
    normalized = await asyncio.to_thread(normalize_for_telegram, raw, cap, IMAGE_SHARPEN == "1")
    if normalized and user_id:
        await asyncio.to_thread(dump_debug, normalized, user_id, "norm")
        logger.info(f"Normalized {len(raw)}b -> {len(normalized)}b")
    elif not normalized:
        logger.error("Normalization returned None")
    return normalized

async def generate_avatar_pose(user, pose_type="dominant"):
    try:
        variant = harley_variant(user)
        if pose_type == "reward":
            return await _build_and_send(user, reward_prompt(variant), user.user_id, reward_negative(variant), reward_chain(), upscale=True)
        prompt = (f"Raw editorial photograph of {HARLEY_BASE}, {variant['body']}{variant_phrase(variant)}, "
                  f"wearing {random.choice(HARLEY_OUTFITS)}, {random.choice(HARLEY_POSES)}, "
                  f"framed from mid-thigh up, {STUDIO}, {SHARP_TAIL}")
        return await _build_and_send(user, prompt, user.user_id, NEGATIVE_PROMPT_SEXY + (variant.get("clothed_negative") or ""), upscale=False)
    except Exception as e:
        logger.error(f"Avatar error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

async def deliver_reward(context, user, wish=None, session=None):
    user_id = user.user_id
    variant = harley_variant(user)
    image_bytes = await _build_and_send(user, reward_prompt(variant, wish), user_id, reward_negative(variant), reward_chain(), upscale=True)
    if not image_bytes:
        await context.bot.send_message(chat_id=user_id, text="Couldn't get it out of the camera. Try /avatar.")
        return
    cloud = await upload_to_cloudinary(image_bytes, user_id, image_type='reward')
    await send_avatar_photo(context, user_id, image_bytes, "🎁 Your reward.")
    if cloud and session:
        session.add(UserImage(user_id=user_id, telegram_file_id='generated', cloudinary_url=cloud['url'], cloudinary_public_id=cloud['public_id'], image_type='reward'))
        session.commit()

async def send_avatar_photo(context, chat_id, image_bytes, caption):
    try:
        if not image_bytes or len(image_bytes) < 1000:
            logger.error(f"Invalid image bytes: {len(image_bytes) if image_bytes else 'None'}")
            return False
        buf = BytesIO(image_bytes)
        buf.seek(0)
        await context.bot.send_photo(chat_id=chat_id, photo=buf, caption=caption)
        return True
    except Exception as e:
        logger.error(f"Send photo error: {e}")
    return False

async def _debug_send(context, user_id, label, prompt, negative, chain=None, upscale=False):
    """Raw and normalized as documents, then normalized again as a photo for like-for-like."""
    raw = await _request_image(prompt, negative, chain)
    if not raw:
        await context.bot.send_message(chat_id=user_id, text=f"{label}: no image returned - check the log.")
        return
    w, h = await asyncio.to_thread(image_size, raw)
    try:
        await context.bot.send_document(
            chat_id=user_id, document=BytesIO(raw), filename=f"{label.lower()}_raw.{sniff_ext(raw)}",
            caption=f"{label} RAW | {w}x{h} | {len(raw)}b"
        )
    except Exception as e:
        await context.bot.send_message(chat_id=user_id, text=f"{label}: could not send raw - {e}")
    if upscale:
        raw = await _upscale(raw)
    cap = min(int(IMAGE_MAX_SIDE), 1280)
    norm = await asyncio.to_thread(normalize_for_telegram, raw, cap, IMAGE_SHARPEN == "1")
    if not norm:
        await context.bot.send_message(chat_id=user_id, text=f"{label}: normalize returned None")
        return
    try:
        await context.bot.send_document(
            chat_id=user_id, document=BytesIO(norm), filename=f"{label.lower()}_norm.jpg",
            caption=f"{label} NORM | {len(norm)}b | source {w}x{h} | cap {cap}px"
        )
    except Exception as e:
        await context.bot.send_message(chat_id=user_id, text=f"{label}: could not send normalized - {e}")
    try:
        await context.bot.send_photo(chat_id=user_id, photo=BytesIO(norm), caption=f"{label} NORM as photo")
    except Exception as e:
        await context.bot.send_message(chat_id=user_id, text=f"{label}: could not send photo - {e}")

# ============ CUSTOM INPUT DISPATCHER ============
async def custom_input_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await chat_handler(update, context)
            return
        if user.awaiting_reward_wish:
            await handle_reward_wish(update, context)
            return
        elif user.awaiting_custom_outfit:
            await handle_custom_outfit(update, context)
            return
        elif user.awaiting_outfit_detail:
            await handle_outfit_detail(update, context)
            return
        elif user.awaiting_custom_location:
            await handle_custom_location(update, context)
            return
        elif user.awaiting_location_detail:
            await handle_location_detail(update, context)
            return
        elif user.awaiting_interval:
            await handle_interval_input(update, context)
            return
        elif context.user_data.get('blocked_task_id'):
            await handle_blocker_input(update, context)
            return
        await chat_handler(update, context)
    finally:
        session.close()

# ============ CHAT HANDLER ============
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_text = update.message.text.strip()
    message_lower = message_text.lower()
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            context_info = "This is a new sub who hasn't finished setup."
            has_task = False
        else:
            has_task = session.query(Task).filter_by(user_id=user_id, status="pending").first() is not None
            context_info = f"""His profile: {user.username or 'unknown'}, {user.gender or 'gender unknown'}, wearing {user.outfit_detail or user.current_outfit or 'unknown'}, at {user.custom_location or user.location or 'unknown'}, risk {user.risk_level}/5, {user.points} points, streak {user.streak}, {user.completed_tasks} tasks completed, active task: {'yes' if has_task else 'no'}."""
        is_greeting = any(greet in message_lower for greet in GREETINGS)
        is_task_inquiry = any(inquiry in message_lower for inquiry in TASK_INQUIRIES)
        is_compliment = any(comp in message_lower for comp in COMPLIMENTS)
        is_disrespectful = any(dis in message_lower for dis in DISRESPECTFUL)
        if is_greeting:
            instruction = "He just walked in and greeted you. Greet him back, tease him a little, use a pet name. 1-2 sentences."
        elif is_task_inquiry and has_task:
            instruction = "He's asking about his task and stalling. Remind him it's pending and tell him to get on with it. Firm but amused. 1-2 sentences."
        elif is_task_inquiry:
            instruction = "He's asking for a task but hasn't got one. Tell him to use /task, or tease him for being eager. 1-2 sentences."
        elif is_compliment:
            instruction = "He just complimented you. Take it as your due, maybe tease him for angling. 1-2 sentences."
        elif is_disrespectful:
            instruction = "He was disrespectful. Put him in his place, threaten punishment or extra tasks. Stern. 1-2 sentences."
        else:
            instruction = "Reply to him. If he seems lost, point him at /task. 1-2 sentences."
        prompt = f"""{context_info}

{instruction}

He said: "{message_text}"
"""
        response = await generate_ai_response(prompt, temperature=0.9)
        if response:
            await update.message.reply_text(response.strip())
        else:
            if is_greeting:
                fallbacks = ["Well hello there, puddin'. Ready to be useful?", "*smirks* Look what the cat dragged in. I was gettin' bored.", "Heya, doll. Hope you stretched, you got work to do.", "Hiya, sweetheart. Miss me?"]
            elif is_task_inquiry and has_task:
                fallbacks = ["You got a task sittin' there, puddin'. Stop stallin'.", "*taps foot* That ain't gonna do itself, doll.", "Tick tock. Go on, get."]
            elif is_task_inquiry:
                fallbacks = ["Eager, huh? /task. Go on.", "Aw, look at him beg for it. /task, doll.", "I'll hand you one when I feel like it. Try /task."]
            elif is_compliment:
                fallbacks = ["Course I'm gorgeous, puddin'. Now earn more of it.", "*preens* Tryin' to get on my good side, are ya?", "Flattery. Cheap, but it works."]
            elif is_disrespectful:
                fallbacks = ["Watch the mouth, doll. That'll cost ya.", "Ooh, brave. Stupid, but brave. Punishment's comin'.", "You wanna try that again, sweetheart?"]
            else:
                fallbacks = ["Mmm? Speak up, puddin'.", "That how you talk to me? Try again.", "You're distractin' me, doll. Back to work.", "Less talk, more action.", "*raises an eyebrow* Yeah?", "Bold today. I like it. Sometimes."]
            await update.message.reply_text(random.choice(fallbacks))
    except Exception as e:
        logger.error(f"CHAT_HANDLER ERROR: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await update.message.reply_text("Mmm? Somethin' broke, puddin'. Try again.")
    finally:
        session.close()

# ============ COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "sub"
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            user = UserState(
                user_id=user_id,
                username=username,
                next_task_time=datetime.now(timezone.utc) + timedelta(minutes=random.randint(30, 180)),
            )
            session.add(user)
            session.commit()
            keyboard = [[InlineKeyboardButton("👨 Male", callback_data="gender_male")], [InlineKeyboardButton("👩 Female", callback_data="gender_female")], [InlineKeyboardButton("⚧ Trans", callback_data="gender_trans")], [InlineKeyboardButton("🌈 Non-binary", callback_data="gender_nonbinary")]]
            await update.message.reply_text(f"Welcome, {username}. Select gender:", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        loc = user.custom_location or user.location or "Not set"
        yes_count = len(get_user_kinks(user, "yes"))
        okay_count = len(get_user_kinks(user, "okay"))
        await update.message.reply_text(f"Welcome back, {username}!\n\n📍 Location: {loc}\nRisk: {user.risk_level} | Points: {user.points}\nStreak: {user.streak}\nKinks: {yes_count} desired, {okay_count} allowed\n\n/task - Get challenge\n/wherenow - Set location\n/outfit - Set clothing\n/avatar - New photo of me\n/variant - What I'm packing\n/risk - Set risk\n/kinks - Set kink preferences\n/interval - Set auto-task interval\n/schedule - Auto-tasks on/off\n/status - Check task\n/rewards - Check reward progress")
    finally:
        session.close()

async def payload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exactly what would be sent, without spending a generation."""
    task_payload = json.dumps({k: v for k, v in _payload('x', IMAGE_MODEL).items() if k != 'prompt'})
    reward_model = IMAGE_MODEL_REWARD or IMAGE_MODEL
    reward_payload = json.dumps({k: v for k, v in _payload('x', reward_model).items() if k != 'prompt'})
    await update.message.reply_text(
        f"TASK {IMAGE_MODEL}\n{task_payload}\n\n"
        f"REWARD {reward_model}\n{reward_payload}\n\n"
        f"chains: task {[IMAGE_MODEL] + IMAGE_FALLBACKS} | reward {reward_chain()}\n"
        f"min sharpness: {MIN_SHARPNESS}\n\n"
        f"task spec: {json.dumps(MODEL_SPECS.get(IMAGE_MODEL, {})) or 'EMPTY'}\n"
        f"reward spec: {json.dumps(MODEL_SPECS.get(reward_model, {})) or 'EMPTY'}"
    )

async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user:
            await update.message.reply_text(f"✅ Bot is working!\n\nflags: outfit={user.awaiting_custom_outfit}/{user.awaiting_outfit_detail}, loc={user.awaiting_custom_location}/{user.awaiting_location_detail}, interval={user.awaiting_interval}, reward={user.awaiting_reward_wish}\n\nlocation: {user.custom_location or 'Not set'}\nvenue key: {get_probe_key(user.custom_location or '')}\ndetail: {user.location_detail or 'none'}\nprobes: {len(probes_for(user))}\noutfit: {user.outfit_detail or 'none'}\nvariant: {user.harley_variant}\ntext: {TEXT_MODEL} | vision: {VISION_MODEL}\nmodel: {IMAGE_MODEL} -> {IMAGE_FALLBACKS} | reward: {IMAGE_MODEL_REWARD or 'same'}\nformat: {IMAGE_FORMAT} | upscale: {IMAGE_UPSCALE}/{IMAGE_UPSCALE_CREATIVITY} | aspect: {IMAGE_ASPECT} | res: {IMAGE_RESOLUTION} | sharpen: {IMAGE_SHARPEN} | max_side: {IMAGE_MAX_SIDE} | steps: {IMAGE_STEPS} | cfg: {IMAGE_CFG} | min_sharp: {MIN_SHARPNESS}\n\n/payload for the exact JSON")
        else:
            await update.message.reply_text("No user record found")
    finally:
        session.close()

async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user:
            user.awaiting_location_detail = False
            user.awaiting_outfit_detail = False
            user.awaiting_custom_outfit = False
            user.awaiting_custom_location = False
            session.commit()
        await update.message.reply_text("Fine - I'll work with what I have. Use /task.")
    finally:
        session.close()

async def gender_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("👨 Male", callback_data="gender_male")], [InlineKeyboardButton("👩 Female", callback_data="gender_female")], [InlineKeyboardButton("⚧ Trans", callback_data="gender_trans")], [InlineKeyboardButton("🌈 Non-binary", callback_data="gender_nonbinary")]]
    await update.message.reply_text("Select gender:", reply_markup=InlineKeyboardMarkup(keyboard))

async def gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gender = query.data.replace("gender_", "")
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user:
            user.gender = gender
            session.commit()
            emoji = {"male": "👨", "female": "👩", "trans": "⚧", "nonbinary": "🌈"}.get(gender, "❓")
            await query.edit_message_text(f"{emoji} Gender: {gender.capitalize()}\n\nUse /outfit to set clothing.")
    finally:
        session.close()

async def variant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🍑 Vagina", callback_data="variant_vagina")], [InlineKeyboardButton("🍆 Penis", callback_data="variant_penis")]]
    await update.message.reply_text("What's she packing?", reply_markup=InlineKeyboardMarkup(keyboard))

async def variant_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    variant_key = query.data.replace("variant_", "")
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await query.edit_message_text("Use /start first")
            return
        user.harley_variant = variant_key if variant_key in HARLEY_VARIANTS else "vagina"
        session.commit()
        await query.edit_message_text(f"Noted, puddin'. {HARLEY_VARIANTS[user.harley_variant]['label']}. Now use /avatar.")
    finally:
        session.close()

async def outfit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user or not user.gender:
            await update.message.reply_text("Set gender first with /gender")
            return
        keyboard = [[InlineKeyboardButton(f"{o['emoji']} {o['name']}", callback_data=f"outfit_{k}")] for k, o in OUTFIT_OPTIONS.items()]
        current = user.outfit_detail or user.current_outfit or "Not set"
        await update.message.reply_text(f"Current: {current}\n\nWhat are you wearing?", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        session.close()

async def outfit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    outfit_key = query.data.replace("outfit_", "")
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        if outfit_key == "custom":
            user.awaiting_custom_outfit = True
            session.commit()
            await query.edit_message_text("Describe your outfit:")
            return
        outfit = OUTFIT_OPTIONS[outfit_key]
        items = outfit.get(f"{user.gender}_items", outfit["male_items"])
        user.current_outfit = outfit["name"]
        user.outfit_items = json.dumps(items)
        user.outfit_detail = None
        user.awaiting_outfit_detail = True
        session.commit()
        await query.edit_message_text(f"{outfit['emoji']} {outfit['name']} - roughly.\n\nWhat are you actually in? Be specific, it changes what I can ask for. Type it, or /skip.")
    finally:
        session.close()

async def handle_custom_outfit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        user.awaiting_custom_outfit = False
        user.awaiting_outfit_detail = False
        custom = update.message.text.strip()
        user.current_outfit = f"Custom: {custom[:50]}"
        user.outfit_detail = custom[:300]
        items = [i.strip() for i in custom.split(',')]
        user.outfit_items = json.dumps(items[:6])
        session.commit()
        await update.message.reply_text("✅ Outfit saved. Use /wherenow!")
    finally:
        session.close()

async def handle_outfit_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        user.awaiting_outfit_detail = False
        user.outfit_detail = update.message.text.strip()[:300]
        session.commit()
        await update.message.reply_text("Noted. Use /wherenow.")
    finally:
        session.close()

async def wherenow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏪 Store", callback_data="ctx_store")], [InlineKeyboardButton("🏠 Home", callback_data="ctx_home")], [InlineKeyboardButton("🏨 Hotel", callback_data="ctx_hotel")], [InlineKeyboardButton("🍸 Bar", callback_data="ctx_bar")], [InlineKeyboardButton("🚗 Vehicle", callback_data="ctx_car")], [InlineKeyboardButton("🏢 Work", callback_data="ctx_work")], [InlineKeyboardButton("✏️ Custom", callback_data="ctx_custom")]]
    await update.message.reply_text("📍 Where are you?", reply_markup=InlineKeyboardMarkup(keyboard))

async def context_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    ctx_type = query.data.replace("ctx_", "")
    if ctx_type == "custom":
        session = get_session()
        try:
            user = session.query(UserState).filter_by(user_id=user_id).first()
            if user:
                user.awaiting_custom_location = True
                session.commit()
                await query.edit_message_text("Describe exactly where you are:")
        finally:
            session.close()
        return
    loc_map = {"store": "Retail Store", "home": "Home", "hotel": "Hotel", "bar": "Bar", "car": "Vehicle", "work": "Work"}
    location = loc_map.get(ctx_type, "Unknown")
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user:
            user.location = location
            user.custom_location = location
            user.location_detail = None
            user.location_probes = None
            session.commit()
    finally:
        session.close()
    keyboard = [[InlineKeyboardButton("🧍 Alone", callback_data="present_alone")], [InlineKeyboardButton("💑 Partner", callback_data="present_partner")], [InlineKeyboardButton("🏠 Roommates", callback_data="present_roommates")], [InlineKeyboardButton("👨‍👩‍👧 Family", callback_data="present_parents")], [InlineKeyboardButton("👶 Kids", callback_data="present_kids")], [InlineKeyboardButton("👥 Public", callback_data="present_public")]]
    await query.edit_message_text(f"📍 {location}\n\nWho is present?", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_custom_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        user.awaiting_custom_location = False
        user.custom_location = update.message.text.strip()[:100]
        user.location = "Custom"
        user.location_detail = None
        user.location_probes = None
        session.commit()
        keyboard = [[InlineKeyboardButton("🧍 Alone", callback_data="present_alone")], [InlineKeyboardButton("💑 Partner", callback_data="present_partner")], [InlineKeyboardButton("🏠 Roommates", callback_data="present_roommates")], [InlineKeyboardButton("👥 Public", callback_data="present_public")]]
        await update.message.reply_text(f"📍 {user.custom_location}\n\nWho is present?", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        session.close()

async def present_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    present_type = query.data.replace("present_", "")
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user:
            user.context_others = present_type
            user.context_alone = (present_type == "alone")
            if present_type == "kids" and user.risk_level > 2:
                user.risk_level = 2
            session.commit()
    finally:
        session.close()
    keyboard = [[InlineKeyboardButton("🔒 Private", callback_data="privacy_private")], [InlineKeyboardButton("🚪 Semi-private", callback_data="privacy_semi")], [InlineKeyboardButton("👁️ Exposed", callback_data="privacy_exposed")], [InlineKeyboardButton("🌍 Public", callback_data="privacy_public")]]
    ctx_name = CONTEXT_OPTIONS.get(present_type, {}).get("name", present_type)
    await query.edit_message_text(f"👥 {ctx_name}\n\nPrivacy level?", reply_markup=InlineKeyboardMarkup(keyboard))

async def privacy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    privacy = query.data.replace("privacy_", "")
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await query.edit_message_text("Use /start first")
            return
        user.context_privacy = privacy
        user.awaiting_location_detail = True
        location = user.custom_location or user.location or "room"
        probes = probes_for(user) or baseline_chips(location)
        user.location_probes = json.dumps(probes)
        session.commit()
        await query.edit_message_text(
            f"Last thing - anything about {location} I should know?\n\n"
            f"What's within reach, how busy it is, anything you could grab. Tick what's there, or /skip.",
            reply_markup=probe_keyboard(probes, [])
        )
        extra = await generate_probes(location)
        if extra:
            seen = {p.lower() for p in probes}
            merged = probes + [p for p in extra if p.lower() not in seen]
            user.location_probes = json.dumps(merged[:16])
            session.commit()
            try:
                await query.edit_message_reply_markup(reply_markup=probe_keyboard(merged[:16], []))
            except Exception as e:
                logger.warning(f"Could not refresh probe keyboard: {e}")
    finally:
        session.close()

async def handle_location_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        user.awaiting_location_detail = False
        text = update.message.text.strip()[:400]
        user.location_detail = f"{user.location_detail}, {text}" if user.location_detail else text
        session.commit()
        await update.message.reply_text("Good. That's enough to work with.\n\nUse /task.")
    finally:
        session.close()

# ============ VENUE CHIPS ============
def probe_keyboard(probes, selected):
    keyboard = []
    for i, label in enumerate(probes):
        mark = "✅ " if label in selected else ""
        short = label if len(label) <= 28 else label[:27].rstrip() + "…"
        keyboard.append([InlineKeyboardButton(f"{mark}{short}", callback_data=f"probe_{i}")])
    keyboard.append([InlineKeyboardButton("✔️ That's all", callback_data="probe_done")])
    return InlineKeyboardMarkup(keyboard)

async def probe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await query.edit_message_text("Use /start first")
            return
        location = user.custom_location or user.location or ""
        if query.data == "probe_done":
            user.awaiting_location_detail = False
            session.commit()
            await query.edit_message_text("Good - that gives me something to work with.\n\nUse /kinks if you haven't set preferences, then /task.")
            return
        probes = probes_for(user)
        try:
            idx = int(query.data.replace("probe_", ""))
        except ValueError:
            return
        if idx >= len(probes):
            return
        token = probes[idx]
        detail = user.location_detail or ""
        if token.lower() in detail.lower():
            user.location_detail = re.sub(re.escape(token), "", detail, flags=re.IGNORECASE).strip(", ") or None
        else:
            user.location_detail = f"{detail}, {token}" if detail else token
        session.commit()
        current = [p for p in probes if p.lower() in (user.location_detail or "").lower()]
        await query.edit_message_text(
            f"Noted. Anything else about {location or 'this place'}, or /skip when you're done.",
            reply_markup=probe_keyboard(probes, current)
        )
    finally:
        session.close()

async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"{i}️⃣ {RISK_LEVELS[i]['name']}", callback_data=f"risk_{i}")] for i in range(1, 6)]
    await update.message.reply_text("Select risk:", reply_markup=InlineKeyboardMarkup(keyboard))

async def risk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    risk = int(query.data.replace("risk_", ""))
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user:
            user.risk_level = risk
            session.commit()
            await query.edit_message_text(f"Risk: {RISK_LEVELS[risk]['name']}")
    finally:
        session.close()

async def kinks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await update.message.reply_text("Use /start first")
            return
        keyboard = []
        for kink_key, (name, desc) in KINK_CATEGORIES.items():
            current_level = getattr(user, kink_key, "okay")
            emoji = KINK_LEVELS[current_level]["emoji"]
            keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"kinkmenu_{kink_key}")])
        keyboard.append([InlineKeyboardButton("🔙 Done", callback_data="kinks_done")])
        await update.message.reply_text("🎭 Kink Preferences\n\n❌ = No (hard limit)\n⭕ = Okay (Dom may use)\n✅ = Yes (desired/favorite)\n\nTap a kink to cycle through options:", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        session.close()

async def kink_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kink_key = query.data.replace("kinkmenu_", "")
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        current = getattr(user, kink_key, "okay")
        name = KINK_CATEGORIES[kink_key][0]
        desc = KINK_CATEGORIES[kink_key][1]
        keyboard = [[InlineKeyboardButton(f"{'✅' if current == 'no' else ''} ❌ No (Never)", callback_data=f"kinkset_{kink_key}_no")], [InlineKeyboardButton(f"{'✅' if current == 'okay' else ''} ⭕ Okay (Dom decides)", callback_data=f"kinkset_{kink_key}_okay")], [InlineKeyboardButton(f"{'✅' if current == 'yes' else ''} ✅ Yes (Desired!)", callback_data=f"kinkset_{kink_key}_yes")], [InlineKeyboardButton("🔙 Back", callback_data="kinks_back")]]
        await query.edit_message_text(f"{name}\n\n{desc}\n\nCurrent: {KINK_LEVELS[current]['emoji']} {KINK_LEVELS[current]['name']}\n\nSelect new level:", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        session.close()

async def kink_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.replace("kinkset_", "").rsplit("_", 1)
    kink_key = data[0]
    level = data[1]
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user and hasattr(user, kink_key):
            setattr(user, kink_key, level)
            session.commit()
            name = KINK_CATEGORIES[kink_key][0]
            await query.edit_message_text(f"✅ {name} set to {KINK_LEVELS[level]['emoji']} {KINK_LEVELS[level]['name']}")
            await asyncio.sleep(0.5)
            await kinks_back_callback(update, context)
    finally:
        session.close()

async def kinks_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await query.edit_message_text("Use /start first")
            return
        keyboard = []
        for kink_key, (name, desc) in KINK_CATEGORIES.items():
            current_level = getattr(user, kink_key, "okay")
            emoji = KINK_LEVELS[current_level]["emoji"]
            keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"kinkmenu_{kink_key}")])
        keyboard.append([InlineKeyboardButton("🔙 Done", callback_data="kinks_done")])
        await query.edit_message_text("🎭 Kink Preferences\n\n❌ = No (hard limit)\n⭕ = Okay (Dom may use)\n✅ = Yes (desired/favorite)\n\nTap a kink to cycle through options:", reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        session.close()

async def kinks_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Kinks updated! Use /task to get challenges.")

async def interval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await update.message.reply_text("Use /start first")
            return
        user.awaiting_interval = True
        session.commit()
        await update.message.reply_text(f"⏱️ Auto-Task Interval\n\nCurrent: {user.min_interval}-{user.max_interval} minutes\n\nSend new interval as: MIN MAX\nExamples: '30 60' or '60 120'\n\nSend 'cancel' to keep current.")
    finally:
        session.close()

async def handle_interval_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        text = update.message.text.strip().lower()
        if text == 'cancel':
            user.awaiting_interval = False
            session.commit()
            await update.message.reply_text("Interval unchanged.")
            return
        try:
            parts = text.split()
            if len(parts) != 2:
                raise ValueError("Need 2 numbers")
            min_int = int(parts[0])
            max_int = int(parts[1])
            if min_int < 5 or max_int > 1440 or min_int >= max_int:
                await update.message.reply_text("❌ Invalid range. Min 5-1440, max > min.")
                return
            user.min_interval = min_int
            user.max_interval = max_int
            user.awaiting_interval = False
            session.commit()
            await update.message.reply_text(f"✅ Interval: {min_int}-{max_int} minutes")
        except ValueError:
            await update.message.reply_text("❌ Invalid format. Send as: MIN MAX")
    finally:
        session.close()

async def task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.pop('blocked_task_id', None)
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await update.message.reply_text("Use /start first")
            return
        user.awaiting_location_detail = False
        user.awaiting_outfit_detail = False
        session.commit()
        if not user.gender:
            await gender_cmd(update, context)
            return
        if not user.current_outfit:
            await outfit_cmd(update, context)
            return
        if not user.custom_location:
            await wherenow_cmd(update, context)
            return
        active = session.query(Task).filter_by(user_id=user_id, status="pending").first()
        if active:
            time_left = (active.expires_at - datetime.now(timezone.utc)).total_seconds() / 60
            await update.message.reply_text(f"You have a task pending! {max(0, time_left):.0f} minutes left.")
            return
        await update.message.reply_text("Generating your task...")
        if random.random() < 0.3:
            avatar_bytes = await generate_avatar_pose(user, "dominant")
            if avatar_bytes:
                await send_avatar_photo(context, user_id, avatar_bytes, "Your task awaits...")
                await asyncio.sleep(1)
        task_text, actual_risk = await generate_task_text(user, session)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=30)
        task = Task(user_id=user_id, task_text=task_text, risk_level=actual_risk, created_at=now, expires_at=expires)
        session.add(task)
        session.commit()
        context.job_queue.run_once(auto_clear_task, when=expires, data={'user_id': user_id, 'task_id': task.id}, name=f"timeout_{task.id}")
        await update.message.reply_text(f"🎯 TASK\n\n{task_text}\n\n30 minutes.", reply_markup=task_keyboard(task.id, get_title()))
    finally:
        session.close()

async def blocked_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.replace("blocked_", ""))
    context.user_data['blocked_task_id'] = task_id
    await query.edit_message_text("What's in the way? Tell me and I'll rework it around that.")

async def handle_blocker_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task_id = context.user_data.get('blocked_task_id')
    session = get_session()
    try:
        task = session.query(Task).filter_by(id=task_id, user_id=user_id, status="pending").first() if task_id else None
        if not task:
            context.user_data.pop('blocked_task_id', None)
            await update.message.reply_text("That task's gone. Use /task.")
            return
        if task.blocked_count >= 3:
            await update.message.reply_text("Three reworks already, puddin'. Use Mercy if it still won't work.")
            return
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        context.user_data.pop('blocked_task_id', None)
        constraint_text = update.message.text.strip()[:200]
        location = user.custom_location or user.location or "Unknown"
        norm = constraint_text.lower().strip().rstrip('.')
        existing = None
        for c in session.query(LocationConstraint).filter_by(user_id=user_id, location=location).all():
            if c.constraint.lower().strip().rstrip('.') == norm:
                existing = c
                break
        now = datetime.now(timezone.utc)
        if existing:
            existing.times_hit += 1
            existing.expires_at = now + timedelta(days=30 if existing.times_hit >= 2 else 3)
        else:
            session.add(LocationConstraint(user_id=user_id, location=location, constraint=constraint_text, expires_at=now + timedelta(hours=3)))
        session.commit()
        await update.message.reply_text("Fair. Reworking it - same difficulty.")
        task.blocked_count += 1
        new_text, actual_risk = await generate_task_text(user, session)
        task.task_text = new_text
        task.risk_level = actual_risk
        task.verification_attempts = 0
        task.followup_target = None
        task.expires_at = now + timedelta(minutes=30)
        session.commit()
        try:
            context.job_queue.run_once(auto_clear_task, when=timedelta(minutes=30), data={'user_id': user_id, 'task_id': task.id}, name=f"timeout_{task.id}")
        except Exception as e:
            logger.warning(f"Could not re-arm blocked timer: {e}")
        await context.bot.send_message(chat_id=user_id, text=f"🎯 REWORKED\n\n{new_text}\n\n30 minutes.", reply_markup=task_keyboard(task.id, get_title()))
    finally:
        session.close()

async def mercy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.replace("mercy_", ""))
    user_id = update.effective_user.id
    context.user_data.pop('blocked_task_id', None)
    session = get_session()
    try:
        task = session.query(Task).filter_by(id=task_id, user_id=user_id, status="pending").first()
        if not task:
            await query.edit_message_text("No active task.")
            return
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await query.edit_message_text("Use /start first")
            return
        await query.edit_message_text("Mercy granted, puddin'. Let me find somethin' else...")
        new_task_text, actual_risk = await generate_task_text(user, session)
        now = datetime.now(timezone.utc)
        task.task_text = new_task_text
        task.risk_level = actual_risk
        task.verification_attempts = 0
        task.followup_target = None
        task.expires_at = now + timedelta(minutes=30)
        session.commit()
        try:
            context.job_queue.run_once(auto_clear_task, when=timedelta(minutes=30), data={'user_id': user_id, 'task_id': task.id}, name=f"timeout_{task.id}")
        except Exception as e:
            logger.warning(f"Could not re-arm mercy timer: {e}")
        await context.bot.send_message(chat_id=user_id, text=f"🔄 NEW TASK (Mercy)\n\n{new_task_text}\n\n30 minutes.", reply_markup=task_keyboard(task.id, get_title()))
    finally:
        session.close()

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await update.message.reply_text("Use /start first")
            return
        active = session.query(Task).filter_by(user_id=user_id, status="pending").first()
        if active:
            time_left = (active.expires_at - datetime.now(timezone.utc)).total_seconds() / 60
            await update.message.reply_text(f"⏰ {max(0, time_left):.0f} min left\n\n{active.task_text[:200]}...", reply_markup=task_keyboard(active.id, get_title()))
        else:
            loc = user.custom_location or user.location or "Not set"
            interval = f"{user.min_interval}-{user.max_interval} min" if user.scheduling_enabled else "Off"
            yes_kinks = len(get_user_kinks(user, "yes"))
            okay_kinks = len(get_user_kinks(user, "okay"))
            await update.message.reply_text(f"No active task.\n\n📍 {loc} | Points: {user.points} | Streak: {user.streak}\nKinks: {yes_kinks} desired, {okay_kinks} allowed\nAuto: {interval}\n\nUse /task!")
    finally:
        session.close()

async def rewards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await update.message.reply_text("Use /start first")
            return
        await update.message.reply_text(f"🎁 Rewards unlock every 5-10 tasks completed.\n\nCurrent streak: {user.streak}\nTotal completed: {user.completed_tasks}\n\nKeep completing tasks to earn rewards!")
    finally:
        session.close()

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        task = session.query(Task).filter_by(user_id=user_id, status="pending").first()
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        await update.message.reply_text("Checking your photo...")
        photo_bytes = await file.download_as_bytearray()
        if len(photo_bytes) < 1000:
            await update.message.reply_text("Photo too small. Send clearer photo.")
            return
        cloudinary_result = await upload_to_cloudinary(photo_bytes, user_id, image_type='verification' if task else 'user_upload')
        session.add(UserImage(user_id=user_id, telegram_file_id=photo.file_id, cloudinary_url=cloudinary_result['url'] if cloudinary_result else None, cloudinary_public_id=cloudinary_result['public_id'] if cloudinary_result else None, image_type='verification' if task else 'user_upload'))
        session.commit()
        if not task:
            msg = "📸 Image saved to your collection."
            if cloudinary_result:
                msg += "\n☁️ Backed up to cloud."
            await update.message.reply_text(msg)
            return
        now = datetime.now(timezone.utc)
        if now > task.expires_at:
            task.status = "expired"
            session.commit()
            await update.message.reply_text("Task expired. Use /task.")
            return
        verification = await analyze_image(photo_bytes, task.task_text, task.followup_target)
        if verification is None:
            await update.message.reply_text("Couldn't reach the verifier just now - resend it, no attempt used.")
            return
        verified, missing, reason = parse_verification(verification)
        task.verification_attempts += 1
        if cloudinary_result:
            task.cloudinary_url = cloudinary_result['url']
            task.cloudinary_public_id = cloudinary_result['public_id']
        if verified == "yes":
            task.status = "completed"
            task.completed_at = now
            task.photo_url = photo.file_id
            task.followup_target = None
            context.user_data.pop('blocked_task_id', None)
            user = session.query(UserState).filter_by(user_id=user_id).first()
            if not user:
                await update.message.reply_text("Your profile is missing. Use /start.")
                return
            user.points += 10 * task.risk_level
            user.streak += 1
            user.completed_tasks += 1
            user.consecutive_failures = 0
            user.challenges_since_reward += 1
            session.commit()
            if user.challenges_since_reward >= random.randint(5, 10):
                user.challenges_since_reward = 0
                user.awaiting_reward_wish = True
                session.commit()
                await update.message.reply_text(
                    f"✅ +{10 * task.risk_level} points! Streak: {user.streak}\n\n"
                    f"🎁 You earned a reward, puddin'. What do you wanna see?",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Surprise me", callback_data="reward_surprise")]])
                )
            else:
                await update.message.reply_text(f"✅ +{10 * task.risk_level} points! Streak: {user.streak}")
        else:
            if task.verification_attempts >= 3:
                task.status = "failed"
                user = session.query(UserState).filter_by(user_id=user_id).first()
                if user:
                    user.consecutive_failures += 1
                session.commit()
                await update.message.reply_text(f"❌ Failed. {reason}")
            else:
                task.followup_target = missing
                session.commit()
                remaining = 3 - task.verification_attempts
                if missing:
                    text = f"Not quite, puddin'. I couldn't see {missing}.\n\nShow me {missing} - just that, nothin' else."
                else:
                    text = f"Not quite. {reason}\n\nTry once more."
                keyboard = [[InlineKeyboardButton("🔄 Try Again", callback_data=f"retry_{task.id}")], [InlineKeyboardButton("❌ Give Up", callback_data=f"giveup_{task.id}")]]
                await update.message.reply_text(f"{text}\n\n{remaining} {'try' if remaining == 1 else 'tries'} left.", reply_markup=InlineKeyboardMarkup(keyboard))
        session.commit()
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text("Error processing photo.")
    finally:
        session.close()

async def handle_reward_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        wish = update.message.text.strip()[:200]
        user.awaiting_reward_wish = False
        session.commit()
        await update.message.reply_text("Comin' right up...")
        await deliver_reward(context, user, wish, session)
    finally:
        session.close()

async def reward_surprise_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            return
        user.awaiting_reward_wish = False
        session.commit()
        await query.edit_message_text("Comin' right up...")
        await deliver_reward(context, user, None, session)
    finally:
        session.close()

async def complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📸 Send photo proof.")

async def giveup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.replace("giveup_", ""))
    user_id = update.effective_user.id
    context.user_data.pop('blocked_task_id', None)
    session = get_session()
    try:
        task = session.query(Task).filter_by(id=task_id, user_id=user_id).first()
        if task and task.status == "pending":
            task.status = "failed"
            user = session.query(UserState).filter_by(user_id=user_id).first()
            if user:
                user.points = max(0, user.points - 5)
                user.streak = 0
            session.commit()
            await query.edit_message_text("❌ Task abandoned. -5 points.")
    finally:
        session.close()

async def retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📸 Send retry photo.")

async def auto_clear_task(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data['user_id']
    task_id = job_data['task_id']
    session = get_session()
    try:
        task = session.query(Task).filter_by(id=task_id).first()
        if not task or task.status != "pending":
            return
        task.status = "expired"
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if user:
            user.points = max(0, user.points - 10)
            user.streak = 0
            user.consecutive_failures += 1
            session.commit()
            try:
                await context.bot.send_message(chat_id=user_id, text="⏰ Task expired. -10 points.")
            except Exception as e:
                logger.error(f"Notify error: {e}")
        session.commit()
    except Exception as e:
        logger.error(f"Auto-clear error: {e}")
    finally:
        session.close()

# ============ AVATAR COMMANDS ============
async def avatar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await update.message.reply_text("Use /start first")
            return
        await update.message.reply_text("Generating...")
        image_bytes = await generate_avatar_pose(user, "dominant")
        if not image_bytes:
            await update.message.reply_text("❌ Couldn't generate. Check the log.")
            return
        cloudinary_result = await upload_to_cloudinary(image_bytes, user_id, image_type='avatar')
        session.add(AvatarImage(user_id=user_id, image_url="generated", cloudinary_url=cloudinary_result['url'] if cloudinary_result else None, cloudinary_public_id=cloudinary_result['public_id'] if cloudinary_result else None))
        user.last_avatar_sent_at = datetime.now(timezone.utc)
        session.commit()
        await send_avatar_photo(context, user_id, image_bytes, f"🎨 Harley - {HARLEY_VARIANTS[user.harley_variant]['label']}")
    finally:
        session.close()

async def debugavatar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paired task and reward shot, raw and normalized, as documents plus a photo."""
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        variant = harley_variant(user) if user else HARLEY_VARIANTS["vagina"]
    finally:
        session.close()
    await update.message.reply_text(
        f"Debug pair - task then reward. Task model {IMAGE_MODEL}, reward model {IMAGE_MODEL_REWARD or 'same'}, "
        f"variant {variant['label']}, cap {min(int(IMAGE_MAX_SIDE), 1280)}px, cfg {IMAGE_CFG}, steps {IMAGE_STEPS}, "
        f"upscale {IMAGE_UPSCALE}/{IMAGE_UPSCALE_CREATIVITY}, min sharpness {MIN_SHARPNESS}."
    )
    dom = (f"Raw editorial photograph of {HARLEY_BASE}, {variant['body']}{variant_phrase(variant)}, "
           f"wearing {random.choice(HARLEY_OUTFITS)}, {random.choice(HARLEY_POSES)}, "
           f"framed from mid-thigh up, {STUDIO}, {SHARP_TAIL}")
    await _debug_send(context, user_id, "TASK", dom, NEGATIVE_PROMPT_SEXY + (variant.get("clothed_negative") or ""), upscale=False)
    await asyncio.sleep(2)
    await _debug_send(context, user_id, "REWARD", reward_prompt(variant), reward_negative(variant), reward_chain(), upscale=True)

async def testreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force a reward shot without completing anything. Optional wish text after the command."""
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        variant = harley_variant(user) if user else HARLEY_VARIANTS["vagina"]
    finally:
        session.close()
    wish = " ".join(context.args).strip()[:120] if context.args else None
    await update.message.reply_text(f"Reward test - {wish or 'surprise'}...")
    await _debug_send(context, user_id, "REWARD", reward_prompt(variant, wish), reward_negative(variant), reward_chain(), upscale=True)

async def imgmodels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dump the live image catalogue with each model's caps, so a swap is a lookup."""
    try:
        r = await asyncio.to_thread(
            requests.get, f"{VENICE_API_URL}/models?type=image",
            headers={"Authorization": f"Bearer {VENICE_API_KEY}"}, timeout=30)
    except Exception as e:
        await update.message.reply_text(f"Fetch failed: {e}")
        return
    if r.status_code != 200:
        await update.message.reply_text(f"HTTP {r.status_code}: {r.text[:200]}")
        return
    payload = r.json()
    rows = payload.get("data") or payload
    lines = []
    for m in rows:
        c = (m.get("model_spec") or {}).get("constraints") or {}
        res = c.get("resolutions") or ([c.get("defaultResolution")] if c.get("defaultResolution") else [])
        steps = (c.get("steps") or {}).get("max")
        lines.append(f"{m.get('id')} | res {res or '-'} | max {c.get('maxWidth')}x{c.get('maxHeight')} | steps {steps}")
    if not lines:
        await update.message.reply_text("Empty list.")
        return
    for i in range(0, len(lines), 25):
        await update.message.reply_text("\n".join(lines[i:i + 25]))

async def tryreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fire one reward at a named model without touching the defaults."""
    if not context.args:
        await update.message.reply_text("usage: /tryreward <model-id> [wish]")
        return
    model = context.args[0]
    wish = " ".join(context.args[1:]).strip()[:120] or None
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=update.effective_user.id).first()
        variant = harley_variant(user) if user else HARLEY_VARIANTS["vagina"]
    finally:
        session.close()
    await update.message.reply_text(f"Trying {model}...")
    await _debug_send(
        context, update.effective_user.id, "TRY", reward_prompt(variant, wish),
        reward_negative(variant), [model], upscale=False,
    )

MODEL_CHOICES = ["grok-imagine-image-quality", "grok-imagine-image-2-0", "nano-banana-pro", "chroma", "z-image-turbo"]

async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(m, callback_data=f"setmodel_{m}")] for m in MODEL_CHOICES]
    await update.message.reply_text(f"Task model: {IMAGE_MODEL}\nReward model: {IMAGE_MODEL_REWARD or 'same'}\n\nPick one, then /debugavatar to test.", reply_markup=InlineKeyboardMarkup(keyboard))

async def setmodel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IMAGE_MODEL
    query = update.callback_query
    await query.answer()
    m = query.data.replace("setmodel_", "")
    if m not in MODEL_CHOICES:
        return
    IMAGE_MODEL = m
    await asyncio.to_thread(get_spec, m)
    await query.edit_message_text(f"Task model: {m}. /debugavatar to test.\n\ngrok = sharpest, R-rated, hides genitals. nano-banana = sharp, rejects explicit. chroma = explicit but soft, last resort. z-image = fast.")

async def rewardmodel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(m, callback_data=f"setreward_{m}")] for m in MODEL_CHOICES]
    keyboard.append([InlineKeyboardButton("same as task", callback_data="setreward_same")])
    await update.message.reply_text(
        f"Reward model: {IMAGE_MODEL_REWARD or 'same'}\n\nPick one, then /testreward.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def setreward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IMAGE_MODEL_REWARD
    query = update.callback_query
    await query.answer()
    m = query.data.replace("setreward_", "")
    IMAGE_MODEL_REWARD = "" if m == "same" else m
    if IMAGE_MODEL_REWARD:
        await asyncio.to_thread(get_spec, IMAGE_MODEL_REWARD)
    await query.edit_message_text(f"Reward model: {IMAGE_MODEL_REWARD or 'same'}. /testreward to check.")

# ============ SCHEDULER ============
async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("▶️ Enable", callback_data="sched_enable")], [InlineKeyboardButton("⏹️ Disable", callback_data="sched_disable")]]
    await update.message.reply_text("Auto-Task Scheduler:", reply_markup=InlineKeyboardMarkup(keyboard))

async def schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    session = get_session()
    try:
        user = session.query(UserState).filter_by(user_id=user_id).first()
        if not user:
            await query.edit_message_text("Use /start first")
            return
        if query.data == "sched_enable":
            user.scheduling_enabled = True
            user.next_task_time = datetime.now(timezone.utc) + timedelta(minutes=random.randint(user.min_interval, user.max_interval))
            session.commit()
            await query.edit_message_text("✅ Auto-tasks enabled!")
        elif query.data == "sched_disable":
            user.scheduling_enabled = False
            user.next_task_time = None
            session.commit()
            await query.edit_message_text("⏹️ Auto-tasks disabled.")
    finally:
        session.close()

async def scheduled_task_check(context: ContextTypes.DEFAULT_TYPE):
    session = get_session()
    try:
        now = datetime.now(timezone.utc)
        session.query(LocationConstraint).filter(LocationConstraint.expires_at < now).delete()
        session.commit()
        users = session.query(UserState).filter(
            UserState.scheduling_enabled == True,
            or_(UserState.next_task_time.is_(None), UserState.next_task_time <= now)
        ).all()
        for user in users:
            active = session.query(Task).filter_by(user_id=user.user_id, status="pending").first()
            if not active:
                try:
                    if random.random() < 0.3:
                        avatar_bytes = await generate_avatar_pose(user, "dominant")
                        if avatar_bytes:
                            await send_avatar_photo(context, user.user_id, avatar_bytes, "Your task awaits...")
                            await asyncio.sleep(1)
                    task_text, actual_risk = await generate_task_text(user, session)
                    expires = now + timedelta(minutes=30)
                    task = Task(user_id=user.user_id, task_text=task_text, risk_level=actual_risk, created_at=now, expires_at=expires)
                    session.add(task)
                    session.commit()
                    context.job_queue.run_once(auto_clear_task, when=expires, data={'user_id': user.user_id, 'task_id': task.id}, name=f"timeout_{task.id}")
                    await context.bot.send_message(chat_id=user.user_id, text=f"🎯 AUTO TASK\n\n{task_text}\n\n30 minutes.", reply_markup=task_keyboard(task.id, get_title()))
                    user.next_task_time = now + timedelta(minutes=random.randint(user.min_interval, user.max_interval))
                    session.commit()
                except Exception as e:
                    logger.error(f"Auto-task error: {e}")
    except Exception as e:
        logger.error(f"Scheduled check error: {e}")
    finally:
        session.close()

# ============ RESET ============
async def resetowner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("⚠️ YES DELETE ALL", callback_data="reset_confirm")], [InlineKeyboardButton("❌ Cancel", callback_data="reset_cancel")]]
    await update.message.reply_text("⚠️ DELETE ALL DATA?", reply_markup=InlineKeyboardMarkup(keyboard))

async def reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "reset_cancel":
        await query.edit_message_text("Reset cancelled.")
        return
    user_id = update.effective_user.id
    session = get_session()
    try:
        session.query(LocationConstraint).filter_by(user_id=user_id).delete()
        session.query(Task).filter_by(user_id=user_id).delete()
        session.query(TaskHistory).filter_by(user_id=user_id).delete()
        session.query(AvatarImage).filter_by(user_id=user_id).delete()
        session.query(UserImage).filter_by(user_id=user_id).delete()
        session.query(UserState).filter_by(user_id=user_id).delete()
        session.commit()
        await query.edit_message_text("✅ All data deleted. Send /start.")
    except Exception as e:
        logger.error(f"Reset error: {e}")
        await query.edit_message_text("Error.")
    finally:
        session.close()

# ============ MAIN ============
def main():
    logger.info("=" * 60)
    logger.info("BOT STARTING")
    logger.info(f"Text: {TEXT_MODEL} | Vision: {VISION_MODEL} | Probes: {PROBE_MODELS}")
    logger.info(f"Task model: {IMAGE_MODEL} | Reward model: {IMAGE_MODEL_REWARD or 'same'} | fallbacks: {IMAGE_FALLBACKS}")
    logger.info(f"Format: {IMAGE_FORMAT} | Upscale: {IMAGE_UPSCALE}/{IMAGE_UPSCALE_CREATIVITY} | Aspect: {IMAGE_ASPECT} | Res: {IMAGE_RESOLUTION} | Sharpen: {IMAGE_SHARPEN} | MaxSide: {IMAGE_MAX_SIDE} | Steps: {IMAGE_STEPS} | CFG: {IMAGE_CFG} | MinSharp: {MIN_SHARPNESS}")
    logger.info(f"Native caps: {NATIVE_CAPS}")
    logger.info(f"Venue presets: {len(VENUE_BASELINE)}")
    logger.info(f"Cloudinary configured: {bool(os.getenv('CLOUDINARY_CLOUD_NAME'))} / {bool(os.getenv('CLOUDINARY_API_KEY'))} / {bool(os.getenv('CLOUDINARY_API_SECRET'))}")
    logger.info("=" * 60)
    load_model_spec()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.job_queue.run_repeating(
        scheduled_task_check, interval=60, first=10, name="scheduled_check",
        job_kwargs={"misfire_grace_time": 180, "coalesce": True, "max_instances": 1}
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("payload", payload_cmd))
    application.add_handler(CommandHandler("test", test_cmd))
    application.add_handler(CommandHandler("skip", skip_cmd))
    application.add_handler(CommandHandler("debugavatar", debugavatar_cmd))
    application.add_handler(CommandHandler("testreward", testreward_cmd))
    application.add_handler(CommandHandler("imgmodels", imgmodels_cmd))
    application.add_handler(CommandHandler("tryreward", tryreward_cmd))
    application.add_handler(CommandHandler("model", model_cmd))
    application.add_handler(CommandHandler("rewardmodel", rewardmodel_cmd))
    application.add_handler(CommandHandler("gender", gender_cmd))
    application.add_handler(CommandHandler("outfit", outfit_cmd))
    application.add_handler(CommandHandler("wherenow", wherenow_cmd))
    application.add_handler(CommandHandler("risk", risk_cmd))
    application.add_handler(CommandHandler("kinks", kinks_cmd))
    application.add_handler(CommandHandler("task", task_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("schedule", schedule_cmd))
    application.add_handler(CommandHandler("interval", interval_cmd))
    application.add_handler(CommandHandler("avatar", avatar_cmd))
    application.add_handler(CommandHandler("variant", variant_cmd))
    application.add_handler(CommandHandler("resetowner", resetowner_cmd))
    application.add_handler(CommandHandler("rewards", rewards_cmd))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CallbackQueryHandler(gender_callback, pattern="^gender_"))
    application.add_handler(CallbackQueryHandler(variant_callback, pattern="^variant_"))
    application.add_handler(CallbackQueryHandler(outfit_callback, pattern="^outfit_"))
    application.add_handler(CallbackQueryHandler(context_callback, pattern="^ctx_"))
    application.add_handler(CallbackQueryHandler(present_callback, pattern="^present_"))
    application.add_handler(CallbackQueryHandler(privacy_callback, pattern="^privacy_"))
    application.add_handler(CallbackQueryHandler(probe_callback, pattern="^probe_"))
    application.add_handler(CallbackQueryHandler(risk_callback, pattern="^risk_"))
    application.add_handler(CallbackQueryHandler(kink_menu_callback, pattern="^kinkmenu_"))
    application.add_handler(CallbackQueryHandler(kink_set_callback, pattern="^kinkset_"))
    application.add_handler(CallbackQueryHandler(kinks_back_callback, pattern="^kinks_back"))
    application.add_handler(CallbackQueryHandler(kinks_done_callback, pattern="^kinks_done"))
    application.add_handler(CallbackQueryHandler(complete_callback, pattern="^complete_"))
    application.add_handler(CallbackQueryHandler(blocked_callback, pattern="^blocked_"))
    application.add_handler(CallbackQueryHandler(giveup_callback, pattern="^giveup_"))
    application.add_handler(CallbackQueryHandler(retry_callback, pattern="^retry_"))
    application.add_handler(CallbackQueryHandler(schedule_callback, pattern="^sched_"))
    application.add_handler(CallbackQueryHandler(reset_callback, pattern="^reset_"))
    application.add_handler(CallbackQueryHandler(mercy_callback, pattern="^mercy_"))
    application.add_handler(CallbackQueryHandler(reward_surprise_callback, pattern="^reward_surprise$"))
    application.add_handler(CallbackQueryHandler(setmodel_callback, pattern="^setmodel_"))
    application.add_handler(CallbackQueryHandler(setreward_callback, pattern="^setreward_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_input_dispatcher))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        bootstrap_retries=5,
    )

if __name__ == "__main__":
    main()