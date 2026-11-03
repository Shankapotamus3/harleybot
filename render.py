# core/render.py
import os, io, base64, logging, asyncio
import requests
from io import BytesIO
from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError
from . import venice

logger = logging.getLogger(__name__)

RENDER_ATTEMPTS = int(os.environ.get("RENDER_ATTEMPTS", "8"))
MUSH_BYTES_PER_PX = float(os.environ.get("MUSH_BYTES_PER_PX", "1.0"))
SHARPNESS_FLOOR = float(os.environ.get("SHARPNESS_FLOOR", "90"))
MEASURE_EDGE = 1024
VISION_EDGE = 768
MAX_SIDE = int(os.environ.get("IMAGE_MAX_SIDE", "1920"))
SHARPEN = os.environ.get("IMAGE_SHARPEN", "1") == "1"

MODEL_MIN_SHARPNESS = {
    "lustify-v8": float(os.environ.get("LUSTIFY_MIN_SHARPNESS", "120")),
    "chroma": float(os.environ.get("CHROMA_MIN_SHARPNESS", "60")),
    "grok-imagine-image": float(os.environ.get("GROK_MIN_SHARPNESS", "250")),
    "z-image-turbo": float(os.environ.get("ZIMAGE_MIN_SHARPNESS", "250")),
    "nano-banana-pro": float(os.environ.get("NANO_MIN_SHARPNESS", "250")),
}


def sniff_ext(b):
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
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
    s = MEASURE_EDGE / max(w, h)
    if s < 1:
        g = g.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    lap = g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return float(ImageStat.Stat(lap).var[0])


def check_render(source):
    """(ok, reason). Accepts bytes or (w, h)."""
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


def mushy(raw):
    try:
        with Image.open(BytesIO(raw)) as im:
            px = im.width * im.height
    except Exception:
        return False
    return px > 0 and (len(raw) / px) < MUSH_BYTES_PER_PX


def vision_thumb(raw):
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


def finalize_png(raw):
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        logger.error(f"undecodable: {e}")
        return None
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    if SHARPEN:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=50, threshold=3))
    out = BytesIO()
    img.save(out, format="PNG", compress_level=1)
    return out.getvalue()


async def upscale(raw):
    """Server-side detail pass. Falls back to the raw bytes on any failure."""
    try:
        scale = int(os.environ.get("IMAGE_UPSCALE", "2") or 0)
    except ValueError:
        scale = 2
    if scale <= 0:
        return raw
    try:
        r = await asyncio.to_thread(
            requests.post, f"{venice.API}/image/upscale", headers=venice.headers(),
            json={"image": base64.b64encode(raw).decode("ascii"), "scale": scale,
                  "creativity": float(os.environ.get("IMAGE_UPSCALE_CREATIVITY", "0.18"))},
            timeout=180)
        if r.status_code != 200:
            logger.warning(f"upscale {r.status_code}, keeping raw")
            return raw
        if "json" in r.headers.get("content-type", ""):
            d = r.json().get("image")
            if isinstance(d, dict):
                d = d.get("b64_json") or d.get("base64") or ""
            return base64.b64decode(d) if d else raw
        return r.content
    except Exception as e:
        logger.warning(f"upscale failed, keeping raw: {e}")
        return raw


async def render_batch(spec, build, count):
    prompt, negative = await build()

    async def one():
        raw = await venice.request_image(prompt, negative, spec)
        if not raw:
            return (0.0, b"", "no image")
        if MUSH_BYTES_PER_PX and mushy(raw):
            logger.info(f"mushy draw on {spec['model']}, re-rolling once")
            raw = await venice.request_image(prompt, negative, spec)
            if not raw:
                return (0.0, b"", "no image after reroll")
        ok, reason = check_render(raw)
        if not ok:
            return (0.0, raw, reason)
        return (sharpness_score(raw), raw, None)

    gathered = await asyncio.gather(*(one() for _ in range(count)), return_exceptions=True)
    scored = [g for g in gathered if not isinstance(g, Exception)]
    for g in gathered:
        if isinstance(g, Exception):
            logger.warning(f"render raised: {g}")
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored


async def render_best(spec, build):
    """Best of a batch, one extra batch if everything lands under the floor."""
    floor = MODEL_MIN_SHARPNESS.get(spec["model"], SHARPNESS_FLOOR)
    for attempt in (1, 2):
        batch = await render_batch(spec, build, RENDER_ATTEMPTS)
        clean = [row for row in batch if row[2] is None and row[1]]
        passing = [row for row in clean if row[0] >= floor]
        if passing:
            score, png, _ = passing[0]
            logger.info(f"attempt {attempt}: best {score:.1f} of {len(batch)}")
            return png
        if clean:
            score, png, _ = clean[0]
            logger.info(f"attempt {attempt}: nothing over floor, keeping {score:.1f}")
            return png
        logger.warning(f"attempt {attempt}: nothing usable")
    raise RuntimeError("render failed")


def dump_debug(raw, user_id, kind="raw"):
    try:
        with open(f"/tmp/{kind}_{user_id}.bin", "wb") as f:
            f.write(raw)
    except Exception as e:
        logger.warning(f"dump failed: {e}")


async def debug_send(bot, png, *, label, chat_id=None, upscale_pass=False, gate=None):
    chat_id = chat_id or int(os.environ.get("DEBUG_CHAT_ID", "0") or 0)
    if not chat_id:
        return
    try:
        with Image.open(BytesIO(png)) as im:
            dims, px = f"{im.width}x{im.height}", im.width * im.height
        lines = [f"label: {label}", f"dims: {dims}", f"bytes: {len(png)}",
                 f"bpp: {(len(png) / px if px else 0):.3f}", f"sharpness: {sharpness_score(png):.1f}"]
        if gate:
            lines.append(f"gate: {gate}")
        payload = png
        if upscale_pass:
            payload = await upscale(png)
            lines.append(f"upscaled bytes: {len(payload)}")
        await bot.send_document(chat_id=chat_id, document=payload,
                                filename=f"{label}_{int(__import__('time').time())}.png",
                                caption="\n".join(lines)[:1024],
                                write_timeout=180, read_timeout=180)
    except Exception:
        logger.exception(f"debug_send failed for {label}")