# core/venice.py
import os, re, json, time, base64, logging, asyncio, threading
import requests

logger = logging.getLogger(__name__)

API = os.environ.get("VENICE_API_URL", "https://api.venice.ai/api/v1")
KEY = os.environ.get("VENICE_API_KEY", "")
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]*$")
_sem = threading.Semaphore(int(os.environ.get("IMAGE_CONCURRENCY", "4")))

IMAGE_FORMAT = os.environ.get("IMAGE_FORMAT", "png")
IMAGE_ASPECT = os.environ.get("IMAGE_ASPECT", "3:4")
IMAGE_RESOLUTION = os.environ.get("IMAGE_RESOLUTION", "2K")
IMAGE_WIDTH = int(os.environ.get("IMAGE_WIDTH", "960"))
IMAGE_HEIGHT = int(os.environ.get("IMAGE_HEIGHT", "1280"))
IMAGE_STEPS = os.environ.get("IMAGE_STEPS", "45")
IMAGE_CFG = os.environ.get("IMAGE_CFG", "7.5")
MINIMAL_PAYLOAD = os.environ.get("IMAGE_MINIMAL", "1") == "1"

TEXT_MODEL = os.environ.get("TEXT_MODEL", "venice-uncensored-role-play")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen-3-8-flash")
VISION_CHAIN = [m.strip() for m in os.environ.get(
    "VISION_CHAIN", f"{VISION_MODEL},google-gemma-4-26b-a4b-it").split(",") if m.strip()]
PROBE_MODELS = [m.strip() for m in os.environ.get(
    "PROBE_MODELS", "openai-gpt-oss-120b,gemma-4-31b-it").split(",") if m.strip()]

MODEL_EXPLICIT = {"lustify-v8", "chroma", "wai-illu"}
KNOWN_MODELS = {
    "grok-imagine-image": {"mode": "ratio",
                           "ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"], "res": ["1K", "2K"]},
    "nano-banana-pro": {"mode": "ratio",
                        "ratios": ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"], "res": ["1K", "2K"]},
    "chroma": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 10},
    "lustify-v8": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 50},
    "z-image-turbo": {"mode": "pixels", "max_w": 1280, "max_h": 1280, "steps_max": 8},
}

IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "chroma")
IMAGE_MODEL_REWARD = os.environ.get("IMAGE_MODEL_REWARD", "chroma")
IMAGE_FALLBACKS = [m.strip() for m in os.environ.get("IMAGE_FALLBACKS", "lustify-v8").split(",") if m.strip()]

_SPECS = {}


def headers():
    return {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


async def post(path, payload, timeout=60):
    r = await asyncio.to_thread(requests.post, f"{API}{path}", headers=headers(), json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text[:160]}")
    return r.json()


async def get(path, timeout=30):
    r = await asyncio.to_thread(requests.get, f"{API}{path}", headers=headers(), timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text[:160]}")
    return r.json()


def _fetch_spec(model):
    if model in _SPECS:
        return _SPECS[model]
    spec = {}
    try:
        r = requests.get(f"{API}/models?type=image", headers=headers(), timeout=30)
        if r.status_code == 200:
            payload = r.json()
            for m in (payload.get("data") or payload):
                if m.get("id") == model:
                    spec = m.get("model_spec") or {}
                    break
    except Exception as e:
        logger.warning(f"spec fetch {model}: {e}")
    _SPECS[model] = spec
    return spec


def get_spec(model, nude=False):
    m = model or (IMAGE_MODEL_REWARD if nude else IMAGE_MODEL)
    raw = _fetch_spec(m)
    c = raw.get("constraints") or {}
    known = KNOWN_MODELS.get(m, {})
    spec = {"model": m, "output_format": IMAGE_FORMAT, "mode": "pixels",
            "width": 0, "height": 0, "aspect_ratio": None, "resolution": None,
            "steps": None, "cfg": None}
    if c.get("aspectRatios") or (not c and known.get("mode") == "ratio"):
        ratios = c.get("aspectRatios") or known.get("ratios") or ["3:4"]
        spec["mode"] = "ratio"
        spec["aspect_ratio"] = IMAGE_ASPECT if IMAGE_ASPECT in ratios else ratios[0]
        res = c.get("resolutions") or known.get("res") or ["1K", "2K"]
        spec["resolution"] = IMAGE_RESOLUTION if IMAGE_RESOLUTION in res else res[-1]
    else:
        div = max(1, int(c.get("widthHeightDivisor") or 8))
        max_w = int(c.get("maxWidth") or known.get("max_w") or 1280)
        max_h = int(c.get("maxHeight") or known.get("max_h") or 1280)
        spec["width"] = max(div, (min(IMAGE_WIDTH, max_w) // div) * div)
        spec["height"] = max(div, (min(IMAGE_HEIGHT, max_h) // div) * div)
        res = c.get("resolutions")
        if res or c.get("defaultResolution"):
            res = res or ["1K", "2K"]
            spec["resolution"] = IMAGE_RESOLUTION if IMAGE_RESOLUTION in res else res[-1]
        max_steps = int((c.get("steps") or {}).get("max") or known.get("steps_max") or 30)
        if not MINIMAL_PAYLOAD:
            spec["steps"] = min(int(IMAGE_STEPS), max_steps)
            spec["cfg"] = float(IMAGE_CFG)
    return spec


def load_specs():
    for m in {IMAGE_MODEL, IMAGE_MODEL_REWARD}:
        logger.info(f"BOOT SPEC {m}: {json.dumps(_fetch_spec(m)) or 'EMPTY'}")


def payload(prompt, spec, negative=None):
    data = {"model": spec["model"], "prompt": prompt, "format": spec["output_format"], "safe_mode": False}
    if negative:
        data["negative_prompt"] = negative
    if spec["mode"] == "ratio":
        data["aspect_ratio"] = spec["aspect_ratio"]
        if spec["resolution"]:
            data["resolution"] = spec["resolution"]
    else:
        data["width"] = spec["width"]
        data["height"] = spec["height"]
        if spec["resolution"]:
            data["resolution"] = spec["resolution"]
        if spec["steps"]:
            data["steps"] = spec["steps"]
            data["cfg_scale"] = spec["cfg"]
    return data


def prune(data, body):
    try:
        err = json.loads(body)
    except Exception:
        return False
    issues = err.get("issues") or err.get("errors") or []
    if not isinstance(issues, list):
        return False
    changed = False
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        path = issue.get("path") or []
        keys = [str(k) for k in (issue.get("keys") or [])]
        if isinstance(path, list) and path:
            keys.append(str(path[-1]))
        if issue.get("code") in ("too_big", "too_small") and isinstance(path, list) and path:
            k = str(path[-1])
            bound = issue.get("maximum") if issue.get("code") == "too_big" else issue.get("minimum")
            if k in data and bound is not None:
                try:
                    data[k] = type(data[k])(bound)
                    changed = True
                    continue
                except Exception:
                    pass
        for k in keys:
            k = k.split(".")[-1]
            if k in data and k != "prompt":
                data.pop(k, None)
                changed = True
    return changed


def extract(result):
    if not result.get("images"):
        return None
    d = result["images"][0]
    if isinstance(d, bytes):
        return d
    if not isinstance(d, str):
        return None
    if d.startswith("http"):
        r = requests.get(d, timeout=60)
        return r.content if r.status_code == 200 else None
    if d.startswith("data:image"):
        return base64.b64decode(d.split(",", 1)[1])
    try:
        return base64.b64decode(d)
    except Exception:
        return None


def post_image(data, model):
    for attempt in range(3):
        try:
            with _sem:
                r = requests.post(f"{API}/image/generate", headers=headers(), json=data, timeout=180)
        except Exception as e:
            logger.error(f"image transport [{model}]: {e}")
            return None
        logger.info(f"image status [{model}]: {r.status_code}")
        if r.status_code == 200:
            return extract(r.json())
        if r.status_code == 429 and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 400 and attempt < 2 and prune(data, r.text):
            logger.warning(f"[{model}] pruned to {sorted(data.keys())}")
            continue
        logger.error(f"image error [{model}]: {r.status_code} - {r.text[:300]}")
        return None
    return None


async def request_image(prompt, negative, spec):
    chain = [spec["model"]] + [m for m in IMAGE_FALLBACKS if m != spec["model"]]
    for i, model in enumerate(chain):
        current = spec if i == 0 else get_spec(model)
        raw = await asyncio.to_thread(post_image, payload(prompt, current, negative), model)
        if raw:
            return raw
    return None


async def chat(messages, system, temperature=0.9, max_tokens=500):
    try:
        data = await post("/chat/completions", {
            "model": TEXT_MODEL,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "temperature": temperature, "max_tokens": max_tokens,
        }, timeout=30)
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"chat: {e}")
        return None


async def analyze_image(image_b64, mime, prompt):
    for model in VISION_CHAIN:
        try:
            data = await post("/chat/completions", {
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ]}],
                "max_tokens": 200,
            }, timeout=30)
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"vision {model}: {e}")
    return None


def parse_probe_lines(content):
    out, seen = [], set()
    for line in content.splitlines():
        t = re.sub(r"^[\-\*\d\.\)]+\s*", "", line.strip()).strip(" .")
        if not t or len(t) > 30 or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    return out


async def _probe_call(prompt, limit=8, minimum=4):
    for model in PROBE_MODELS:
        for attempt in range(2):
            try:
                r = await asyncio.to_thread(
                    requests.post, f"{API}/chat/completions", headers=headers(),
                    json={"model": model, "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.8, "max_tokens": 260}, timeout=25)
            except Exception as e:
                logger.warning(f"probe {model}: {e}")
                break
            if r.status_code == 429 and attempt == 0:
                await asyncio.sleep(1.5)
                continue
            if r.status_code != 200:
                break
            items = parse_probe_lines(r.json()["choices"][0]["message"]["content"])
            if len(items) >= minimum:
                return items[:limit]
            break
    return []


async def generate_probes(location, rooms=None, known=None):
    """What the room can carry, not what it contains."""
    known = known or []
    where = f"a {location}, specifically the {', '.join(rooms)}" if rooms else f"a {location}"
    avoid = f"\nDo NOT repeat any of these: {', '.join(known[:20])}." if known else ""
    prompt = (f"List 8 things in {where} that a photo task could be built around. "
              f"Think about what a task needs, not what the room contains: something to bend over, "
              f"something to hold or tie to, something to kneel on, a mirror at a usable angle, "
              f"a door that shuts, something overhead, a hard or cold surface. "
              f"Name the object and where it is: \"towel rail by the shower\". "
              f"Real and specific, one per line, no numbering.{avoid}")
    return await _probe_call(prompt, 8, 4)


async def generate_rooms(location):
    prompt = (f"List up to 8 parts of a {location} that a photo task could be set in. "
              f"Short labels, one to three words, the way someone who works there would name them: "
              f"\"the lane\", \"the shoe counter\", \"the car park\". "
              f"Only parts that actually exist in one. One per line, no numbering.")
    return await _probe_call(prompt, 8, 3)


async def generate_venue_typical(location):
    prompt = (f"List 8 physical things a {location} definitely has. Mundane and specific: "
              f"\"the ball return\", \"the scoring console\", \"the shoe rack\". "
              f"No abstractions, no room names, no furniture categories. "
              f"One per line, no numbering.")
    return await _probe_call(prompt, 8, 3)