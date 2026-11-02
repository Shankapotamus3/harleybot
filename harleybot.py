# harleybot.py
"""
Harley Quinn task bot.

Changelog
---------
- Env-driven tuning restored: IMAGE_CFG, IMAGE_STEPS, IMAGE_MAX_SIDE,
  IMAGE_UPSCALE, IMAGE_UPSCALE_CREATIVITY, per-model floors.
- Token resolution accepts BOT_TOKEN / TELEGRAM_BOT_TOKEN / TELEGRAM_TOKEN /
  TOKEN; loud boot failure naming the vars that do exist.
- Vision: default model in code, fallback chain, word-boundary verdict parse,
  max_tokens 64, directive prompt.
- Imaging contract inlined: PNG only, native boxes, unclipped 1024-edge
  Laplacian, best-of-N selection, upscale-the-winner.
- Reward path requires BARE and filters the chain to explicit-capable models.
- Negative prompt split: imaging vocabulary for sharpness, garment nouns
  only where clothing is actually being enforced.
- Kids Present removed; boot migration maps existing rows to 'alone'.
- arm_timeout cancels duplicate timeout jobs; auto_clear_task guards on
  expires_at.
- get_spec caches on 200 only and prefetches off the event loop.
- scheduler skips users without gender/outfit/location and rolls back per user.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import random
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import httpx
from PIL import Image, ImageFilter, ImageStat
from sqlalchemy import (
    BigInteger, Column, DateTime, Float, Integer, String, Text, create_engine,
    select,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("harley")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TOKEN")
    or ""
).strip()

OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///harley.db")

TASK_TTL_HOURS = int(os.getenv("TASK_TTL_HOURS", "12"))
TASK_IDLE_HOURS = int(os.getenv("TASK_IDLE_HOURS", "6"))
SCHEDULER_INTERVAL = int(os.getenv("SCHEDULER_INTERVAL", "1800"))
TASKS_PER_REWARD = int(os.getenv("TASKS_PER_REWARD", "5"))
MAX_REWORKS = 3
BOOTSTRAP_RETRIES = int(os.getenv("BOOTSTRAP_RETRIES", "3"))

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "lustify-v8")
IMAGE_MODEL_REWARD = os.getenv("IMAGE_MODEL_REWARD", "lustify-v8")

# --------------------------------------------------------------------------- #
# imaging  ---- BEGIN ----
# --------------------------------------------------------------------------- #

API_BASE = "https://api.venice.ai/api/v1"
GENERATE_URL = f"{API_BASE}/image/generate"
UPSCALE_URL = f"{API_BASE}/image/upscale"

VENICE_KEY = (
    os.getenv("VENICE_API_KEY") or os.getenv("VENICE_KEY") or ""
).strip()

VISION_CHAIN = [m for m in (
    os.getenv("VENICE_VISION_MODEL", "e2ee-qwen3-vl-30b-a3b-p"),
    "qwen-3-8-flash",
    "google-gemma-4-26b-a4b-it",
) if m]

MAX_PARALLEL = 4
MEASURE_EDGE = 1024
TARGET_LONG_EDGE = 1920
DEFAULT_SAMPLER = "DPM++ 2M"

IMAGE_CFG = _env_float("IMAGE_CFG", 5.0)
IMAGE_STEPS = int(_env_float("IMAGE_STEPS", 32))
IMAGE_MAX_SIDE = int(_env_float("IMAGE_MAX_SIDE", 1280))
IMAGE_UPSCALE = _env_float("IMAGE_UPSCALE", 1.5)
IMAGE_UPSCALE_CREATIVITY = _env_float("IMAGE_UPSCALE_CREATIVITY", 0.2)

ASPECT_BOXES = {
    "portrait": (960, 1280),
    "square": (1280, 1280),
    "landscape": (1280, 960),
}
MODEL_RENDER_MULT = {"lustify-v8": IMAGE_UPSCALE}
MODEL_STEPS: dict[str, int] = {}
MODEL_CFG: dict[str, float] = {}


def steps_for(model: str) -> int:
    return MODEL_STEPS.get(model, IMAGE_STEPS)


def cfg_for(model: str) -> float:
    return MODEL_CFG.get(model, IMAGE_CFG)


def mult_for(model: str) -> float:
    return MODEL_RENDER_MULT.get(model, IMAGE_UPSCALE)


MODEL_CHOICES = [
    "lustify-v8", "chroma", "wai-illu", "grok-imagine-image-2-0",
    "nano-banana-pro", "z-image-turbo", "gpt-image-2",
]
MODEL_EXPLICIT = {"lustify-v8", "chroma", "wai-illu"}
MODEL_NO_NEGATIVE = {"gpt-image-2", "gpt-image-2-edit"}
MODEL_NO_SAMPLER = {"gpt-image-2", "gpt-image-2-edit", "nano-banana-pro"}

MODEL_MIN_SHARPNESS = {
    "lustify-v8": _env_float("LUSTIFY_MIN_SHARPNESS", 100.0),
    "chroma": _env_float(
        "CHROMA_MIN_SHARPNESS", _env_float('MODEL_MIN_SHARPNESS["chroma"]', 60.0)
    ),
    "submit": _env_float("SUBMIT_MIN_SHARPNESS", 45.0),
}
DEFAULT_MIN_SHARPNESS = _env_float("DEFAULT_MIN_SHARPNESS", 100.0)

LAST_IMAGE_ERROR = ""


class RenderFailed(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors[-3:]) or "render failed")


class _HTTPError(RuntimeError):
    def __init__(self, status: int, detail: str, retryable: bool):
        self.status = status
        self.retryable = retryable
        super().__init__(f"HTTP {status}: {detail}")


def sharpness_score(img: Image.Image) -> float:
    g = img.convert("L")
    w, h = g.size
    scale = MEASURE_EDGE / max(w, h)
    if scale < 1:
        g = g.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                     Image.LANCZOS)
    lap = g.filter(ImageFilter.Kernel(
        (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128
    ))
    return float(ImageStat.Stat(lap).var[0])


def _box(aspect: str, model: str) -> tuple[int, int]:
    b = ASPECT_BOXES.get(aspect, ASPECT_BOXES["portrait"])
    m = mult_for(model)
    w, h = int(b[0] * m), int(b[1] * m)
    over = max(w, h) / IMAGE_MAX_SIDE
    if over > 1:
        w, h = int(w / over), int(h / over)
    return (w, h)


def _target(size: tuple[int, int]) -> tuple[int, int]:
    w, h = size
    s = TARGET_LONG_EDGE / max(w, h)
    return (max(1, round(w * s)), max(1, round(h * s)))


def _payload(model, prompt, negative, box, seed=None) -> dict:
    body = {
        "model": model,
        "prompt": prompt,
        "width": box[0],
        "height": box[1],
        "steps": steps_for(model),
        "cfg_scale": cfg_for(model),
        "output_format": "png",
        "hide_watermark": True,
        "safe_mode": False,
    }
    if negative and model not in MODEL_NO_NEGATIVE:
        body["negative_prompt"] = negative
    if model not in MODEL_NO_SAMPLER:
        body["sampler"] = DEFAULT_SAMPLER
    if seed is not None:
        body["seed"] = seed
    return body


def _decode(data: str) -> bytes:
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def _http_error(r: httpx.Response) -> _HTTPError:
    try:
        d = r.json().get("details") or {}
    except Exception:
        d = {}
    msg = str(d.get("error") or d.get("message") or r.text[:200])
    retry = d.get("retryable")
    if retry is None:
        retry = r.status_code in (429, 500, 502, 503, 504)
    return _HTTPError(r.status_code, msg, bool(retry))


class Client:
    def __init__(self) -> None:
        self._c = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=20.0),
            headers={"Authorization": f"Bearer {VENICE_KEY}"},
        )
        self._sem = asyncio.Semaphore(MAX_PARALLEL)

    async def close(self) -> None:
        await self._c.aclose()

    async def _post(self, url: str, body: dict) -> dict:
        last: Optional[_HTTPError] = None
        for attempt in range(BOOTSTRAP_RETRIES):
            async with self._sem:
                r = await self._c.post(url, json=body)
            if r.status_code == 200:
                return r.json()
            err = _http_error(r)
            last = err
            if not err.retryable:
                raise err
            await asyncio.sleep(1.5 * (attempt + 1))
        raise last or RuntimeError("request failed")

    async def generate(self, body: dict) -> bytes:
        data = await self._post(GENERATE_URL, body)
        images = data.get("images") or []
        if not images:
            raise RuntimeError("empty images array")
        return _decode(images[0])

    async def upscale(self, png: bytes, factor: float) -> bytes:
        data = await self._post(UPSCALE_URL, {
            "image": base64.b64encode(png).decode(),
            "scale": round(factor, 2),
            "creativity": IMAGE_UPSCALE_CREATIVITY,
        })
        raw = data.get("image") or (data.get("images") or [None])[0]
        if not raw:
            raise RuntimeError("empty upscale response")
        return _decode(raw)

    async def chat_vision(self, model: str, prompt: str, png: bytes) -> str:
        uri = "data:image/png;base64," + base64.b64encode(png).decode()
        data = await self._post(f"{API_BASE}/chat/completions", {
            "model": model,
            "max_tokens": 64,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": uri}},
            ]}],
        })
        return data["choices"][0]["message"]["content"].strip()


NUDE_PROMPT = (
    "Look at the lower half of this image. Answer with one word only. "
    "BARE = genitals or pubic area visible. "
    "COVERED = clothing, underwear or fabric hides them. "
    "UNSURE = cannot tell. "
    "Answer:"
)

_VERDICT_RE = re.compile(r"\b(BARE|COVERED|UNSURE)\b")


async def nude_check(client: Client, png: bytes) -> str:
    for model in VISION_CHAIN:
        try:
            text = (await client.chat_vision(model, NUDE_PROMPT, png)).upper()
        except Exception as exc:
            log.warning("nude_check %s failed: %s", model, exc)
            continue
        log.info("nude_check %s reply %r", model, text[:80])
        m = _VERDICT_RE.search(text)
        if m:
            return m.group(1)
        log.info("nude_check %s unparsed", model)
    return "UNSURE"


def _load(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    return img.convert("RGB")


async def _deliver(client, img, png, target):
    if max(img.size) >= max(target):
        return img, png
    factor = max(target) / max(img.size)
    try:
        up = _load(await client.upscale(png, factor))
        if max(up.size) >= max(target):
            buf = io.BytesIO()
            up.save(buf, "PNG", compress_level=1)
            return up, buf.getvalue()
    except Exception as exc:
        log.warning("upscale failed (%s), local fallback", exc)
    out = img.resize(target, Image.LANCZOS)
    out = out.filter(ImageFilter.UnsharpMask(1.2, 50, 3))
    buf = io.BytesIO()
    out.save(buf, "PNG", compress_level=1)
    return out, buf.getvalue()


async def render_batch(model: str, prompt: str, negative: Optional[str],
                       n: int, aspect: str = "portrait",
                       client: Optional[Client] = None):
    """Raw harness: every draw with its score, no floor, no selection."""
    own = client is None
    client = client or Client()
    box = _box(aspect, model)
    try:
        res = await asyncio.gather(
            *(client.generate(_payload(model, prompt, negative, box))
              for _ in range(n)),
            return_exceptions=True,
        )
        out = []
        for item in res:
            if isinstance(item, Exception):
                out.append((0.0, b"", str(item)))
                continue
            try:
                img = _load(item)
            except Exception as exc:
                out.append((0.0, b"", f"decode {exc}"))
                continue
            out.append((sharpness_score(img), item, ""))
        return out
    finally:
        if own:
            await client.close()


async def render_best(prompt: str, negative: Optional[str], *, chain: Sequence[str],
                      aspect: str = "portrait", attempts: int = 8, rounds: int = 2,
                      floor: Optional[float] = None, require_bare: bool = False,
                      client: Optional[Client] = None):
    own = client is None
    client = client or Client()
    errors: list[str] = []
    total = 0
    try:
        for rnd in range(rounds):
            for model in chain:
                box = _box(aspect, model)
                min_sharp = floor if floor is not None else MODEL_MIN_SHARPNESS.get(
                    model, DEFAULT_MIN_SHARPNESS)
                res = await asyncio.gather(
                    *(client.generate(_payload(model, prompt, negative, box))
                      for _ in range(attempts)),
                    return_exceptions=True,
                )
                total += attempts
                cands = []
                hard_stop = False
                for item in res:
                    if isinstance(item, Exception):
                        errors.append(f"{model}: {item}")
                        if isinstance(item, _HTTPError) and item.status == 422:
                            hard_stop = True
                        continue
                    try:
                        img = _load(item)
                    except Exception as exc:
                        errors.append(f"{model}: decode {exc}")
                        continue
                    score = sharpness_score(img)
                    log.info("%s r%d sharp %.1f bytes %d size %s",
                             model, rnd, score, len(item), img.size)
                    if score >= min_sharp:
                        cands.append((score, item, img))
                if hard_stop and not cands:
                    log.info("%s hard stop (422)", model)
                    continue
                if not cands:
                    continue
                cands.sort(key=lambda c: c[0], reverse=True)

                chosen = None
                if require_bare:
                    for score, png, img in cands[:3]:
                        verdict = await nude_check(client, png)
                        log.info("%s cand %.1f %s", model, score, verdict)
                        if verdict == "BARE":
                            chosen = (score, png, img, verdict)
                            break
                    if chosen is None:
                        continue
                else:
                    score, png, img = cands[0]
                    chosen = (score, png, img, "SKIPPED")

                score, png, img, verdict = chosen
                final_img, final_png = await _deliver(client, img, png, _target(img.size))
                log.info("delivered %s %.1f %s %s", model, score, verdict, final_img.size)
                return {
                    "png": final_png, "image": final_img, "sharpness": score,
                    "verdict": verdict, "model": model, "attempts": total,
                    "errors": errors,
                }
        raise RenderFailed(errors)
    finally:
        if own:
            await client.close()

# --------------------------------------------------------------------------- #
# imaging  ---- END ----
# --------------------------------------------------------------------------- #

SHARP_TAIL = ", sharp focus, crisp edges, high microcontrast, visible skin texture"

NEGATIVE_PROMPT = (
    "clothing covering the body, blurry, out of focus, soft focus, "
    "shallow depth of field, hazy, low contrast, washed out, plastic skin, "
    "airbrushed, extra limbs, bad anatomy"
)

# imaging vocabulary carries the sharpness; garment nouns only where needed
NEGATIVE_PROMPT_NUDE = (
    "clothing, dress, jeans, leggings, stockings, boots, crop top, bra, "
    "underwear, fabric folds, harness straps across hips, "
    "blurry, out of focus, soft focus, shallow depth of field, motion blur, "
    "hazy, milky, low contrast, washed out, oversmooth, plastic skin, "
    "airbrushed, extra limbs, bad anatomy"
)

REWARD_GEAR = ["a black leather collar", "studded cuffs", "a chest strap"]
REWARD_CLAUSE = (
    "nothing on but {gear}, bare shoulders, bare thighs, defined collarbone, "
    "visible skin pores"
)

IDENTITY = (
    "Harley Quinn, pale skin, blonde pigtails with red and blue tips, "
    "smudged makeup, small diamond tattoo under the eye"
)

OUTFIT_OPTIONS = {
    "classic": "red and black jester costume with a corset",
    "casual": "oversized crop top and shorts",
    "formal": "fitted black dress",
    "gym": "sports bra and leggings",
}

CONTEXT_OPTIONS = {
    "alone": "alone",
    "partner": "with {partner}",
    "crowd": "in a crowded room",
}

Base = declarative_base()
_engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)
Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


class UserState(Base):
    __tablename__ = "user_states"
    user_id = Column(BigInteger, primary_key=True)
    step = Column(String(32), default="gender")
    gender = Column(String(16))
    outfit = Column(String(16))
    location = Column(String(128))
    context = Column(String(16), default="alone")
    partner = Column(String(64))
    avatar_slot = Column(Integer, default=0)
    owner_set = Column(Integer, default=0)
    completed = Column(Integer, default=0)
    reward_streak = Column(Integer, default=0)
    last_task_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    kind = Column(String(16), default="task")
    text = Column(Text)
    status = Column(String(16), default="pending")
    reworks = Column(Integer, default=0)
    blocked_count = Column(Integer, default=0)
    blocked_task_id = Column(Integer)
    sharpness = Column(Float)
    verdict = Column(String(16))
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)


class TaskHistory(Base):
    __tablename__ = "task_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    task_id = Column(Integer)
    action = Column(String(32))
    detail = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserImage(Base):
    __tablename__ = "user_images"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    task_id = Column(Integer)
    file_id = Column(String(256))
    sharpness = Column(Float)
    verdict = Column(String(16))
    created_at = Column(DateTime, default=datetime.utcnow)


class AvatarImage(Base):
    __tablename__ = "avatar_images"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    slot = Column(Integer, default=0)
    file_id = Column(String(256))
    created_at = Column(DateTime, default=datetime.utcnow)


class LocationConstraint(Base):
    __tablename__ = "location_constraints"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    label = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow)


@contextmanager
def db():
    s = Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_owner(uid: int) -> bool:
    return OWNER_ID == 0 or uid == OWNER_ID


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #

def arm_timeout(context: ContextTypes.DEFAULT_TYPE, task_id: int, user_id: int):
    name = f"timeout:{task_id}"
    for job in context.job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    context.job_queue.run_once(
        expire_task, when=timedelta(hours=TASK_TTL_HOURS),
        name=name, data={"task_id": task_id, "user_id": user_id},
    )


async def expire_task(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    with db() as s:
        task = s.get(Task, data["task_id"])
        if not task or task.status != "pending":
            return
        if aware(task.expires_at) and aware(task.expires_at) > aware(utcnow()):
            return
        task.status = "expired"
    try:
        await context.bot.send_message(
            data["user_id"], "That one timed out. /task for a new one."
        )
    except Exception:
        pass


def auto_clear_task(task: Task):
    task.status = "done"
    task.expires_at = None


# --------------------------------------------------------------------------- #
# keyboards
# --------------------------------------------------------------------------- #

def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows]
    )


def setup_gender_kb():
    return kb([[("Female", "g:f"), ("Male", "g:m"), ("Any", "g:a")]])


def setup_outfit_kb():
    return kb([[(k.title(), f"o:{k}")] for k in OUTFIT_OPTIONS])


def setup_context_kb():
    return kb([[(v.split("{")[0].title(), f"c:{k}")] for k, v in CONTEXT_OPTIONS.items()])


def task_kb(task_id: int):
    return kb([[
        ("Rework", f"rw:{task_id}"),
        ("Blocked", f"bl:{task_id}"),
        ("Done", f"dn:{task_id}"),
    ]])


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #

def build_prompt(state: UserState, *, reward: bool) -> str:
    parts = [f"Photorealistic portrait of {IDENTITY}"]
    if state.gender == "m":
        parts.append("masculine presentation")
    elif state.gender == "f":
        parts.append("feminine presentation")
    if state.outfit and state.outfit in OUTFIT_OPTIONS and not reward:
        parts.append(f"wearing {OUTFIT_OPTIONS[state.outfit]}")
    parts.append("cropped at mid-thigh")
    if reward:
        gear = random.choice(REWARD_GEAR)
        parts.append(REWARD_CLAUSE.format(gear=gear))
    ctx = CONTEXT_OPTIONS.get(state.context or "alone", "alone")
    if state.context == "partner" and state.partner:
        ctx = ctx.format(partner=state.partner)
    parts.append(ctx)
    if state.location and state.location != "Unknown":
        parts.append(f"in {state.location}")
    parts.append("hard key light, 85mm")
    return ", ".join(parts) + SHARP_TAIL


TASK_VERBS = [
    "standing against a wall, chin lifted",
    "sitting on the floor, knees drawn up",
    "leaning in a doorway, arms crossed",
    "mid-turn, glancing back over a shoulder",
    "crouched, weight on one hip",
    "stretching, arms overhead",
]


def generate_task_text(state: UserState) -> str:
    verb = random.choice(TASK_VERBS)
    where = state.location if state.location and state.location != "Unknown" else "the room"
    who = "alone"
    if state.context == "partner" and state.partner:
        who = f"with {state.partner}"
    elif state.context == "crowd":
        who = "with people around"
    return (
        f"Take a photo {verb} in {where}, {who}. "
        f"Same framing as the reference, mid-thigh up."
    )


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as s:
        st = s.get(UserState, uid)
        if not st:
            st = UserState(user_id=uid, step="gender")
            s.add(st)
        else:
            st.step = "gender"
    await update.message.reply_text(
        "Let's set you up. Gender presentation?", reply_markup=setup_gender_kb()
    )


async def setup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    with db() as s:
        st = s.get(UserState, uid)
        if not st:
            st = UserState(user_id=uid, step="gender")
            s.add(st)

        if data.startswith("g:"):
            st.gender = data.split(":")[1]
            st.step = "outfit"
            await q.edit_message_text("Usual outfit?", reply_markup=setup_outfit_kb())
            return

        if data.startswith("o:"):
            st.outfit = data.split(":")[1]
            st.step = "location"
            await q.edit_message_text(
                "Where do you usually shoot? Send a city or place name."
            )
            return

        if data.startswith("c:"):
            st.context = data.split(":")[1]
            if st.context == "partner":
                st.step = "partner"
                await q.edit_message_text("Partner's name?")
                return
            st.step = "done"
            await q.edit_message_text(
                "Ready. You'll get a task every few hours. /task to start now."
            )
            return


async def setup_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    with db() as s:
        st = s.get(UserState, uid)
        if not st:
            return False
        if st.step == "location":
            st.location = text[:128]
            st.step = "context"
            s.add(LocationConstraint(user_id=uid, label=text[:128]))
            await update.message.reply_text(
                "Who's around?", reply_markup=setup_context_kb()
            )
            return True
        if st.step == "partner":
            st.partner = text[:64]
            st.step = "done"
            await update.message.reply_text("Ready. /task to start.")
            return True
    return False


# --------------------------------------------------------------------------- #
# task creation
# --------------------------------------------------------------------------- #

async def issue_task(update_or_chat, context, uid: int, kind: str = "task"):
    with db() as s:
        st = s.get(UserState, uid)
        if not st or not st.gender or not st.outfit or not st.location:
            return None
        text = generate_task_text(st)
        task = Task(
            user_id=uid, kind=kind, text=text,
            expires_at=utcnow() + timedelta(hours=TASK_TTL_HOURS),
        )
        s.add(task)
        s.flush()
        st.last_task_at = utcnow()
        tid = task.id
    arm_timeout(context, tid, uid)
    chat_id = getattr(update_or_chat, "chat_id", None) or getattr(
        getattr(update_or_chat, "chat", None), "id", uid)
    await context.bot.send_message(chat_id, f"*{kind.upper()}*\n{text}",
                                   parse_mode=ParseMode.MARKDOWN)
    return tid


async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as s:
        pending = s.scalar(select(Task).where(
            Task.user_id == uid, Task.status == "pending"))
        if pending:
            await update.message.reply_text("You already have one open.")
            return
    if not await issue_task(update.message, context, uid):
        await update.message.reply_text("Finish setup first: /start")
        return
    with db() as s:
        t = s.scalar(select(Task).where(Task.user_id == uid, Task.status == "pending"))
        if t:
            await update.message.reply_text("Send your photo.",
                                            reply_markup=task_kb(t.id))


async def scheduler_tick(context: ContextTypes.DEFAULT_TYPE):
    with db() as s:
        users = s.scalars(select(UserState)).all()
        due = []
        for st in users:
            if not (st.gender and st.outfit and st.location):
                continue
            if st.last_task_at and aware(st.last_task_at) > aware(
                    utcnow() - timedelta(hours=TASK_IDLE_HOURS)):
                continue
            open_task = s.scalar(select(Task).where(
                Task.user_id == st.user_id, Task.status == "pending"))
            if open_task:
                continue
            due.append(st.user_id)
        for uid in due:
            try:
                if await issue_task(None, context, uid):
                    await context.bot.send_message(uid, "Send your photo.")
            except Exception as exc:
                log.warning("scheduler %s: %s", uid, exc)
                s.rollback()


# --------------------------------------------------------------------------- #
# photo handling
# --------------------------------------------------------------------------- #

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as s:
        task = s.scalar(select(Task).where(
            Task.user_id == uid, Task.status == "pending").order_by(Task.id.desc()))
        if not task:
            await update.message.reply_text("No open task. /task")
            return
        tid, kind = task.id, task.kind

    f = await update.message.photo[-1].get_file()
    raw = bytes(await f.download_as_bytearray())
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        img = img.convert("RGB")
    except Exception:
        await update.message.reply_text("Couldn't read that image.")
        return

    score = sharpness_score(img)
    floor = MODEL_MIN_SHARPNESS.get("submit", DEFAULT_MIN_SHARPNESS)
    log.info("submission sharp %.1f", score)

    with db() as s:
        s.add(UserImage(user_id=uid, task_id=tid, file_id=update.message.photo[-1].file_id,
                        sharpness=score))

    if score < floor:
        await update.message.reply_text(
            f"Too soft ({score:.0f}). Brace your phone and reshoot.",
            reply_markup=task_kb(tid),
        )
        return

    verdict = "SKIPPED"
    if kind == "reward":
        client = Client()
        try:
            buf = io.BytesIO()
            img.save(buf, "PNG", compress_level=1)
            verdict = await nude_check(client, buf.getvalue())
        finally:
            await client.close()

    with db() as s:
        task = s.get(Task, tid)
        task.sharpness = score
        task.verdict = verdict
        task.status = "review"
        s.add(TaskHistory(user_id=uid, task_id=tid, action="submit",
                          detail=f"{score:.1f} {verdict}"))

    if kind == "reward" and verdict != "BARE":
        await update.message.reply_text(
            f"Sharp ({score:.0f}) but the camera can't see bare skin — {verdict}. "
            "Rework it.",
            reply_markup=task_kb(tid),
        )
        return

    await update.message.reply_text(
        f"Good — {score:.0f}, {verdict}.", reply_markup=task_kb(tid)
    )


# --------------------------------------------------------------------------- #
# task callbacks
# --------------------------------------------------------------------------- #

async def task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    action, raw_id = q.data.split(":")
    tid = int(raw_id)

    with db() as s:
        task = s.get(Task, tid)
        if not task or task.user_id != uid:
            return
        if task.status not in ("pending", "review"):
            await q.edit_message_text("That one's closed.")
            return

        if action == "rw":
            if task.reworks >= MAX_REWORKS:
                task.status = "blocked"
                task.blocked_task_id = tid
                st = s.get(UserState, uid)
                st.step = "blocker"
                await q.edit_message_text("Three reworks. What's blocking you?")
                return
            task.reworks += 1
            task.status = "pending"
            task.expires_at = utcnow() + timedelta(hours=TASK_TTL_HOURS)
            s.add(TaskHistory(user_id=uid, task_id=tid, action="rework",
                              detail=str(task.reworks)))
            await q.edit_message_text(
                f"Rework {task.reworks}/{MAX_REWORKS}. Reshoot and resend."
            )
            arm_timeout(context, tid, uid)
            return

        if action == "bl":
            task.status = "blocked"
            st = s.get(UserState, uid)
            st.step = "blocker"
            await q.edit_message_text("What's blocking you?")
            return

        if action == "dn":
            auto_clear_task(task)
            st = s.get(UserState, uid)
            st.completed = (st.completed or 0) + 1
            st.reward_streak = (st.reward_streak or 0) + 1
            s.add(TaskHistory(user_id=uid, task_id=tid, action="done"))
            earned = st.reward_streak >= TASKS_PER_REWARD
            if earned:
                st.reward_streak = 0
            await q.edit_message_text("Logged.")
            if earned:
                await issue_task(q.message, context, uid, kind="reward")
            return


async def blocker_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    with db() as s:
        st = s.get(UserState, uid)
        if not st or st.step != "blocker":
            return False
        st.step = "done"
        task = s.scalar(select(Task).where(
            Task.user_id == uid, Task.status == "blocked").order_by(Task.id.desc()))
        if not task:
            return False
        task.blocked_count = (task.blocked_count or 0) + 1
        task.status = "done"
        task.blocked_task_id = None
    await update.message.reply_text(f"Noted: {update.message.text[:200]}")
    await issue_task(update.message, context, uid)
    return True


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await setup_text(update, context):
        return
    if await blocker_input(update, context):
        return


# --------------------------------------------------------------------------- #
# image generation
# --------------------------------------------------------------------------- #

async def send_render(chat, context, prompt, negative, chain, *, bare):
    global LAST_IMAGE_ERROR
    try:
        res = await render_best(prompt, negative, chain=chain, require_bare=bare)
    except RenderFailed as exc:
        LAST_IMAGE_ERROR = "; ".join(exc.errors[-3:])
        log.warning("render failed: %s", LAST_IMAGE_ERROR)
        await context.bot.send_message(chat, f"All image models failed.\n{LAST_IMAGE_ERROR}")
        return
    await context.bot.send_document(
        chat, document=io.BytesIO(res["png"]),
        filename=f"{res['model']}_{int(res['sharpness'])}.png",
        caption=f"{res['model']} · {res['sharpness']:.0f} · {res['verdict']}",
    )


async def tryn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    model = args[0] if args else IMAGE_MODEL
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5
    with db() as s:
        st = s.get(UserState, update.effective_user.id) or UserState(user_id=0)
    prompt = build_prompt(st, reward=False)
    await update.message.reply_text(f"{model} x{n}\nfloor 0 (harness)")
    batch = await render_batch(model, prompt, NEGATIVE_PROMPT, n)
    for score, png, err in batch:
        if err:
            await update.message.reply_text(f"ERR {err[:120]}")
            continue
        await update.message.reply_document(
            io.BytesIO(png), filename=f"{score:.0f}.png",
            caption=f"{score:.1f} · {len(png)}B",
        )


async def tryreward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as s:
        st = s.get(UserState, update.effective_user.id) or UserState(user_id=0)
    prompt = build_prompt(st, reward=True)
    chain = [m for m in [IMAGE_MODEL_REWARD] if m in MODEL_EXPLICIT]
    await send_render(update.effective_chat.id, context, prompt,
                      NEGATIVE_PROMPT_NUDE, chain, bare=True)


async def testreward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tryreward(update, context)


async def imgmodels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API_BASE}/models", params={"type": "image"},
                        headers={"Authorization": f"Bearer {VENICE_KEY}"})
    if r.status_code != 200:
        await update.message.reply_text(f"HTTP {r.status_code}")
        return
    lines = []
    for m in r.json().get("data", []):
        mid = m.get("id", "?")
        spec = m.get("model_spec", {}) or {}
        lines.append(
            f"{mid} | max {spec.get('maxWidth')}x{spec.get('maxHeight')} "
            f"| res {'yes' if spec.get('resolutionOptions') else '-'} "
            f"| {'EXPLICIT' if mid in MODEL_EXPLICIT else ''}"
        )
    await update.message.reply_text("\n".join(lines)[:3900])


async def set_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [[(m, f"sm:{m}")] for m in MODEL_CHOICES]
    await update.message.reply_text("Reward model?", reply_markup=kb(rows))


async def model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IMAGE_MODEL_REWARD
    q = update.callback_query
    await q.answer()
    IMAGE_MODEL_REWARD = q.data.split(":", 1)[1]
    await q.edit_message_text(f"Reward model: {IMAGE_MODEL_REWARD}")


async def debugavatar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as s:
        st = s.get(UserState, uid)
        avatars = s.scalars(select(AvatarImage).where(
            AvatarImage.user_id == uid)).all()
        if not st:
            await update.message.reply_text("No state.")
            return
        await update.message.reply_text(
            f"step={st.step} gender={st.gender} outfit={st.outfit} "
            f"loc={st.location} ctx={st.context} done={st.completed} "
            f"streak={st.reward_streak} avatars={len(avatars)}"
        )


async def resetowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as s:
        st = s.get(UserState, uid)
        if st:
            st.owner_set = 0
    await update.message.reply_text("Owner cleared.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("handler error", exc_info=context.error)


# --------------------------------------------------------------------------- #
# boot
# --------------------------------------------------------------------------- #

def migrate_kids_context():
    Base.metadata.create_all(_engine)
    with db() as s:
        for st in s.scalars(select(UserState)).all():
            if st.context == "kids":
                st.context = "alone"


async def post_init(app: Application):
    migrate_kids_context()
    app.job_queue.run_repeating(scheduler_tick, interval=SCHEDULER_INTERVAL,
                                first=60, name="scheduler")
    log.info(
        "booted cfg=%.1f steps=%d max_side=%d upscale=%.2f",
        IMAGE_CFG, IMAGE_STEPS, IMAGE_MAX_SIDE, IMAGE_UPSCALE,
    )


def main():
    if not BOT_TOKEN:
        present = sorted(k for k in os.environ if "TOKEN" in k.upper())
        raise SystemExit(f"BOT_TOKEN empty. Token-ish vars present: {present}")
    if not VENICE_KEY:
        raise SystemExit("VENICE_API_KEY empty.")
    log.info("token %s…%s", BOT_TOKEN[:4], BOT_TOKEN[-4:])
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("tryn", tryn))
    app.add_handler(CommandHandler("tryreward", tryreward))
    app.add_handler(CommandHandler("testreward", testreward))
    app.add_handler(CommandHandler("imgmodels", imgmodels))
    app.add_handler(CommandHandler("model", set_model))
    app.add_handler(CommandHandler("debugavatar", debugavatar))
    app.add_handler(CommandHandler("resetowner", resetowner))
    app.add_handler(CallbackQueryHandler(setup_callback, pattern=r"^[goc]:"))
    app.add_handler(CallbackQueryHandler(model_callback, pattern=r"^sm:"))
    app.add_handler(CallbackQueryHandler(task_callback, pattern=r"^(rw|bl|dn):\d+$"))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()