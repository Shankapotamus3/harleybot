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

Judge ONLY whether {target} is clearly visible. Ignore everything else. Be lenient.

Line 1 must be exactly: YES or NO
Line 2: MISSING: <the single thing still not visible, or none>
Line 3: REASON: <short phrase>"""

WHOLE = """Task: {task}

Judge whether this photo shows the task was done. Be LENIENT:
- The sub is holding the phone one hand, so full-body framing is often impossible.
- Selfies mean they cannot see their own framing; partial crops are normal.
- Any equivalent item counts (any clamp = clothespin, any tie = rope).
- Judge only the PRIMARY element. If the primary element is present, pass.

Line 1 must be exactly: YES or NO
Line 2: MISSING: <the single most important element not visible, or none>
Line 3: REASON: <short phrase>"""

_VERIFIED = re.compile(r"VERIFIED\s*[:\-]\s*(yes|no)", re.IGNORECASE)
_MISSING = re.compile(r"MISSING\s*[:\-]\s*(.+)", re.IGNORECASE)
_REASON = re.compile(r"REASON\s*[:\-]\s*(.+)", re.IGNORECASE)
_BARE = re.compile(r"^\W*(yes|no)\b", re.IGNORECASE)


def parse(response):
    """(verified, missing, reason) or None if no verdict could be read."""
    if not response:
        return None
    verified, missing, reason = None, None, ""
    lines = response.splitlines()
    for line in lines:
        s = line.strip()
        m = _VERIFIED.search(s)
        if m:
            verified = m.group(1).lower()
            continue
        m = _MISSING.search(s)
        if m:
            val = m.group(1).strip().rstrip(".")
            if val.lower() not in ("none", "n/a", "-", "nothing", "no"):
                missing = val
            continue
        m = _REASON.search(s)
        if m:
            reason = m.group(1).strip()
    if verified is None:
        for line in lines[:3]:
            m = _BARE.search(line.strip())
            if m:
                verified = m.group(1).lower()
                break
    if verified is None:
        return None
    if not reason:
        first = next((l.strip() for l in lines if l.strip()), "")
        reason = first[:120] or "no reason given"
    return verified, missing, reason


async def verify_photo(image_bytes, task_text, followup_target=None):
    """Verdict tuple, or None if no model produced anything parseable."""
    thumb = await asyncio.to_thread(render.vision_thumb, image_bytes)
    mime = "image/png" if render.sniff_ext(thumb) == "png" else "image/jpeg"
    b64 = base64.b64encode(thumb).decode("ascii")
    prompt = (FOLLOWUP.format(target=followup_target, task=task_text)
              if followup_target else WHOLE.format(task=task_text))

    for model in venice.VISION_CHAIN:
        try:
            data = await venice.post("/chat/completions", {
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]}],
                "max_tokens": 200,
                "temperature": 0.0,
            }, timeout=30)
            raw = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            logger.warning(f"verify {model} transport: {e}")
            continue

        parsed = parse(raw)
        if parsed is None:
            logger.warning(f"verify {model} unparseable: {raw[:160]!r}")
            continue
        logger.info(f"verify {model}: {parsed[0]} ({parsed[2][:60]})")
        return parsed

    logger.error("verify: no model returned a readable verdict")
    return None


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