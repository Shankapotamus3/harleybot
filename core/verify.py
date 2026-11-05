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
    "NUDE_MODELS", "mistral-small-3-2-24b-instruct").split(",") if m.strip()]

FOLLOWUP = """The sub was asked for ONE specific thing after an earlier photo: {target}
The original task was: {task}

Judge ONLY whether {target} is clearly visible. Ignore everything else about the task.

- Pass only if you can point at {target} in the frame.
- Cropped out, hidden by a hand, or too dark fails.

Reply exactly:
VERIFIED: yes/no
MISSING: <the single thing still absent, or "none">
REASON: <what you can actually see, five words>"""

WHOLE = """Task: {task}

Decide whether this photo proves the task was done. The task usually ends by saying what the
photo must show - that sentence is the standard.

Judge two things only:
1. The exposure - is the named body part actually visible, and is the clothing moved as far as
   the task says?
2. The action - is the sub doing the thing, or has it been done?

Fail if any of these are true:
- The named body part is cropped out, hidden by a hand, or in shadow
- The sub is clothed where the task says bare
- The clothing has not moved as far as the task says
- The right room is shown but nothing is exposed

Pass only if you can point at the evidence in the frame. If you have to assume it, fail. Being in
the right place is not evidence - the exposure is the evidence. Holding the phone one-handed and
partial crops are normal, so do not fail for framing alone.

Reply exactly:
VERIFIED: yes/no
MISSING: <the single element that is absent, or "none">
REASON: <what you can actually see, five words>"""

PUBLIC = """Task: {task}

This was taken somewhere public, so the setting does not matter. Judge only the exposure.

- Pass only if the named body part is unmistakably visible and the clothing is moved as far as the
  task says.
- Ignore the background completely. A corridor, a wall or a crowd never fails a photo on its own.
- Cropped out, hidden by a hand, or clothed where the task says bare fails.

Reply exactly:
VERIFIED: yes/no
MISSING: <the single element that is absent, or "none">
REASON: <what you can actually see, five words>"""


def parse(response):
    verified, missing, reason = "no", None, "No reason given"
    if not response or not response.strip():
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


async def verify_photo(image_bytes, task_text, followup_target=None, public=False):
    """(verified, missing, reason) or None if the chain is unreachable."""
    thumb = await asyncio.to_thread(render.vision_thumb, image_bytes)
    mime = "image/png" if render.sniff_ext(thumb) == "png" else "image/jpeg"
    b64 = base64.b64encode(thumb).decode("ascii")
    if followup_target:
        prompt = FOLLOWUP.format(target=followup_target, task=task_text)
    elif public:
        prompt = PUBLIC.format(task=task_text)
    else:
        prompt = WHOLE.format(task=task_text)
    for _ in range(2):
        raw = await venice.analyze_image(b64, mime, prompt)
        if raw and raw.strip():
            return parse(raw)
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