# core/storage.py
import io, time, random, asyncio, logging
from datetime import datetime, timezone
import cloudinary, cloudinary.uploader
from . import render

logger = logging.getLogger(__name__)


async def upload(image_bytes, user_id, image_type="user_upload", folder="telegram_bot"):
    try:
        buf = io.BytesIO(image_bytes) if isinstance(image_bytes, (bytes, bytearray)) else image_bytes
        buf.seek(0)
        public_id = (f"{folder}/user_{user_id}/{image_type}_"
                     f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{random.randint(1000, 9999)}")
        result = await asyncio.to_thread(
            cloudinary.uploader.upload, buf, public_id=public_id, resource_type="image",
            context={"user_id": str(user_id), "image_type": image_type},
            tags=[f"user_{user_id}", image_type, "telegram_bot"])
        return {"url": result.get("secure_url"), "public_id": result.get("public_id")}
    except Exception as e:
        logger.error(f"upload failed: {type(e).__name__}: {e}")
        return None


async def upload_png(png, user_id, prefix="render"):
    r = await upload(png, user_id, image_type=prefix)
    return r.get("url") if r else None


async def deliver(bot, chat_id, user_id, png, *, caption, reply_to=None, prefix="render"):
    png = await render.upscale(png)
    final = await asyncio.to_thread(render.finalize_png, png) or png
    url = await upload_png(final, user_id, prefix)
    kw = dict(caption=caption[:1024], reply_to_message_id=reply_to,
              write_timeout=180, read_timeout=180)
    if url:
        await bot.send_document(chat_id=chat_id, document=url, **kw)
    else:
        await bot.send_document(chat_id=chat_id, document=final,
                                filename=f"{prefix}_{int(time.time())}.png", **kw)
