import re

SELFIE_INVISIBLE = {"phone", "the phone", "your phone", "mobile", "smartphone",
                    "camera", "the camera"}
MIRROR_HINTS = ("mirror", "reflection", "reflected", "in the glass", "at the basin")


def needs_mirror(task_text):
    t = (task_text or "").lower()
    return any(h in t for h in MIRROR_HINTS)


def unachievable(item, task_text):
    return item.lower().strip(" .") in SELFIE_INVISIBLE and not needs_mirror(task_text)


def split_missing(missing):
    return [p.strip(" .") for p in re.split(r",|\band\b", missing or "") if p.strip(" .")]


def usable_missing(missing, task_text):
    """Misses the sub physically cannot frame, removed."""
    return [p for p in split_missing(missing) if not unachievable(p, task_text)]