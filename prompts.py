# core/prompts.py
import re

SOFT_WORDS = re.compile(
    r"\b(soft focus|shallow depth of field|bokeh|dreamy|hazy|misty|film grain|"
    r"cinematic glow|soft lighting|glowing|ethereal)\b", re.IGNORECASE)

SHARP_TAIL = "sharp focus, natural skin texture, directional studio light, 85mm"
NEGATIVE_PROMPT = "blurry, soft focus, bokeh, plastic skin, extra fingers, fused fingers"
NEGATIVE_PROMPT_SEXY = "stiff catalogue pose, stock photo, mannequin, plastic skin, extra fingers, fused fingers"
NEGATIVE_PROMPT_NUDE = ("briefs, boxers, panties, knickers, trousers, jeans, skirt, towel, sheet, "
                        "sarong, loincloth, drapery, fabric over the groin, clothing over the groin, "
                        "censored, mosaic, partially covered")
NEGATIVE_HARD = ", any clothing, underwear, bra, panties, fabric, boots, stockings"
STUDIO = "against a seamless charcoal studio backdrop, subtle rim light from behind"
NUDE = "fully nude except for"


def _clean(prompt):
    cleaned = SOFT_WORDS.sub("", prompt)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip().strip(",").strip()
    return f"{cleaned}, {SHARP_TAIL}"


def build(subject, *, nude, pose="", gear="", clause="", scene="", extra_negative=""):
    """subject is app-supplied fragments. Returns (positive, negative)."""
    bits = [b for b in subject if b]
    if nude:
        if gear:
            bits.append(f"{NUDE} {gear}")
        if clause:
            bits.append(clause)
        if pose:
            bits.append(pose)
        negative = NEGATIVE_PROMPT_NUDE + extra_negative
    else:
        if pose:
            bits.append(pose)
        negative = NEGATIVE_PROMPT_SEXY + NEGATIVE_HARD + extra_negative
    if scene:
        bits.append(scene)
    bits.append(STUDIO)
    return _clean(", ".join(bits)), negative