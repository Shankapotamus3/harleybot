# core/verify.py
import os, re, base64, logging, asyncio
from . import venice, render

logger = logging.getLogger(__name__)
NUDE_GATE = os.environ.get("NUDE_GATE", "0") == "1"

NUDE_SYSTEM = ("You are a strict image classifier. Reply with exactly one word. NUDE if the visible "
               "torso is bare from chest to hips and nothing covers the crotch. COVERED if clothing, "
               "underwear, a hand, a prop or a leg hides the crotch. UNSURE if framing makes it "
               "impossible to tell.")

NUDE_MODELS = [m.strip() for m in os.environ.get(
    "NUDE_MODELS", "e2ee-qwen3-vl-30b-a3b-instruct,qwen3-vl-235b-a22b-instruct").split(",") if m.strip()]

FOLLOWUP = """The sub was asked for ONE specific thing after an earlier photo: {target}
The original task was: {task}

Judge ONLY whether {target} is clearly visible. Ignore everything else about the task. Be lenient.

Reply exactly:
VERIFIED: yes/no
MISSING: <the single thing still not visible, or "none">
REASON: brief"""

WHOLE = """Task: {task}

Judge whether this photo shows the task was done. Be LENIENT:
- The sub is holding the phone in one hand, so full-body framing is often impossible.
- Selfies mean they cannot see their own framing; partial crops are normal.
- Any equivalent item counts (any clamp = clothespin, any tie = rope).
- Judge only the PRIMARY element. If the primary element is present, pass.

Reply exactly:
VERIFIED: yes/no
MISSING: <the single most important element not visible, or "none">
REASON: brief"""


def parse(response):
    verified, missing, reason = "no", None, "No reason given"
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
            val = m.group(1).strip().rstrip(".")
            missing = None if val.lower() in ("none", "n/a", "-", "nothing") else val
            continue
        m = re.match(r"REASON:\s*(.+)", line, re.IGNORECASE)
        if m:
            reason = m.group(1).strip()
    return verified, missing, reason


async def verify_photo(image_bytes, task_text, followup_target=None):
    """(verified, missing, reason) or None if the chain is unreachable."""
    thumb = await asyncio.to_thread(render.vision_thumb, image_bytes)
    mime = "image/png" if render.sniff_ext(thumb) == "png" else "image/jpeg"
    b64 = base64.b64encode(thumb).decode("ascii")
    prompt = (FOLLOWUP.format(target=followup_target, task=task_text)
              if followup_target else WHOLE.format(task=task_text))
    raw = await venice.analyze_image(b64, mime, prompt)
    if raw is None:
        return None
    return parse(raw)


async def nude_check(png_bytes):
    if not NUDE_GATE:
        return "UNSURE"
    b64 = base64.b64encode(png_bytes).decode("ascii")
    for model in NUDE_MODELS:
        try:
            data = await venice.post("/chat/completions", {
                "model": model, "max_tokens": 200, "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": NUDE_SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Classify this image."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]},
                ],
            }, timeout=25)
            text = (data["choices"][0]["message"]["content"] or "").strip().upper()
        except Exception as e:
            logger.warning(f"nude_check {model}: {e}")
            continue
        if "NUD" in text:
            return "NUDE"
        if "COVER" in text:
            return "COVERED"
        return "UNSURE"
    return "UNSURE"