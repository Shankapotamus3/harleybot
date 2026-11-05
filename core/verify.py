# core/verify.py
import re, base64, logging, asyncio
from . import venice, render

logger = logging.getLogger(__name__)

FOLLOWUP = """The sub was asked for ONE specific thing after an earlier photo: {target}
The original task was: {task}

Judge ONLY whether every item in that list is clearly visible. Ignore everything else about the task.

- All of them or it fails. One present and one absent is a fail.
- Cropped out, hidden by a hand, too dark, or partly visible but unclear all count as absent.

Reply exactly:
VERIFIED: yes/no
MISSING: <every absent item, comma separated, or "none">
REASON: <what you can actually see, five words>"""

WHOLE = """Task: {task}

Decide whether this photo proves the task was done.

Work in three steps, in order:

1. ELEMENTS: list every element the task requires to be visible - the named body part, the state
   of the clothing, and anything the final sentence says the photo must show. Comma separated.
2. Check each one against the photo. Partly visible but unclear counts as absent.
3. All of them or it fails. Two of three elements is a no.

Fail if any element is absent, cropped out, hidden by a hand, in shadow, or still covered.
Being in the right place is not evidence - the exposure is the evidence. Holding the phone
one-handed and partial crops are normal, so do not fail for framing alone.

Reply exactly:
ELEMENTS: <the list you made>
VERIFIED: yes/no
MISSING: <every absent element, comma separated, or "none">
REASON: <what you can actually see, five words>"""

PUBLIC = """Task: {task}

This was taken somewhere public, so the setting does not matter. Judge only the exposure.

Work in three steps, in order:

1. ELEMENTS: list every element the task requires to be visible - the named body part, the state
   of the clothing, and anything the final sentence says the photo must show. Comma separated.
2. Check each one against the photo. Partly visible but unclear counts as absent.
3. All of them or it fails.

Ignore the background completely. A corridor, a wall or a crowd never fails a photo on its own.

Reply exactly:
ELEMENTS: <the list you made>
VERIFIED: yes/no
MISSING: <every absent element, comma separated, or "none">
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
    if verified == "yes" and missing:
        verified = "no"
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