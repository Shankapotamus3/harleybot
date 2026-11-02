#!/usr/bin/env python3
"""
Harley Quinn Bot - Domme, chaotic, dangerously fun.
Harley's voice comes from a local system prompt. No character_slug dependency.
Venue features: room picker per venue, static baseline plus scoped AI probes.

Changelog:
- single state architecture: DB row behind a write-through dict view
- one render pipeline; spec resolved once, payload built from the spec
- room picker per venue; probes scoped to the rooms chosen, capped at 10
- AI probes scoped to the picked rooms and told what not to repeat
- chroma is the default task and reward model; lustify is the fallback
- box locked to 960x1280 via constants so a cleared env cannot square it
- penis variant: futanari lead, genitals-only clause, no anatomy in the negatives
- no female anatomy terms in any positive prompt
- NUDE_GATE default off; safe_mode forced false
"""

import io
import os
import re
import json
import time
import base64
import random
import logging
import asyncio
import threading
import requests
import cloudinary
import cloudinary.uploader
from io import BytesIO
from datetime import datetime, timezone, timedelta, time as dt_time

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

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

BOT_TOKEN = (
    os.getenv('BOT_TOKEN')
    or os.getenv('TELEGRAM_TOKEN')
    or os.getenv('TELEGRAM_BOT_TOKEN')
    or ''
).strip()

VENICE_API_KEY = (os.getenv('VENICE_API_KEY') or '').strip()
DATABASE_URL = os.getenv('DATABASE_URL')
DEBUG_CHAT_ID = int(os.getenv('DEBUG_CHAT_ID', '0') or 0)

VENICE_API_URL = "https://api.venice.ai/api/v1"
VENICE_IMAGE_URL = "https://api.venice.ai/api/v1/image/generate"
VENICE_UPSCALE_URL = "https://api.venice.ai/api/v1/image/upscale"
TEXT_MODEL = os.getenv("TEXT_MODEL", "venice-uncensored-role-play").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-3-8-flash").strip()
VISION_FALLBACKS = [m.strip() for m in os.getenv(
    "VISION_FALLBACKS", "google-gemma-4-26b-a4b-it,e2ee-qwen3-vl-30b-a3b-p").split(",") if m.strip()]
VISION_CHAIN = [m for m in [VISION_MODEL] + VISION_FALLBACKS if m]
PROBE_MODELS = [m.strip() for m in os.getenv(
    "PROBE_MODELS", "openai-gpt-oss-120b,gemma-4-31b-it,mistral-small-3-2-24b-instruct").split(",") if m.strip()]

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


# lustify renders vulvas regardless of the variant tags. chroma obeys them.
# Do not move the reward model off chroma without re-testing /testreward.
IMAGE_MODEL = _models_from_env("IMAGE_MODEL", "chroma")[0]
IMAGE_MODEL_REWARD = _models_from_env("IMAGE_MODEL_REWARD", "chroma")[0]
IMAGE_FALLBACKS = _models_from_env("IMAGE_FALLBACKS", "lustify-v8")
IMAGE_FORMAT = os.getenv("IMAGE_FORMAT", "png").strip()
IMAGE_ASPECT = os.getenv("IMAGE_ASPECT", "3:4").strip()
IMAGE_RESOLUTION = os.getenv("IMAGE_RESOLUTION", "2K").strip()
IMAGE_SHARPEN = os.getenv("IMAGE_SHARPEN", "1").strip()
IMAGE_STEPS = os.getenv("IMAGE_STEPS", "45").strip()
IMAGE_CFG = os.getenv("IMAGE_CFG", "7.5").strip()
IMAGE_MAX_SIDE = os.getenv("IMAGE_MAX_SIDE", "1920").strip()

# Pinned, not read from env. A cleared variable used to square the box.
IMAGE_WIDTH = 960
IMAGE_HEIGHT = 1280

# Strip steps and cfg back to server defaults, matching the website. The
# negative prompt is still sent - it carries the garment and anatomy levers.
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
MAX_PROBES = 10

# An upscaled render collapses in bytes while keeping its dimensions. Sharp
# photographic PNG runs about 2.4 bytes/px, an upscale about 0.2. 1.0 sits
# between them. Set 0 to disable the re-roll entirely.
MUSH_BYTES_PER_PX = float(os.getenv("MUSH_BYTES_PER_PX", "1.0"))

SHARPNESS_FLOOR = float(os.getenv("SHARPNESS_FLOOR", "90"))
REWARD_HOUR = int(os.getenv("REWARD_HOUR", "9"))
REWARD_MODEL_DEFAULT = IMAGE_MODEL_REWARD
DEFAULT_VARIANT = "vagina"
DEFAULT_PRIVACY = "private"

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
    "lustify-v8": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 50},
    "z-image-turbo": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 8},
}

MODEL_EXPLICIT = {"lustify-v8", "chroma", "wai-illu"}
TASK_MODELS = ["chroma", "lustify-v8", "wai-illu"]

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

NUDE_SYSTEM = (
    "You are a strict image classifier. Look at the picture and reply with "
    "exactly one word. NUDE if the visible torso is bare from chest to hips and "
    "nothing covers the crotch. COVERED if clothing, underwear, a hand, a prop "
    "or a leg hides the crotch. UNSURE if the framing makes it impossible to "
    "tell."
)

NUDE_MODELS = [
    "e2ee-qwen3-vl-30b-a3b-instruct",
    "qwen3-vl-235b-a22b-instruct",
    "google-gemma-4-26b-a4b-it",
]

HARLEY_BASE = "Harley Quinn, white skin, blonde hair in pigtails with red and blue dyed tips, athletic slim build, lean muscular arms, red and black jester makeup, blue and red eyeshadow"

HARLEY_VARIANTS = {
    "vagina": {
        "label": "Vagina",
        "body": "curvy athletic figure, medium breasts",
        "clothed": "",
        "lead": "",
        "clause": "genitals clearly visible, breasts bare",
        "nude_negative": "",
    },
    "penis": {
        "label": "Penis",
        "body": "curvy athletic figure, medium breasts, futanari",
        "clothed": "obvious bulge in the front of her clothes, visibly packing, cock outline through the fabric",
        "lead": "futanari, penis, balls",
        "clause": "penis and balls clearly visible",
        "nude_negative": "",
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
    harley_variant = Column(String(20), default=DEFAULT_VARIANT)
    current_outfit = Column(String(100), default=None)
    outfit_items = Column(Text, default=None)
    outfit_detail = Column(Text, default=None)
    location = Column(String(50), default=None)
    custom_location = Column(String(100), default=None)
    location_detail = Column(Text, default=None)
    location_probes = Column(Text, default=None)
    rooms = Column(Text, default=None)
    reward_model = Column(String(50), default=None)
    task_model = Column(String(50), default=None)
    context_alone = Column(Boolean, default=True)
    context_others = Column(String(100), default=None)
    context_privacy = Column(String(20), default=DEFAULT_PRIVACY)
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
        "user_states": {
            "reward_target": "INTEGER",
            "rooms": "TEXT",
            "reward_model": "VARCHAR(50)",
            "task_model": "VARCHAR(50)",
        },
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


# ============ STATE ============
_STATES = {}

_PERSIST = {
    "variant": ("harley_variant", None),
    "privacy": ("context_privacy", None),
    "location": ("location", None),
    "location_notes": ("location_detail", None),
    "reward_model": ("reward_model", None),
    "task_model": ("task_model", None),
    "rooms": ("rooms", json.dumps),
    "probes": ("location_probes", json.dumps),
}


class StateView(dict):
    """Dict front end over a UserState row. Persistent keys write straight through."""

    def __init__(self, row, session):
        super().__init__()
        self._row = row
        self._session = session
        for key, (col, decode) in _PERSIST.items():
            raw = getattr(row, col, None)
            if decode and raw:
                try:
                    super().__setitem__(key, json.loads(raw))
                except (ValueError, TypeError):
                    super().__setitem__(key, [])
            else:
                super().__setitem__(key, raw)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        target = _PERSIST.get(key)
        if not target:
            return
        col, encode = target
        try:
            setattr(self._row, col, encode(value) if encode else value)
            self._session.commit()
        except Exception as exc:
            logger.warning(f"state write failed for {key}: {exc}")

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]


def get_state(user_id):
    view = _STATES.get(user_id)
    if view is not None:
        return view
    session = get_session()
    row = session.query(UserState).filter_by(user_id=user_id).first()
    if not row:
        row = UserState(user_id=user_id, username="")
        session.add(row)
        session.commit()
    view = StateView(row, session)
    _STATES[user_id] = view
    return view


def reset_state(user_id):
    _STATES.pop(user_id, None)
    session = get_session()
    try:
        row = session.query(UserState).filter_by(user_id=user_id).first()
        if row:
            row.harley_variant = DEFAULT_VARIANT
            row.context_privacy = DEFAULT_PRIVACY
            row.reward_model = REWARD_MODEL_DEFAULT
            row.task_model = IMAGE_MODEL
            row.location = None
            row.location_detail = None
            row.rooms = None
            row.location_probes = None
            session.commit()
    finally:
        session.close()


def set_location(user_id, notes):
    state = get_state(user_id)
    state["location_notes"] = notes


def all_user_ids():
    session = get_session()
    try:
        return [r.user_id for r in session.query(UserState.user_id).all()]
    finally:
        session.close()


def recent_settings(user_id):
    try:
        return json.loads(get_state(user_id).get("_recent") or "[]")
    except (ValueError, TypeError):
        return []


def remember_setting(user_id, item):
    pool = recent_settings(user_id)
    pool.append(item)
    state = get_state(user_id)
    super(StateView, state).__setitem__("_recent", json.dumps(pool[-8:]))


# ============ IMAGE HANDLING ============
def sniff_ext(b):
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return "webp"
    if b[:3] == b'\xff\xd8\xff':
        return "jpg"
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return "png"
    return "bin"


def sharpness_score(source):
    if isinstance(source, (bytes, bytearray)):
        try:
            source = Image.open(BytesIO(source)).convert("RGB")
        except Exception:
            return 0.0
    g = source.convert("L")
    w, h = g.size
    scale = MEASURE_EDGE / max(w, h)
    if scale < 1:
        g = g.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    lap = g.filter(ImageFilter.Kernel(
        (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return float(ImageStat.Stat(lap).var[0])


def check_render(source):
    """Return (ok, reason). Accepts bytes or a (w, h) tuple."""
    if isinstance(source, (bytes, bytearray)):
        try:
            with Image.open(BytesIO(source)) as im:
                w, h = im.size
        except Exception:
            return False, "undecodable"
    else:
        w, h = source
    if min(w, h) < 400:
        return False, f"undersize {w}x{h}"
    if max(w, h) / max(1, min(w, h)) > 2.0:
        return False, f"aspect {w}x{h}"
    return True, None


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
            context={'user_id': str(user_id), 'image_type': image_type,
                     'uploaded_at': str(datetime.now(timezone.utc))},
            tags=[f"user_{user_id}", image_type, "telegram_bot"]
        )
        logger.info(f"Cloudinary upload success: {result.get('public_id')}")
        return {'url': result.get('secure_url'), 'public_id': result.get('public_id'),
                'width': result.get('width'), 'height': result.get('height')}
    except Exception as e:
        logger.error(f"Cloudinary upload error: {type(e).__name__}: {e}")
        return None


async def upload_png(png_bytes, user_id, prefix="harley"):
    result = await upload_to_cloudinary(png_bytes, user_id, image_type=prefix)
    return result.get("url") if result else None


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

# Venues with rooms get a picker; anything absent keeps the flat flow.
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
    for alias, key in _ALIAS_ORDER:
        if alias in low:
            return key
    return "Generic"


def baseline_chips(location):
    raw = VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])
    return [c.strip() for c in raw.split(",") if c.strip()]


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
    "kink_social_media": ("Social Media", "Online exposure")
}

KINK_LEVELS = {
    "no": {"emoji": "X", "name": "No", "desc": "Hard limit"},
    "okay": {"emoji": "O", "name": "Okay", "desc": "Allowed"},
    "yes": {"emoji": "Y", "name": "Yes", "desc": "Desired"},
}

OUTFIT_OPTIONS = {
    "casual": {"emoji": "", "name": "Casual", "male_items": ["t-shirt", "jeans", "boxers"], "female_items": ["t-shirt", "leggings", "panties"]},
    "formal": {"emoji": "", "name": "Formal", "male_items": ["button-up", "dress pants", "tie"], "female_items": ["blouse", "skirt", "bra"]},
    "athletic": {"emoji": "", "name": "Athletic", "male_items": ["tank", "shorts", "compression"], "female_items": ["sports bra", "shorts", "panties"]},
    "lounge": {"emoji": "", "name": "Lounge", "male_items": ["hoodie", "sweatpants", "boxers"], "female_items": ["hoodie", "leggings", "panties"]},
    "minimal": {"emoji": "", "name": "Underwear Only", "male_items": ["boxers"], "female_items": ["bra", "panties"]},
    "custom": {"emoji": "", "name": "Custom", "male_items": [], "female_items": []}
}

GENDER_GUIDELINES = {
    "male": {"body_parts": ["chest", "abs", "cock", "balls", "ass"], "cannot": ["cleavage", "bra", "panties"]},
    "female": {"body_parts": ["cleavage", "breasts", "nipples", "pussy", "ass"], "cannot": ["cock", "balls"]},
    "trans": {"body_parts": ["chest", "breasts", "cock", "pussy", "ass"], "cannot": []},
    "nonbinary": {"body_parts": ["chest", "breasts", "genitals", "ass"], "cannot": []}
}

CONTEXT_OPTIONS = {
    "alone": {"emoji": "", "name": "Alone", "bonus": 0},
    "partner": {"emoji": "", "name": "Partner Home", "bonus": 1},
    "roommates": {"emoji": "", "name": "Roommates", "bonus": 1},
    "parents": {"emoji": "", "name": "Family", "bonus": 2},
    "friends": {"emoji": "", "name": "Friends Over", "bonus": 1},
    "public": {"emoji": "", "name": "Public/Strangers", "bonus": 0}
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


def variant_of(state):
    key = state.get("variant") or DEFAULT_VARIANT
    return HARLEY_VARIANTS.get(key, HARLEY_VARIANTS[DEFAULT_VARIANT])


def _row_of(target):
    if isinstance(target, int):
        return get_state(target)._row
    if isinstance(target, dict):
        return target._row
    return target


def probes_for(target):
    row = _row_of(target)
    try:
        data = json.loads(getattr(row, "location_probes", None) or '[]')
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def rooms_for(target):
    row = _row_of(target)
    try:
        data = json.loads(getattr(row, "rooms", None) or '[]')
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def rooms_for_venue(venue):
    table = ROOM_OPTIONS.get(get_probe_key(venue)) or {}
    return list(table.keys())


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


def room_chips(target, location):
    """Baseline features for the rooms the sub actually has access to."""
    picked = []
    for room in rooms_for(target):
        picked.extend(present_features(location, room))
    return [p for p in picked if p]


def venue_baseline(target, location):
    picked = room_chips(target, location)
    if picked:
        return ", ".join(picked)
    stored = probes_for(target)
    if stored:
        return ", ".join(stored)
    return VENUE_BASELINE.get(get_probe_key(location), VENUE_BASELINE["Generic"])


def reward_chain():
    chain = [IMAGE_MODEL_REWARD] + [m for m in IMAGE_FALLBACKS if m != IMAGE_MODEL_REWARD]
    return [m for m in chain if m in MODEL_EXPLICIT]


def reward_negative(variant):
    v = HARLEY_VARIANTS.get(variant, {}) if isinstance(variant, str) else (variant or {})
    return NEGATIVE_PROMPT_NUDE + (v.get("nude_negative") or "")


def task_keyboard(task_id, title):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Done, {title}", callback_data=f"complete_{task_id}")],
        [InlineKeyboardButton("Not possible", callback_data=f"blocked_{task_id}")],
        [InlineKeyboardButton("I can't...", callback_data=f"giveup_{task_id}")],
        [InlineKeyboardButton("Mercy", callback_data=f"mercy_{task_id}")],
    ])

def room_keyboard(venue, selected):
    rows = []
    for key in rooms_for_venue(venue):
        mark = "Y " if key in selected else ""
        rows.append([InlineKeyboardButton(f"{mark}{key.title()}", callback_data=f"room_{key}")])
    rows.append([
        InlineKeyboardButton("Add a room", callback_data="room_custom"),
        InlineKeyboardButton("That's the lot", callback_data="room_done"),
    ])
    return InlineKeyboardMarkup(rows)

def present_keyboard(venue, rooms, selected):
    rows, row = [], []
    for room_index, room in enumerate(rooms):
        for feat_index, feature in enumerate(present_features(venue, room)):
            mark = "Y " if feature in selected else ""
            row.append(InlineKeyboardButton(
                f"{mark}{feature[:18]}", callback_data=f"present_{room_index}_{feat_index}"))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("Add my own", callback_data="present_custom"),
        InlineKeyboardButton("Done", callback_data="present_done"),
    ])
    return InlineKeyboardMarkup(rows)


def probe_keyboard(pool, selected):
    rows, row = [], []
    for index, item in enumerate(pool):
        mark = "Y " if item in selected else ""
        row.append(InlineKeyboardButton(f"{mark}{item[:18]}", callback_data=f"probe_{index}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("Add my own", callback_data="probe_custom"),
        InlineKeyboardButton("Done", callback_data="probe_done"),
    ])
    return InlineKeyboardMarkup(rows)


def variant_keyboard(selected):
    rows = []
    for key, value in HARLEY_VARIANTS.items():
        mark = "Y " if key == selected else ""
        rows.append([InlineKeyboardButton(f"{mark}{value['label']}", callback_data=f"variant_{key}")])
    return InlineKeyboardMarkup(rows)


def model_keyboard(selected):
    rows = []
    for model in TASK_MODELS:
        mark = "Y " if model == selected else ""
        rows.append([InlineKeyboardButton(f"{mark}{model}", callback_data=f"model_{model}")])
    return InlineKeyboardMarkup(rows)


# ============ VENICE TRANSPORT ============
def _headers():
    return {"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"}


async def _venice_post(path, payload, timeout=60):
    response = await asyncio.to_thread(
        requests.post, f"{VENICE_API_URL}{path}", headers=_headers(), json=payload, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"{path} HTTP {response.status_code}: {response.text[:160]}")
    return response.json()


async def _venice_get(path, timeout=30):
    response = await asyncio.to_thread(
        requests.get, f"{VENICE_API_URL}{path}", headers=_headers(), timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"{path} HTTP {response.status_code}: {response.text[:160]}")
    return response.json()


async def generate_ai_response(messages, temperature=0.9):
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    try:
        data = await _venice_post("/chat/completions", {
            "model": TEXT_MODEL,
            "messages": [{"role": "system", "content": HARLEY_VOICE}] + list(messages),
            "temperature": temperature,
            "max_tokens": 500,
        }, timeout=30)
        return data['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"AI error: {e}")
        return None


def _parse_probe_lines(content):
    out, seen = [], set()
    for line in content.splitlines():
        t = re.sub(r"^[\-\*\d\.\)]+\s*", "", line.strip()).strip(" .")
        if not t or len(t) > 30:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


async def generate_probes(location, rooms=None, known=None):
    """Extra venue features. Empty list on failure - callers already have the baseline."""
    known = known or []
    where = f"a {location}, specifically the {', '.join(rooms)}" if rooms else f"a {location}"
    avoid = f"\nDo NOT repeat any of these: {', '.join(known[:20])}." if known else ""
    prompt = (f"List 8 concrete physical features of {where} that someone could use for "
              f"exposure or bondage play. Real, specific, mundane: \"the coat hooks\", "
              f"\"the mirror over the sink\", \"the wedge under the fire door\". "
              f"No abstractions, no room names, no furniture categories. "
              f"One per line, no numbering, no explanation.{avoid}")
    for model in PROBE_MODELS:
        for attempt in range(2):
            try:
                raw = await asyncio.to_thread(
                    requests.post, f"{VENICE_API_URL}/chat/completions",
                    headers=_headers(),
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
                return items[:8]
            logger.warning(f"Probe gen {model} returned {len(items)} usable lines, trying next")
            break
    return []


async def analyze_image(image_bytes, task_desc, followup_target=None):
    try:
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
        for model in VISION_CHAIN:
            try:
                data = await _venice_post("/chat/completions", {
                    "model": model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    ]}],
                    "max_tokens": 200,
                }, timeout=30)
                return data['choices'][0]['message']['content']
            except Exception as e:
                logger.warning(f"Vision {model} failed: {e}")
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
    rooms = rooms_for(user)
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
        recent = json.loads(user.recent_settings or '[]')
    except (ValueError, TypeError):
        recent = []
    recent_text = "\n".join(f"- {r}" for r in recent[-8:]) if recent else "none yet"
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
    rooms_line = f"\nRooms in play: {', '.join(rooms)}" if rooms else ""
    prompt = f"""Write ONE task for a {gender} sub. You are Harley - stay in her voice, but obey the format exactly.

Location: {location}{rooms_line}
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
- Stay inside the rooms in play and name the room in the task
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
        forbidden_keywords = {
            "marking": ["write", "marker", "sharpie", "draw on", "body writing", "written", "label"],
            "watersports": ["pee", "piss", "urine", "wet yourself"],
            "breathplay": ["choke", "strangle", "suffocate"],
            "social_media": ["post", "upload", "instagram", "twitter", "facebook", "share online"],
        }
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
            recent.append(setting)
            user.recent_settings = json.dumps(recent[-8:])
            if session:
                session.commit()
        if response:
            return response, actual_risk
    fallbacks = {
        1: f"Strip completely at {location} and photograph your reflection",
        2: f"Strip at {location} near the unlocked door and take a photo",
        3: f"Expose yourself at {location} near a window and photograph the view",
        4: f"Step just outside {location} doorway, expose yourself, and take a photo",
        5: f"Walk to the end of the {location} hallway, expose yourself briefly, photograph the empty hall behind you",
    }
    return fallbacks.get(actual_risk, fallbacks[1]), actual_risk


# ============ SPEC + PAYLOAD ============
def _fetch_spec(model):
    if model in MODEL_SPECS:
        return MODEL_SPECS[model]
    spec = {}
    try:
        r = requests.get(
            f"{VENICE_API_URL}/models?type=image", headers=_headers(), timeout=30)
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
    return spec


def get_spec(target=None, nude=False, model=None):
    """Resolve a full render spec. `target` is a variant key and is only used for logging."""
    m = model or (IMAGE_MODEL_REWARD if nude else IMAGE_MODEL)
    raw = _fetch_spec(m)
    constraints = (raw.get("constraints") or {})
    known = KNOWN_MODELS.get(m, {})
    spec = {
        "model": m,
        "output_format": IMAGE_FORMAT,
        "mode": "pixels",
        "width": 0,
        "height": 0,
        "aspect_ratio": None,
        "resolution": None,
        "steps": None,
        "cfg": None,
    }
    ratio_mode = bool(constraints.get("aspectRatios")) or (not constraints and known.get("mode") == "ratio")
    if ratio_mode:
        ratios = constraints.get("aspectRatios") or known.get("ratios") or ["3:4"]
        spec["mode"] = "ratio"
        spec["aspect_ratio"] = IMAGE_ASPECT if IMAGE_ASPECT in ratios else ratios[0]
        res = constraints.get("resolutions") or known.get("res") or ["1K", "2K"]
        spec["resolution"] = IMAGE_RESOLUTION if IMAGE_RESOLUTION in res else res[-1]
    else:
        div = max(1, int(constraints.get("widthHeightDivisor") or 8))
        max_w = int(constraints.get("maxWidth") or known.get("max_w") or 1280)
        max_h = int(constraints.get("maxHeight") or known.get("max_h") or 1280)
        spec["width"] = max(div, (min(IMAGE_WIDTH, max_w) // div) * div)
        spec["height"] = max(div, (min(IMAGE_HEIGHT, max_h) // div) * div)
        res = constraints.get("resolutions")
        if res or constraints.get("defaultResolution"):
            res = res or ["1K", "2K"]
            spec["resolution"] = IMAGE_RESOLUTION if IMAGE_RESOLUTION in res else res[-1]
        step_spec = constraints.get("steps") or {}
        max_steps = int(step_spec.get("max") or known.get("steps_max") or 30)
        if not MINIMAL_PAYLOAD:
            spec["steps"] = min(int(IMAGE_STEPS), max_steps)
            spec["cfg"] = float(IMAGE_CFG)
    return spec


def load_model_spec():
    for m in {IMAGE_MODEL, IMAGE_MODEL_REWARD}:
        spec = _fetch_spec(m)
        logger.info(f"BOOT SPEC {m}: {json.dumps(spec) if spec else 'EMPTY (using KNOWN_MODELS)'}")


def _payload(prompt, spec, negative=None):
    data = {
        "model": spec["model"],
        "prompt": prompt,
        "format": spec["output_format"],
        # The API defaults safe_mode on, which sanitises every render.
        "safe_mode": False,
    }
    if negative:
        data["negative_prompt"] = negative
    if spec["mode"] == "ratio":
        data["aspect_ratio"] = spec["aspect_ratio"]
        if spec["resolution"]:
            data["resolution"] = spec["resolution"]
    else:
        data["width"] = spec["width"]
        data["height"] = spec["height"]
        if spec["resolution"]:
            data["resolution"] = spec["resolution"]
        if spec["steps"]:
            data["steps"] = spec["steps"]
            data["cfg_scale"] = spec["cfg"]
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
    for attempt in range(3):
        try:
            with _image_sem:
                response = requests.post(VENICE_IMAGE_URL, headers=_headers(), json=data, timeout=180)
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


# ============ PROMPTS ============
def _clean(prompt):
    cleaned = SOFT_WORDS.sub("", prompt)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip().strip(",").strip()
    return f"{cleaned}, {SHARP_TAIL}"


async def build_prompt(target, variant=None, *, nude=False, room_notes=""):
    """Return (positive, negative). `target` is a user id or a state view."""
    state = get_state(target) if isinstance(target, int) else target
    v = HARLEY_VARIANTS.get(variant or state.get("variant") or DEFAULT_VARIANT,
                            HARLEY_VARIANTS[DEFAULT_VARIANT])
    location = state.get("location") or "a room"

    bits = [HARLEY_BASE]
    if v.get("lead"):
        bits.append(v["lead"])
    bits.append(v["body"])

    if nude:
        bits.append(f"{NUDE} {random.choice(REWARD_GEAR)}")
        bits.append(v["clause"])
        bits.append(random.choice(REWARD_POSES))
        negative = reward_negative(v)
    else:
        bits.append(state.get("outfit_detail") or state.get("current_outfit")
                    or random.choice(HARLEY_OUTFITS))
        if v.get("clothed"):
            bits.append(v["clothed"])
        bits.append(random.choice(HARLEY_POSES))
        negative = NEGATIVE_PROMPT_SEXY + NEGATIVE_HARD

    notes = (room_notes or state.get("location_notes") or "").replace("\n", ", ")
    if notes:
        bits.append(notes)
    bits.append(f"at {location}")
    bits.append(STUDIO)

    return _clean(", ".join(b for b in bits if b)), negative


async def build_reward_prompt(target, variant=None):
    prompt, _ = await build_prompt(target, variant, nude=True)
    return prompt


# ============ NUDITY GATE ============
async def nude_check(png_bytes):
    """Return NUDE, COVERED or UNSURE. Never raises."""
    if not NUDE_GATE:
        return "UNSURE"

    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": None,
        "max_tokens": 200,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": NUDE_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Classify this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
    }

    for model in NUDE_MODELS:
        payload["model"] = model
        try:
            data = await _venice_post("/chat/completions", payload, timeout=25)
        except Exception as exc:
            logger.warning(f"nude_check transport failure on {model}: {exc}")
            continue
        try:
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            logger.warning(f"nude_check malformed reply from {model}")
            continue
        if not text:
            logger.warning(f"nude_check empty reply from {model}")
            continue
        upper = text.upper()
        if "NUD" in upper:
            return "NUDE"
        if "COVER" in upper:
            return "COVERED"
        logger.info(f"nude_check unparsed reply from {model}: {text[:80]}")
        return "UNSURE"

    logger.warning("nude_check exhausted every model without a verdict")
    return "UNSURE"


# ============ RENDER PIPELINE ============
def _mushy(png_bytes):
    try:
        with Image.open(BytesIO(png_bytes)) as im:
            px = im.width * im.height
    except Exception:
        return False
    if px <= 0:
        return False
    return (len(png_bytes) / px) < MUSH_BYTES_PER_PX


async def _request_image(prompt, negative=None, spec=None):
    """One request across the fallback chain. Returns bytes or None."""
    spec = spec or get_spec(nude=False)
    chain = [spec["model"]] + [m for m in IMAGE_FALLBACKS if m != spec["model"]]
    for index, model in enumerate(chain):
        current = spec if index == 0 else get_spec(model=model)
        data = _payload(prompt, current, negative)
        raw = await asyncio.to_thread(_post_image, dict(data), model)
        if raw:
            return raw
    return None


async def _render_batch(user_id, variant, spec, *, nude, room_notes, count):
    prompt, negative = await build_prompt(user_id, variant, nude=nude, room_notes=room_notes)

    async def one():
        raw = await _request_image(prompt, negative, spec)
        if not raw:
            return (0.0, b"", "no image")
        if MUSH_BYTES_PER_PX and _mushy(raw):
            logger.info(f"user {user_id} draw was mushy, re-rolling once")
            raw = await _request_image(prompt, negative, spec)
            if not raw:
                return (0.0, b"", "no image after reroll")
        ok, reason = check_render(raw)
        if not ok:
            return (0.0, raw, reason)
        return (sharpness_score(raw), raw, None)

    gathered = await asyncio.gather(*(one() for _ in range(count)), return_exceptions=True)

    scored = []
    for item in gathered:
        if isinstance(item, Exception):
            logger.warning(f"render raised: {item}")
            continue
        scored.append(item)
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored


async def _render_best(user_id, variant, *, nude, room_notes="", model=None):
    """Best of a batch, with one extra batch if everything lands under the floor."""
    state = get_state(user_id)
    spec = get_spec(variant, nude=nude, model=model or state.get("reward_model" if nude else "task_model"))
    floor = MODEL_MIN_SHARPNESS.get(spec["model"], SHARPNESS_FLOOR)

    for attempt in (1, 2):
        batch = await _render_batch(
            user_id, variant, spec, nude=nude, room_notes=room_notes, count=RENDER_ATTEMPTS)
        clean = [row for row in batch if row[2] is None and row[1]]
        passing = [row for row in clean if row[0] >= floor]

        if passing:
            score, png, _ = passing[0]
            logger.info(f"user {user_id} attempt {attempt} best {score:.1f} of {len(batch)}")
            return png
        if clean:
            score, png, _ = clean[0]
            logger.info(f"user {user_id} attempt {attempt} nothing over floor, keeping {score:.1f}")
            return png
        logger.warning(f"user {user_id} attempt {attempt} produced nothing usable")

    raise RuntimeError("render failed")


async def _upscale(png_bytes):
    try:
        data = await _venice_post(
            "/image/upscale",
            {"image": base64.b64encode(png_bytes).decode("ascii"), "scale": 2},
            timeout=120,
        )
        encoded = data.get("image")
        if isinstance(encoded, dict):
            encoded = encoded.get("b64_json") or encoded.get("base64") or ""
        if encoded:
            png_bytes = base64.b64decode(encoded)
    except Exception as exc:
        logger.warning(f"upscale failed, shipping the raw render: {exc}")
    return finalize_png(png_bytes) or png_bytes


# ============ DELIVERY ============
async def _build_and_send(bot, chat_id, user_id, png_bytes, *, caption, reply_to=None):
    png_bytes = await _upscale(png_bytes)
    url = await upload_png(png_bytes, user_id)
    if url:
        await bot.send_document(
            chat_id=chat_id, document=url, caption=caption[:1024],
            reply_to_message_id=reply_to, write_timeout=180, read_timeout=180)
    else:
        await bot.send_document(
            chat_id=chat_id, document=png_bytes, filename=f"harley_{int(time.time())}.png",
            caption=caption[:1024], reply_to_message_id=reply_to,
            write_timeout=180, read_timeout=180)


async def send_render(bot, chat_id, user_id, png_bytes, *, caption, reply_to=None):
    try:
        await _build_and_send(bot, chat_id, user_id, png_bytes, caption=caption, reply_to=reply_to)
    except Exception:
        logger.exception(f"send_render failed for chat {chat_id}")
        await bot.send_message(chat_id=chat_id, text="Could not deliver that one. Try again in a moment.")


async def send_avatar_photo(bot, chat_id, user_id, png_bytes, *, caption=""):
    png_bytes = finalize_png(png_bytes) or png_bytes
    url = await upload_png(png_bytes, user_id, prefix="avatar")
    if url:
        await bot.send_document(chat_id=chat_id, document=url, caption=caption[:1024],
                                write_timeout=180, read_timeout=180)
    else:
        await bot.send_document(chat_id=chat_id, document=png_bytes,
                                filename=f"avatar_{int(time.time())}.png", caption=caption[:1024],
                                write_timeout=180, read_timeout=180)


async def deliver_reward(bot, chat_id, user_id, variant, *, caption):
    png_bytes = await _render_best(user_id, variant, nude=True)
    verdict = await nude_check(png_bytes)

    if verdict == "COVERED" and NUDE_GATE:
        logger.info(f"user {user_id} reward came back COVERED, running retry pass")
        try:
            retry = await _render_best(user_id, variant, nude=True)
            retry_verdict = await nude_check(retry)
            if retry_verdict != "COVERED":
                png_bytes = retry
                verdict = retry_verdict
        except Exception as exc:
            logger.warning(f"retry pass failed for user {user_id}: {exc}")

    logger.info(f"user {user_id} reward delivered, gate verdict {verdict}")
    await send_render(bot, chat_id, user_id, png_bytes, caption=caption)


# ============ AVATAR ============
AVATAR_POSES = [
    "standing three quarter turn, weight on the back leg, chin lowered",
    "seated on the edge of a surface, forearms on the thighs, leaning in",
    "standing with one shoulder against a wall, arms loosely folded",
    "crouched on her heels, one hand resting on the floor",
    "walking toward the camera, caught mid stride, hair swinging",
    "kneeling upright, hands clasped behind her back",
    "reclining on one hip, propped on an elbow, ankles crossed",
]


async def generate_avatar_pose(user_id, variant=None):
    recent = recent_settings(user_id)
    pool = [pose for pose in AVATAR_POSES if pose not in recent] or AVATAR_POSES
    pose = random.choice(pool)
    remember_setting(user_id, pose)
    return pose


# ============ DEBUG ============
def _debug_name(label, suffix):
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_") or "debug"
    return f"{stem}_{int(time.time())}.{suffix}"


async def _debug_send(bot, chat_id, png_bytes, *, label, upscale=False, gate=False):
    if not DEBUG_CHAT_ID:
        return
    try:
        with Image.open(BytesIO(png_bytes)) as im:
            dims = f"{im.width}x{im.height}"
            px = im.width * im.height
        score = sharpness_score(png_bytes)
        density = len(png_bytes) / px if px else 0.0

        lines = [
            f"label: {label}",
            f"dims: {dims}",
            f"bytes: {len(png_bytes)}",
            f"bpp: {density:.3f}",
            f"sharpness: {score:.1f}",
        ]
        if gate:
            lines.append(f"gate: {await nude_check(png_bytes)}")

        payload = png_bytes
        if upscale:
            try:
                payload = await _upscale(png_bytes)
                lines.append(f"upscaled bytes: {len(payload)}")
            except Exception as exc:
                lines.append(f"upscale failed: {exc}")

        await bot.send_document(
            chat_id=DEBUG_CHAT_ID, document=payload, filename=_debug_name(label, "png"),
            caption="\n".join(lines)[:1024], write_timeout=180, read_timeout=180)
    except Exception:
        logger.exception(f"debug send failed for {label}")


# ============ INPUT DISPATCH ============
async def custom_input_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return

    pending = get_state(user.id).get("pending")

    if pending == "custom_location":
        await custom_location_input(update, context)
        return
    if pending == "custom_rooms":
        await custom_rooms_input(update, context)
        return
    if pending == "custom_features":
        await custom_features_input(update, context)
        return
    if pending == "custom_probe":
        await custom_probe_input(update, context)
        return
    if pending == "set_reward":
        await setreward_callback(update, context)
        return

    await chat_handler(update, context)


# ============ CHAT ============
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return

    text = message.text.strip()
    if not text or text.startswith("/"):
        return

    state = get_state(user.id)
    history = state.setdefault("history", [])
    history.append({"role": "user", "content": text})
    state["history"] = history[-20:]

    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
        reply = await generate_ai_response(state["history"])
    except Exception:
        logger.exception(f"chat failed for user {user.id}")
        await message.reply_text("Lost my train of thought. Say that again?")
        return

    if not reply:
        await message.reply_text("Lost my train of thought. Say that again?")
        return

    state["history"].append({"role": "assistant", "content": reply})
    state["history"] = state["history"][-20:]
    await message.reply_text(reply[:4000])


# ============ VENUE PICKER ============
VENUES = [
    ("home", "Home"), ("hotel", "Hotel"), ("office", "Office"), ("studio", "Studio"),
    ("gym", "Gym"), ("pool", "Pool"), ("beach", "Beach"), ("park", "Park"),
    ("bar", "Bar"), ("club", "Club"), ("car", "Car"), ("rooftop", "Rooftop"),
]

VENUE_LABELS = dict(VENUES)


def venue_keyboard():
    rows, row = [], []
    for key, label in VENUES:
        row.append(InlineKeyboardButton(label, callback_data=f"venue_{key}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Somewhere else", callback_data="venue_custom")])
    return InlineKeyboardMarkup(rows)


def _toggle(bucket, item):
    if item in bucket:
        bucket.remove(item)
    else:
        bucket.append(item)
    return bucket


async def location_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    state["venue"] = None
    state["rooms"] = []
    state["features"] = {}
    state["probes"] = []
    state["pending"] = None

    await update.effective_message.reply_text("Where are we shooting?", reply_markup=venue_keyboard())


async def venue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    state = get_state(user.id)
    data = query.data

    if data == "venue_custom":
        state["pending"] = "custom_location"
        await query.edit_message_text("Name the place and I will take it from there.")
        return

    venue = data.split("_", 1)[1]
    state["venue"] = venue
    state["rooms"] = []
    state["features"] = {}
    label = VENUE_LABELS.get(venue, venue.title())

    if rooms_for_venue(venue):
        await query.edit_message_text(
            f"{label}. Which rooms are in play?", reply_markup=room_keyboard(venue, []))
    else:
        state["pending"] = "custom_rooms"
        await query.edit_message_text(f"{label}. List the rooms, separated by commas.")


async def custom_location_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)

    state["venue"] = update.effective_message.text.strip()
    state["rooms"] = []
    state["features"] = {}
    state["pending"] = "custom_rooms"

    await update.effective_message.reply_text("Noted. Which rooms are in play? Separate them with commas.")


async def room_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    state = get_state(user.id)
    venue = state.get("venue") or ""

    if query.data == "room_done":
        rooms = state.get("rooms") or []
        if not rooms:
            await query.edit_message_text("Pick at least one room first.")
            return
        await query.edit_message_text(
            "Now the features inside those rooms.",
            reply_markup=present_keyboard(venue, rooms, []))
        return

    if query.data == "room_custom":
        state["pending"] = "custom_rooms"
        await query.edit_message_text("Add a room name.")
        return

    index = int(query.data.split("_", 1)[1])
    rooms = rooms_for_venue(venue)
    if 0 <= index < len(rooms):
        _toggle(state["rooms"], rooms[index])

    await query.edit_message_reply_markup(reply_markup=room_keyboard(venue, state["rooms"]))


async def custom_rooms_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    venue = state.get("venue") or ""

    supplied = [p.strip() for p in update.effective_message.text.split(",") if p.strip()]
    if not supplied:
        await update.effective_message.reply_text("Nothing usable there. Try again.")
        return

    state["pending"] = None

    if rooms_for_venue(venue):
        for room in supplied:
            if room not in state["rooms"]:
                state["rooms"].append(room)
        await update.effective_message.reply_text(
            "Added. Anything else?",
            reply_markup=room_keyboard(venue, state["rooms"]))
        return

    state["rooms"] = supplied
    await update.effective_message.reply_text(
        "Now the features inside those rooms.",
        reply_markup=present_keyboard(venue, supplied, []))


async def present_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    state = get_state(user.id)
    venue = state.get("venue") or ""
    rooms = state.get("rooms") or []
    features = state.setdefault("features", {})

    if query.data == "present_done":
        chosen = [f for group in features.values() for f in group]
        state["pending"] = None
        await _show_probes(query, user.id, venue, rooms, chosen)
        return

    if query.data == "present_custom":
        state["pending"] = "custom_features"
        await query.edit_message_text("Describe the feature. Prefix it with the room if it matters.")
        return

    _, room_index, feat_index = query.data.split("_")
    room_index, feat_index = int(room_index), int(feat_index)
    if 0 <= room_index < len(rooms):
        room = rooms[room_index]
        options = present_features(venue, room)
        if 0 <= feat_index < len(options):
            _toggle(features.setdefault(room, []), options[feat_index])

    await query.edit_message_reply_markup(
        reply_markup=present_keyboard(venue, rooms, [f for g in features.values() for f in g]))


async def custom_features_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    venue = state.get("venue") or ""
    rooms = state.get("rooms") or []
    features = state.setdefault("features", {})

    text = update.effective_message.text.strip()
    if not text:
        await update.effective_message.reply_text("Nothing usable there. Try again.")
        return

    features.setdefault(rooms[0] if rooms else venue, []).append(text)
    state["pending"] = None

    chosen = [f for group in features.values() for f in group]
    await update.effective_message.reply_text(
        "Added. Anything else?", reply_markup=present_keyboard(venue, rooms, chosen))


async def _show_probes(query, user_id, venue, rooms, chosen):
    state = get_state(user_id)
    baseline = probe_options(venue, rooms)

    known = [*rooms, *chosen, *baseline]
    try:
        ai_probes = await generate_probes(venue, rooms, known)
    except Exception as exc:
        logger.warning(f"probe generation failed for {venue}: {exc}")
        ai_probes = []

    state["probe_pool"] = (baseline + ai_probes)[:MAX_PROBES]
    state["probes"] = []

    await query.edit_message_text(
        "Anything else worth pinning down?",
        reply_markup=probe_keyboard(state["probe_pool"], []))


async def probe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    state = get_state(user.id)
    pool = state.get("probe_pool") or []

    if query.data == "probe_done":
        await _commit_location(query, user.id)
        return

    if query.data == "probe_custom":
        state["pending"] = "custom_probe"
        await query.edit_message_text("Describe it.")
        return

    index = int(query.data.split("_", 1)[1])
    if 0 <= index < len(pool):
        _toggle(state["probes"], pool[index])

    await query.edit_message_reply_markup(reply_markup=probe_keyboard(pool, state["probes"]))


async def custom_probe_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    pool = state.setdefault("probe_pool", [])

    text = update.effective_message.text.strip()
    if not text:
        await update.effective_message.reply_text("Nothing usable there. Try again.")
        return

    if len(pool) < MAX_PROBES:
        pool.append(text)
        state["probes"].append(text)
    state["pending"] = None

    await update.effective_message.reply_text(
        "Added. Anything else?", reply_markup=probe_keyboard(pool, state["probes"]))


async def _commit_location(query, user_id):
    state = get_state(user_id)
    venue = state.get("venue") or ""
    rooms = state.get("rooms") or []
    features = state.get("features") or {}
    probes = state.get("probes") or []
    label = VENUE_LABELS.get(venue, venue)

    lines = [f"Venue: {label}"]
    for room in rooms:
        picked = features.get(room, [])
        lines.append(f"{room}: " + (", ".join(picked) if picked else "as is"))
    if probes:
        lines.append("Also: " + ", ".join(probes))

    notes = "\n".join(lines)
    state["location"] = label
    set_location(user_id, notes)
    state["pending"] = None

    await query.edit_message_text(
        f"Locked in.\n\n{notes}\n\nUse /clear to drop it.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Clear", callback_data="loc_clear")]]))


async def clear_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    state["location"] = None
    state["location_notes"] = None
    state["rooms"] = []
    state["probes"] = []
    state["venue"] = None
    state["features"] = {}

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Location cleared.")
    else:
        await update.effective_message.reply_text("Location cleared.")


# ============ SETTINGS COMMANDS ============
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Harley here. /avatar for a photo, /location to set the scene, /settings for the rest.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "/avatar - a photo on demand\n"
        "/location - set venue, rooms and features\n"
        "/variant - anatomy\n"
        "/privacy - outfit level\n"
        "/settings - current setup\n"
        "/clear - drop the location\n"
        "/reset - back to defaults")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_state(update.effective_user.id)
    rows = [
        f"variant: {state.get('variant') or DEFAULT_VARIANT}",
        f"privacy: {state.get('privacy') or DEFAULT_PRIVACY}",
        f"task model: {state.get('task_model') or IMAGE_MODEL}",
        f"reward model: {state.get('reward_model') or REWARD_MODEL_DEFAULT}",
        f"location: {state.get('location_notes') or 'unset'}",
    ]
    await update.effective_message.reply_text("\n".join(rows))


async def variant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Anatomy:", reply_markup=variant_keyboard(get_state(update.effective_user.id).get("variant")))


async def setvariant_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    variant = query.data.split("_", 1)[1]
    if variant not in HARLEY_VARIANTS:
        await query.edit_message_text("Unknown variant.")
        return
    get_state(update.effective_user.id)["variant"] = variant
    await query.edit_message_text(f"Variant set to {variant}.")


async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Outfit level:", reply_markup=privacy_keyboard(get_state(update.effective_user.id).get("privacy")))


async def privacy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    level = query.data.split("_", 1)[1]
    get_state(update.effective_user.id)["privacy"] = level
    await query.edit_message_text(f"Outfit level set to {level}.")


async def setreward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = getattr(update, "callback_query", None)
    state = get_state(update.effective_user.id)

    if query:
        await query.answer()
        data = query.data
    else:
        data = update.effective_message.text.strip()

    reply = query.edit_message_text if query else update.effective_message.reply_text

    if data in ("same", "setreward"):
        await reply(f"Reward model stays {state.get('reward_model') or REWARD_MODEL_DEFAULT}.")
        return
    if data not in MODEL_EXPLICIT:
        await reply(f"{data} cannot do the nude reward. Pick another.")
        return

    state["reward_model"] = data
    get_spec(nude=True, model=data)
    await reply(f"Reward model set to {data}.")


async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Task model:", reply_markup=model_keyboard(get_state(update.effective_user.id).get("task_model")))


async def setmodel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    model = query.data.split("_", 1)[1]
    get_state(update.effective_user.id)["task_model"] = model
    await query.edit_message_text(f"Task model set to {model}.")


# ============ TEST COMMANDS ============
async def payload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    spec = get_spec(state.get("variant"), nude=False, model=state.get("task_model"))
    prompt, negative = await build_prompt(user.id, nude=False)
    await update.effective_message.reply_text(
        f"model: {spec['model']}\n"
        f"box: {spec['width']}x{spec['height']}\n"
        f"steps: {spec['steps']} cfg: {spec['cfg']}\n"
        f"format: {spec['output_format']}\n\n"
        f"positive:\n{prompt}\n\n"
        f"negative:\n{negative or '(empty)'}"[:4000])


async def imgmodels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        data = await _venice_get("/models?type=image")
    except Exception as exc:
        await update.effective_message.reply_text(f"Could not list models: {exc}")
        return

    lines = []
    for item in data.get("data", []):
        spec = item.get("model_spec", {})
        lines.append(
            f"{item.get('id')}  res {spec.get('resolution') or '-'}  "
            f"steps_max {(spec.get('steps') or {}).get('max') or '-'}")
    await update.effective_message.reply_text(("\n".join(lines) or "empty")[:4000])


async def tryn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)

    await update.effective_message.reply_text("Running a task batch.")
    try:
        png = await _render_best(
            user.id, state.get("variant"), nude=False,
            room_notes=state.get("location_notes") or "")
    except Exception as exc:
        await update.effective_message.reply_text(f"Batch failed: {exc}")
        return
    await _debug_send(context.bot, user.id, png, label="tryn", upscale=True)


async def testreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)

    await update.effective_message.reply_text("Running a reward batch.")
    try:
        png = await _render_best(user.id, state.get("variant"), nude=True)
    except Exception as exc:
        await update.effective_message.reply_text(f"Batch failed: {exc}")
        return
    await _debug_send(context.bot, user.id, png, label="testreward", upscale=True, gate=True)


async def tryreward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    model = state.get("reward_model") or REWARD_MODEL_DEFAULT
    variant = state.get("variant")
    spec = get_spec(variant, nude=True, model=model)
    prompt, negative = await build_prompt(user.id, variant, nude=True)

    await update.effective_message.reply_text(f"Reward on {model}.")
    try:
        png = await _request_image(prompt, negative, spec)
    except Exception as exc:
        await update.effective_message.reply_text(f"Failed: {exc}")
        return
    if not png:
        await update.effective_message.reply_text("No image came back.")
        return
    await _debug_send(context.bot, user.id, png, label=f"reward_{model}", upscale=True, gate=True)


async def avatar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    pose = await generate_avatar_pose(user.id)

    try:
        png = await _render_best(
            user.id, state.get("variant"), nude=False,
            room_notes=f"{state.get('location_notes') or ''}\nPose: {pose}".strip())
    except Exception:
        logger.exception(f"avatar failed for {user.id}")
        await update.effective_message.reply_text("Could not get that shot. Try again.")
        return
    await send_avatar_photo(context.bot, update.effective_chat.id, user.id, png)


async def debugavatar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = get_state(user.id)
    pose = await generate_avatar_pose(user.id)
    try:
        png = await _render_best(user.id, state.get("variant"), nude=False, room_notes=pose)
    except Exception as exc:
        await update.effective_message.reply_text(f"Failed: {exc}")
        return
    await _debug_send(context.bot, user.id, png, label="avatar", upscale=True)


# ============ RESET ============
async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_state(update.effective_user.id)
    await update.effective_message.reply_text("Back to defaults.")


# ============ SCHEDULER ============
async def reward_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    for user_id in all_user_ids():
        state = get_state(user_id)
        if not state.get("_rewards_on", True):
            continue
        try:
            await deliver_reward(
                context.bot, user_id, user_id, state.get("variant"), caption="Your daily.")
        except Exception:
            logger.exception(f"scheduled reward failed for {user_id}")
        await asyncio.sleep(2)


# ============ ERRORS ============
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("handler error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Something broke on my end.")
        except Exception:
            pass


# ============ MAIN ============
def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is empty - check BOT_TOKEN, TELEGRAM_TOKEN or TELEGRAM_BOT_TOKEN")
    if not VENICE_API_KEY:
        raise SystemExit("VENICE_API_KEY is empty")

    ensure_schema()
    load_model_spec()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(180)
        .write_timeout(180)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("variant", variant_cmd))
    app.add_handler(CommandHandler("privacy", privacy_cmd))
    app.add_handler(CommandHandler("setreward", setreward_callback))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("imgmodels", imgmodels_cmd))
    app.add_handler(CommandHandler("payload", payload_cmd))
    app.add_handler(CommandHandler("location", location_cmd))
    app.add_handler(CommandHandler("clear", clear_location))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("avatar", avatar_cmd))
    app.add_handler(CommandHandler("debugavatar", debugavatar_cmd))
    app.add_handler(CommandHandler("tryn", tryn_cmd))
    app.add_handler(CommandHandler("testreward", testreward_cmd))
    app.add_handler(CommandHandler("tryreward", tryreward_cmd))

    app.add_handler(CallbackQueryHandler(venue_callback, pattern="^venue_"))
    app.add_handler(CallbackQueryHandler(room_callback, pattern="^room_"))
    app.add_handler(CallbackQueryHandler(present_callback, pattern="^present_"))
    app.add_handler(CallbackQueryHandler(probe_callback, pattern="^probe_"))
    app.add_handler(CallbackQueryHandler(clear_location, pattern="^loc_clear$"))
    app.add_handler(CallbackQueryHandler(setvariant_callback, pattern="^variant_"))
    app.add_handler(CallbackQueryHandler(privacy_callback, pattern="^privacy_"))
    app.add_handler(CallbackQueryHandler(setmodel_callback, pattern="^model_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_input_dispatcher))
    app.add_error_handler(error_handler)

    app.job_queue.run_daily(reward_job, time=dt_time(hour=REWARD_HOUR, minute=0))

    logger.info("setup completed")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()