#!/usr/bin/env python3
"""
Harley Quinn Bot - playful, chaotic, dangerously fun.
Merged build: Harley persona via Venice character_slug, fixed avatar, real vision verification.
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

from PIL import Image, ImageFilter, ImageEnhance, UnidentifiedImageError

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
CHARACTER_SLUG = os.getenv("CHARACTER_SLUG", "harley-quinn-1205").strip()
TEXT_MODEL = os.getenv("TEXT_MODEL", "claude-opus-4-8-fast").strip()
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "nano-banana-pro").strip()
IMAGE_MODEL_REWARD = os.getenv("IMAGE_MODEL_REWARD", "").strip()
IMAGE_FALLBACKS = [m.strip() for m in os.getenv("IMAGE_FALLBACKS", "chroma").split(",") if m.strip()]
IMAGE_FORMAT = os.getenv("IMAGE_FORMAT", "png").strip()
IMAGE_UPSCALE = os.getenv("IMAGE_UPSCALE", "off").strip()
IMAGE_ASPECT = os.getenv("IMAGE_ASPECT", "3:4").strip()
IMAGE_SHARPEN = os.getenv("IMAGE_SHARPEN", "1").strip()
MODEL_SPEC = {}
MODEL_SPECS = {}

SHARP_TAIL = "editorial portrait photograph, sharp focus on the eyes and face, deep focus, natural skin texture, crisp fabric detail, flattering directional light, professional studio lighting, 85mm lens"
NEGATIVE_PROMPT = "blurry, soft focus, bokeh, shallow focus, out of focus, motion blur, low detail, smeared, airbrushed, smooth plastic skin, beauty filter, harsh flash, blown out highlights, overexposed, grainy"
NEGATIVE_PROMPT_NUDE = "briefs, boxers, panties, knickers, trousers, jeans, skirt, towel, sheet, sarong, loincloth, drapery, fabric over the groin, clothing over the groin, censored, mosaic, partially covered, cropped at the hips"
STUDIO = "against a seamless charcoal studio backdrop, subtle rim light from behind"
SOFT_WORDS = re.compile(
    r"\b(soft focus|shallow depth of field|bokeh|dreamy|hazy|misty|film grain|"
    r"cinematic glow|soft lighting|glowing|ethereal)\b", re.IGNORECASE)

HARLEY_BASE = "Harley Quinn, white skin, blonde hair in pigtails with red and blue dyed tips, athletic slim build, red and black jester makeup, blue and red eyeshadow"

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