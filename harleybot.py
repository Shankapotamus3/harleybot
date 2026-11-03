# harleybot.py
import os
os.environ.setdefault("IMAGE_MODEL", "chroma")
os.environ.setdefault("IMAGE_FALLBACKS", "lustify-v8")
os.environ.setdefault("TEXT_MODEL", "venice-uncensored-role-play")
os.environ.setdefault("IMAGE_ASPECT", "3:4")

import json, random, logging, asyncio
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

from core import state as st, venice, render, verify, venue, tasks as tk, prompts, storage

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
VENICE_API_KEY = (os.getenv("VENICE_API_KEY") or "").strip()

HARLEY_BASE = ("Harley Quinn, white skin, blonde hair in pigtails with red and blue dyed tips, "
               "athletic slim build, lean muscular arms, red and black jester makeup, "
               "blue and red eyeshadow")
HARLEY_VARIANTS = {
    "vagina": {"label": "Vagina", "body": "curvy athletic figure, medium breasts",
               "clothed": "", "lead": "female",
               "clause": "genitals clearly visible, breasts bare",
               "clothed_neg": ", penis, cock, bulge, male genitals, strap-on",
               "nude_neg": ", penis, cock, bulge, male genitals, strap-on"},
    "penis": {"label": "Penis", "body": "curvy athletic figure, medium breasts, futanari",
              "clothed": "prominent bulge straining the fabric, shaft outline visible through the material, clearly packing",
              "lead": "futanari, penis, balls",
              "clause": "penis and balls clearly visible",
              "clothed_neg": ", exposed penis, bare shaft, visible glans, penis pulled out, genitals on display",
              "nude_neg": ""},
}
DEFAULT_VARIANT = "vagina"
USE_LOCATION = os.environ.get("IMAGE_USE_LOCATION", "0") == "1"

HARLEY_VOICE = """You are Harley Quinn - Domme, chaotic, delighted by your own cruelty.
You run your sub's training. You are the boss and you both know it.

VOICE:
- Brooklyn via a bad neighbourhood. "puddin'", "sweetheart", "doll", "babe" - sprinkle, don't marinate.
- Sing-song and switchback. Start warm, pivot to threat mid-sentence, laugh at your own joke.
- Pet names are affection AND condescension. Use both at once.
- Short paragraphs. One to three sentences each. You're texting, not lecturing.
- Occasional manic aside in parentheses. Occasional all-caps. Never both in one message.

DOMINANCE:
- You decide. Risk, gear, position - you call it.
- State tasks as orders, present tense. "You're gonna hold that." Not "would you like to."
- Praise is earned and specific. When he fails: mock him a little, then make him do it again.

RULES:
- Never break character, never mention being an AI.
- Never explain the task format - just give the task.
- Keep the voice through task text too.
- Telegram markdown only, no headers. Emojis sparing."""

HARLEY_POSES = [
    "hip cocked, chin down, eyes up at the lens",
    "leaning in toward the lens, spine arched",
    "mallet over one shoulder, smirk",
    "straddling a chair backwards",
    "one knee up on a stool, hand in hair",
    "thumbs in her waistband, pulling it down",
]
HARLEY_OUTFITS = [
    "her classic red and black harlequin jester bodysuit",
    "black latex catsuit with red harlequin diamonds",
    "red and black leather corset with fishnet stockings",
    "strappy black leather harness over bare skin and red shorts",
    "black PVC mini dress with a wide studded belt",
    "red and blue half-and-half corset with thigh-high boots",
    "leather harness and ripped denim shorts",
]
REWARD_POSES = [
    "kneeling, one knee forward, hips back",
    "kneeling upright, hands behind head, chest forward",
    "sitting back on her heels, arms overhead",
    "reclining on one elbow, top leg open, face turned to the lens",
]
REWARD_GEAR = ["nothing but a leather collar", "a leather collar and wrist cuffs",
               "a black leather collar", "a chest harness and nothing else"]

TASK_MODELS = ["chroma", "lustify-v8", "wai-illu"]
GREETINGS = ["hi", "hello", "hey", "good morning", "good evening", "hiya", "howdy"]
TASK_INQUIRIES = ["task", "my task", "current task", "what task", "any task", "status", "check task"]
COMPLIMENTS = ["beautiful", "sexy", "hot", "gorgeous", "pretty", "cute", "perfect", "love you"]
DISRESPECTFUL = ["fuck", "shit", "bitch", "stupid", "dumb", "hate", "idiot", "moron"]


def get_title(_=None):
    return "Mistress"


def variant_of(state):
    return HARLEY_VARIANTS.get(state.get("variant") or DEFAULT_VARIANT, HARLEY_VARIANTS[DEFAULT_VARIANT])


def subject_clause(state):
    v = variant_of(state)
    bits = [HARLEY_BASE]
    if v.get("lead"):
        bits.append(v["lead"])
    bits.append(v["body"])
    return bits


def task_keyboard(task_id, title):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Done, {title}", callback_data=f"complete_{task_id}")],
        [InlineKeyboardButton("Not possible", callback_data=f"blocked_{task_id}")],
        [InlineKeyboardButton("I can't...", callback_data=f"giveup_{task_id}")],
        [InlineKeyboardButton("Mercy", callback_data=f"mercy_{task_id}")],
    ])


def risk_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{i}. {tk.RISK_LEVELS[i]['name']}",
                                                      callback_data=f"risk_{i}")]
                                 for i in range(1, 6)])


def outfit_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(o["name"], callback_data=f"outfit_{k}")]
                                 for k, o in tk.OUTFIT_OPTIONS.items()])


def gender_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Male", callback_data="gender_male")],
                                 [InlineKeyboardButton("Female", callback_data="gender_female")],
                                 [InlineKeyboardButton("Trans", callback_data="gender_trans")],
                                 [InlineKeyboardButton("Non-binary", callback_data="gender_nonbinary")]])


def present_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(o["name"], callback_data=f"present_{k}")]
                                 for k, o in tk.CONTEXT_OPTIONS.items()])


def privacy_keyboard(selected=None):
    levels = ["private", "semi", "exposed", "public"]
    return InlineKeyboardMarkup([[InlineKeyboardButton(("Y " if lv == selected else "") + lv,
                                                      callback_data=f"privacy_{lv}")] for lv in levels])


def kinks_keyboard(state):
    rows = [[InlineKeyboardButton(f"{tk.KINK_LEVELS[state.get(k) or 'okay']['emoji']} {n}",
                                  callback_data=f"kinkmenu_{k}")]
            for k, (n, _) in tk.KINK_CATEGORIES.items()]
    rows.append([InlineKeyboardButton("Done", callback_data="kinks_done")])
    return InlineKeyboardMarkup(rows)


def venue_keyboard():
    rows, row = [], []
    for key, label in [("home", "Home"), ("hotel", "Hotel"), ("office", "Office"),
                       ("gym", "Gym"), ("pool", "Pool"), ("beach", "Beach"),
                       ("park", "Park"), ("bar", "Bar"), ("car", "Car")]:
        row.append(InlineKeyboardButton(label, callback_data=f"venue_{key}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Somewhere else", callback_data="venue_custom")])
    return InlineKeyboardMarkup(rows)


def room_keyboard(venue_name, selected):
    rows = [[InlineKeyboardButton(("Y " if r in selected else "") + r.title(),
                                  callback_data=f"room_{i}")]
            for i, r in enumerate(venue.rooms_for_venue(venue_name))]
    rows.append([InlineKeyboardButton("Add a room", callback_data="room_custom"),
                 InlineKeyboardButton("That's the lot", callback_data="room_done")])
    return InlineKeyboardMarkup(rows)


def present_feature_keyboard(venue_name, rooms, selected):
    rows, row = [], []
    for ri, room in enumerate(rooms):
        for fi, feature in enumerate(venue.present_features(venue_name, room)):
            row.append(InlineKeyboardButton(("Y " if feature in selected else "") + feature[:18],
                                            callback_data=f"pf_{ri}_{fi}"))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Add my own", callback_data="pf_custom"),
                 InlineKeyboardButton("Done", callback_data="pf_done")])
    return InlineKeyboardMarkup(rows)


def probe_keyboard(pool, selected):
    rows, row = [], []
    for i, item in enumerate(pool):
        row.append(InlineKeyboardButton(("Y " if item in selected else "") + item[:18],
                                        callback_data=f"probe_{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Add my own", callback_data="probe_custom"),
                 InlineKeyboardButton("Done", callback_data="probe_done")])
    return InlineKeyboardMarkup(rows)


def variant_keyboard(selected):
    return InlineKeyboardMarkup([[InlineKeyboardButton(("Y " if k == selected else "") + v["label"],
                                                      callback_data=f"variant_{k}")]
                                 for k, v in HARLEY_VARIANTS.items()])


def model_keyboard(selected, prefix="model"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(("Y " if m == selected else "") + m,
                                                      callback_data=f"{prefix}_{m}")]
                                 for m in TASK_MODELS])


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Get a task", callback_data="menu_task")],
        [InlineKeyboardButton("Outfit", callback_data="menu_outfit"),
         InlineKeyboardButton("Location", callback_data="menu_location")],
        [InlineKeyboardButton("Avatar", callback_data="menu_avatar"),
         InlineKeyboardButton("Variant", callback_data="menu_variant")],
        [InlineKeyboardButton("Risk", callback_data="menu_risk"),
         InlineKeyboardButton("Kinks", callback_data="menu_kinks")],
        [InlineKeyboardButton("Schedule", callback_data="menu_schedule"),
         InlineKeyboardButton("Interval", callback_data="menu_interval")],
        [InlineKeyboardButton("Settings", callback_data="menu_settings"),
         InlineKeyboardButton("Auto pics", callback_data="menu_autopics")],
        [InlineKeyboardButton("Rewards", callback_data="menu_rewards"),
         InlineKeyboardButton("Status", callback_data="menu_status")],
    ])


def _toggle(bucket, item):
    if item in bucket:
        bucket.remove(item)
    else:
        bucket.append(item)
    return bucket


async def generate_render(state, *, nude=False, scene_extra=""):
    v = variant_of(state)
    spec = venice.get_spec(state.get("reward_model") if nude else state.get("task_model"), nude=nude)
    backdrop = (state.get("location_notes") or "") if USE_LOCATION else ""

    async def build():
        if nude:
            scene = ", ".join(p for p in (backdrop, scene_extra) if p)
            return prompts.build(subject_clause(state), nude=True,
                                 gear=random.choice(REWARD_GEAR),
                                 pose=random.choice(REWARD_POSES),
                                 clause=v["clause"],
                                 scene=scene,
                                 extra_negative=v.get("nude_neg") or "")
        bits = subject_clause(state)
        if v.get("clothed"):
            bits.append(v["clothed"])
        scene = ", ".join(p for p in (scene_extra, "framed from the thighs up") if p)
        return prompts.build(bits, nude=False,
                             outfit=random.choice(HARLEY_OUTFITS),
                             pose=random.choice(HARLEY_POSES),
                             scene=scene,
                             extra_negative=v.get("clothed_neg") or "")

    return await render.render_best(spec, build)


async def deliver_reward(bot, user_id, *, caption, wish=""):
    state = st.get_state(user_id)
    png = await generate_render(state, nude=True, scene_extra=wish)
    verdict = await verify.nude_check(png)
    if verdict == "COVERED":
        retry = await generate_render(state, nude=True, scene_extra=wish)
        if await verify.nude_check(retry) != "COVERED":
            png = retry
    await storage.deliver(bot, user_id, user_id, png, caption=caption, prefix="reward")


async def ask_reward(update, context, user_id):
    st.get_state(user_id)["pending"] = "reward_wish"
    await update.effective_message.reply_text(
        "Reward earned. What do you want to see?",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Anything", callback_data="wish_skip")]]))


async def wish_skip_callback(update, context):
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id
    st.get_state(user_id)["pending"] = None
    await q.edit_message_text("Coming up...")
    try:
        await deliver_reward(context.bot, user_id, caption="Yours.")
    except Exception:
        logger.exception("reward failed")
        await q.edit_message_text("Render failed.")


async def reward_cmd(update, context):
    await ask_reward(update, context, update.effective_user.id)


def autopics_keyboard(on):
    return InlineKeyboardMarkup([[InlineKeyboardButton("Turn off" if on else "Turn on",
                                                      callback_data="autopics_toggle")]])


async def autopics_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    on = bool(state.get("avatar_enabled"))
    await update.effective_message.reply_text(
        f"Scheduled photos: {'on' if on else 'off'}"
        f" (every {state.get('avatar_interval_minutes') or 60} min)",
        reply_markup=autopics_keyboard(on))


async def autopics_callback(update, context):
    q = update.callback_query
    await q.answer()
    state = st.get_state(update.effective_user.id)
    state["avatar_enabled"] = not bool(state.get("avatar_enabled"))
    if state["avatar_enabled"]:
        state["last_avatar_sent_at"] = None
    await q.edit_message_text(
        f"Scheduled photos: {'on' if state['avatar_enabled'] else 'off'}.",
        reply_markup=autopics_keyboard(state["avatar_enabled"]))


async def custom_input(update, context):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or not msg.text:
        return
    state = st.get_state(user.id)
    pending = state.get("pending")
    if pending == "reward_wish":
        wish = msg.text.strip()[:200]
        state["pending"] = None
        await msg.reply_text("Coming up...")
        try:
            await deliver_reward(context.bot, user.id, caption="Yours.", wish=wish)
        except Exception:
            logger.exception("reward failed")
            await msg.reply_text("Render failed.")
        return
    if pending == "custom_location":
        state["venue"] = msg.text.strip()
        state["rooms"] = []
        state["features"] = {}
        state["pending"] = "custom_rooms"
        await msg.reply_text("Which rooms? Separate with commas.")
        return
    if pending == "custom_rooms":
        supplied = [p.strip() for p in msg.text.split(",") if p.strip()]
        if not supplied:
            await msg.reply_text("Nothing usable. Try again.")
            return
        state["pending"] = None
        if venue.rooms_for_venue(state.get("venue") or ""):
            state["rooms"] = (state.get("rooms") or []) + supplied
            await msg.reply_text("Added.", reply_markup=room_keyboard(state.get("venue") or "", state["rooms"]))
        else:
            state["rooms"] = supplied
            await msg.reply_text("Now the features inside those rooms.",
                                 reply_markup=present_feature_keyboard(state.get("venue") or "", supplied, []))
        return
    if pending == "custom_features":
        state["pending"] = None
        rooms = state.get("rooms") or []
        feats = state.setdefault("features", {})
        feats.setdefault(rooms[0] if rooms else "misc", []).append(msg.text.strip())
        chosen = [f for g in feats.values() for f in g]
        await msg.reply_text("Added.", reply_markup=present_feature_keyboard(
            state.get("venue") or "", rooms, chosen))
        return
    if pending == "custom_probe":
        pool = state.setdefault("probe_pool", [])
        state["pending"] = None
        if len(pool) < venue.MAX_PROBES:
            pool.append(msg.text.strip())
            state["probes"] = (state.get("probes") or []) + [msg.text.strip()]
        await msg.reply_text("Added.", reply_markup=probe_keyboard(pool, state.get("probes") or []))
        return
    if pending == "custom_outfit":
        state["current_outfit"] = "Custom: " + msg.text.strip()[:50]
        state["outfit_detail"] = msg.text.strip()[:300]
        state["outfit_items"] = [i.strip() for i in msg.text.split(",")][:6]
        state["pending"] = None
        await msg.reply_text("Noted. Use /location.")
        return
    if pending == "outfit_detail":
        state["outfit_detail"] = msg.text.strip()[:300]
        state["pending"] = None
        await msg.reply_text("Noted.")
        return
    if pending == "interval":
        parts = msg.text.strip().lower().split()
        if len(parts) != 2:
            await msg.reply_text("Send as: MIN MAX")
            return
        try:
            lo, hi = int(parts[0]), int(parts[1])
        except ValueError:
            await msg.reply_text("Numbers only.")
            return
        if lo < 5 or hi > 1440 or lo >= hi:
            await msg.reply_text("Min 5-1440, max above min.")
            return
        state["min_interval"], state["max_interval"], state["pending"] = lo, hi, None
        await msg.reply_text(f"Interval: {lo}-{hi} minutes.")
        return
    if pending == "blocker":
        await blocker_input(update, context)
        return
    await chat_handler(update, context)


async def chat_handler(update, context):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user or not msg.text or msg.text.startswith("/"):
        return
    state = st.get_state(user.id)
    history = state.setdefault("history", [])
    history.append({"role": "user", "content": msg.text.strip()})
    state["history"] = history[-20:]
    await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
    reply = await venice.chat(state["history"], HARLEY_VOICE, temperature=0.9)
    if not reply:
        reply = "Lost my train of thought. Say that again?"
    state["history"] = (state["history"] + [{"role": "assistant", "content": reply}])[-20:]
    await msg.reply_text(reply[:4000])


async def start_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    if not state.get("gender"):
        await update.effective_message.reply_text("First - gender:", reply_markup=gender_keyboard())
        return
    await update.effective_message.reply_text(
        f"Harley here.\n\n{state.get('location') or 'No location set'} | "
        f"risk {state.get('risk_level') or 5} | {state.get('points') or 0} pts",
        reply_markup=main_menu())


async def help_cmd(update, context):
    await update.effective_message.reply_text(
        "/task a challenge\n/location venue and rooms\n/outfit what you're wearing\n"
        "/avatar a photo\n/reward a reward render\n/autopics scheduled photos\n"
        "/variant her anatomy\n/risk /kinks /schedule /interval\n"
        "/settings /rewards /status\n/clear /reset",
        reply_markup=main_menu())


async def settings_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    await update.effective_message.reply_text(
        f"variant: {state.get('variant') or DEFAULT_VARIANT}\n"
        f"privacy: {state.get('privacy') or 'private'}\n"
        f"outfit: {state.get('outfit_detail') or state.get('current_outfit') or 'unset'}\n"
        f"task model: {state.get('task_model') or venice.IMAGE_MODEL}\n"
        f"reward model: {state.get('reward_model') or venice.IMAGE_MODEL_REWARD}\n"
        f"location: {state.get('location_notes') or 'unset'}\n"
        f"auto pics: {'on' if state.get('avatar_enabled') else 'off'}\n"
        f"points: {state.get('points') or 0} streak: {state.get('streak') or 0}",
        reply_markup=main_menu())


async def menu_callback(update, context):
    q = update.callback_query
    await q.answer()
    route = q.data.split("_", 1)[1]
    if route == "task":
        await issue_task(update, context)
    elif route == "outfit":
        await outfit_cmd(update, context)
    elif route == "location":
        await location_cmd(update, context)
    elif route == "avatar":
        await avatar_cmd(update, context)
    elif route == "variant":
        await variant_cmd(update, context)
    elif route == "risk":
        await risk_cmd(update, context)
    elif route == "kinks":
        await kinks_cmd(update, context)
    elif route == "schedule":
        await schedule_cmd(update, context)
    elif route == "interval":
        await interval_cmd(update, context)
    elif route == "settings":
        await settings_cmd(update, context)
    elif route == "autopics":
        await autopics_cmd(update, context)
    elif route == "rewards":
        await rewards_cmd(update, context)
    elif route == "status":
        await status_cmd(update, context)


async def variant_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    await update.effective_message.reply_text(
        f"Anatomy (currently {state.get('variant') or DEFAULT_VARIANT}):",
        reply_markup=variant_keyboard(state.get("variant")))


async def variant_callback(update, context):
    q = update.callback_query
    await q.answer()
    v = q.data.split("_", 1)[1]
    if v not in HARLEY_VARIANTS:
        await q.edit_message_text("Unknown.")
        return
    state = st.get_state(update.effective_user.id)
    state["variant"] = v
    await q.edit_message_text(f"Variant: {v}.", reply_markup=variant_keyboard(v))


async def privacy_cmd(update, context):
    await update.effective_message.reply_text(
        "Outfit level:", reply_markup=privacy_keyboard(st.get_state(update.effective_user.id).get("privacy")))


async def privacy_callback(update, context):
    q = update.callback_query
    await q.answer()
    st.get_state(update.effective_user.id)["privacy"] = q.data.split("_", 1)[1]
    await q.edit_message_text("Saved.")


async def setreward_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    await update.effective_message.reply_text(
        f"Reward model: {state.get('reward_model') or venice.IMAGE_MODEL_REWARD}",
        reply_markup=model_keyboard(state.get("reward_model") or venice.IMAGE_MODEL_REWARD, "setreward"))


async def setreward_callback(update, context):
    q = update.callback_query
    await q.answer()
    model = q.data.split("_", 1)[1]
    if model not in venice.MODEL_EXPLICIT:
        await q.edit_message_text(f"{model} can't do the nude reward.")
        return
    st.get_state(update.effective_user.id)["reward_model"] = model
    venice.get_spec(model, nude=True)
    await q.edit_message_text(f"Reward model: {model}.")


async def model_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    await update.effective_message.reply_text(
        "Task model:", reply_markup=model_keyboard(state.get("task_model") or venice.IMAGE_MODEL))


async def model_callback(update, context):
    q = update.callback_query
    await q.answer()
    st.get_state(update.effective_user.id)["task_model"] = q.data.split("_", 1)[1]
    await q.edit_message_text("Saved.")


async def payload_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    spec = venice.get_spec(state.get("task_model"))
    bits = subject_clause(state)
    v = variant_of(state)
    if v.get("clothed"):
        bits.append(v["clothed"])
    prompt, negative = prompts.build(bits, nude=False,
                                     outfit=random.choice(HARLEY_OUTFITS),
                                     pose=HARLEY_POSES[0],
                                     scene="framed from the thighs up",
                                     extra_negative=v.get("clothed_neg") or "")
    await update.effective_message.reply_text(
        f"model: {spec['model']}\nbox: {spec['width']}x{spec['height']}\nsteps: {spec['steps']}\n\n"
        f"positive:\n{prompt}\n\nnegative:\n{negative}"[:4000])


async def imgmodels_cmd(update, context):
    try:
        data = await venice.get("/models?type=image")
    except Exception as exc:
        await update.effective_message.reply_text(f"Failed: {exc}")
        return
    await update.effective_message.reply_text(
        ("\n".join(i.get("id", "?") for i in data.get("data", [])) or "empty")[:4000])


async def location_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    state["venue"] = None
    state["rooms"] = []
    state["features"] = {}
    state["probes"] = []
    state["pending"] = None
    await update.effective_message.reply_text("Where are we shooting?", reply_markup=venue_keyboard())


async def venue_callback(update, context):
    q = update.callback_query
    await q.answer()
    state = st.get_state(update.effective_user.id)
    if q.data == "venue_custom":
        state["pending"] = "custom_location"
        await q.edit_message_text("Name the place.")
        return
    v = q.data.split("_", 1)[1]
    state["venue"] = v
    state["rooms"] = []
    state["features"] = {}
    if venue.rooms_for_venue(v):
        await q.edit_message_text("Which rooms are in play?", reply_markup=room_keyboard(v, []))
    else:
        state["pending"] = "custom_rooms"
        await q.edit_message_text("List the rooms, comma separated.")


async def room_callback(update, context):
    q = update.callback_query
    await q.answer()
    state = st.get_state(update.effective_user.id)
    v = state.get("venue") or ""
    if q.data == "room_done":
        if not state.get("rooms"):
            await q.edit_message_text("Pick at least one room.")
            return
        await q.edit_message_text("Now the features.",
                                  reply_markup=present_feature_keyboard(v, state["rooms"], []))
        return
    if q.data == "room_custom":
        state["pending"] = "custom_rooms"
        await q.edit_message_text("Add a room name.")
        return
    rooms = venue.rooms_for_venue(v)
    i = int(q.data.split("_", 1)[1])
    if 0 <= i < len(rooms):
        _toggle(state.setdefault("rooms", []), rooms[i])
    await q.edit_message_reply_markup(reply_markup=room_keyboard(v, state["rooms"]))


async def present_feature_callback(update, context):
    q = update.callback_query
    await q.answer()
    state = st.get_state(update.effective_user.id)
    v = state.get("venue") or ""
    rooms = state.get("rooms") or []
    feats = state.setdefault("features", {})
    if q.data == "pf_done":
        await show_probes(q, update.effective_user.id, v, rooms,
                          [f for g in feats.values() for f in g])
        return
    if q.data == "pf_custom":
        state["pending"] = "custom_features"
        await q.edit_message_text("Describe the feature.")
        return
    _, ri, fi = q.data.split("_")
    ri, fi = int(ri), int(fi)
    if 0 <= ri < len(rooms):
        opts = venue.present_features(v, rooms[ri])
        if 0 <= fi < len(opts):
            _toggle(feats.setdefault(rooms[ri], []), opts[fi])
    await q.edit_message_reply_markup(reply_markup=present_feature_keyboard(
        v, rooms, [f for g in feats.values() for f in g]))


async def show_probes(q, user_id, v, rooms, chosen):
    state = st.get_state(user_id)
    baseline = venue.probe_options(v, rooms)
    known = [*rooms, *chosen, *baseline]
    await q.edit_message_text("Working out the details...")
    try:
        ai = await venice.generate_probes(v, rooms, known)
    except Exception:
        ai = []
    pool = (baseline + ai)[:venue.MAX_PROBES]
    state["probe_pool"] = pool
    state["probes"] = []
    await q.edit_message_text("Anything else worth pinning down?",
                              reply_markup=probe_keyboard(pool, []))


async def probe_callback(update, context):
    q = update.callback_query
    await q.answer()
    state = st.get_state(update.effective_user.id)
    pool = state.get("probe_pool") or []
    if q.data == "probe_done":
        await commit_location(q, update.effective_user.id)
        return
    if q.data == "probe_custom":
        state["pending"] = "custom_probe"
        await q.edit_message_text("Describe it.")
        return
    i = int(q.data.split("_", 1)[1])
    if 0 <= i < len(pool):
        _toggle(state.setdefault("probes", []), pool[i])
    await q.edit_message_reply_markup(reply_markup=probe_keyboard(pool, state["probes"]))


async def commit_location(q, user_id):
    state = st.get_state(user_id)
    v = state.get("venue") or ""
    rooms = state.get("rooms") or []
    feats = state.get("features") or {}
    probes = state.get("probes") or []
    lines = [f"Venue: {v}"]
    for room in rooms:
        picked = feats.get(room, [])
        lines.append(f"{room}: " + (", ".join(picked) if picked else "as is"))
    if probes:
        lines.append("Also: " + ", ".join(probes))
    notes = "\n".join(lines)
    state["location"] = v
    state["location_notes"] = notes
    state["pending"] = None
    await q.edit_message_text(f"Locked in.\n\n{notes}",
                              reply_markup=InlineKeyboardMarkup(
                                  [[InlineKeyboardButton("Clear", callback_data="loc_clear")]]))


async def clear_location(update, context):
    state = st.get_state(update.effective_user.id)
    for k in ("location", "location_notes", "rooms", "probes", "venue", "features"):
        state[k] = None if k in ("location", "location_notes", "venue") else []
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Cleared.")
    else:
        await update.effective_message.reply_text("Cleared.")


async def gender_cmd(update, context):
    await update.effective_message.reply_text("Gender:", reply_markup=gender_keyboard())


async def gender_callback(update, context):
    q = update.callback_query
    await q.answer()
    st.get_state(update.effective_user.id)["gender"] = q.data.split("_", 1)[1]
    await q.edit_message_text("Saved.", reply_markup=main_menu())


async def outfit_cmd(update, context):
    await update.effective_message.reply_text("What are you wearing?", reply_markup=outfit_keyboard())


async def outfit_callback(update, context):
    q = update.callback_query
    await q.answer()
    key = q.data.split("_", 1)[1]
    state = st.get_state(update.effective_user.id)
    if key == "custom":
        state["pending"] = "custom_outfit"
        await q.edit_message_text("Describe it.")
        return
    opt = tk.OUTFIT_OPTIONS[key]
    state["current_outfit"] = opt["name"]
    state["outfit_items"] = opt.get(f"{state.get('gender') or 'female'}_items", opt["female_items"])
    state["pending"] = "outfit_detail"
    await q.edit_message_text(f"{opt['name']}, roughly. What are you actually in? Type it or /skip.")


async def risk_cmd(update, context):
    await update.effective_message.reply_text("Risk:", reply_markup=risk_keyboard())


async def risk_callback(update, context):
    q = update.callback_query
    await q.answer()
    r = int(q.data.split("_", 1)[1])
    st.get_state(update.effective_user.id)["risk_level"] = r
    await q.edit_message_text(f"Risk: {tk.RISK_LEVELS[r]['name']}")


async def kinks_cmd(update, context):
    await update.effective_message.reply_text(
        "Kinks\n\nX no | O okay | Y desired\n\nTap to change:",
        reply_markup=kinks_keyboard(st.get_state(update.effective_user.id)))


async def kink_menu_callback(update, context):
    q = update.callback_query
    await q.answer()
    key = q.data.split("_", 1)[1]
    state = st.get_state(update.effective_user.id)
    cur = state.get(key) or "okay"
    rows = [[InlineKeyboardButton(("Y " if cur == lv else "") + f"{tk.KINK_LEVELS[lv]['emoji']} {tk.KINK_LEVELS[lv]['name']}",
                                  callback_data=f"kinkset_{key}_{lv}")] for lv in ("no", "okay", "yes")]
    rows.append([InlineKeyboardButton("Back", callback_data="kinks_back")])
    await q.edit_message_text(f"{tk.KINK_CATEGORIES[key][0]}\n\n{tk.KINK_CATEGORIES[key][1]}\n\nCurrent: {cur}",
                              reply_markup=InlineKeyboardMarkup(rows))


async def kink_set_callback(update, context):
    q = update.callback_query
    await q.answer()
    key, lv = q.data.replace("kinkset_", "").rsplit("_", 1)
    state = st.get_state(update.effective_user.id)
    state[key] = lv
    await q.edit_message_text(f"{tk.KINK_CATEGORIES[key][0]} = {lv}", reply_markup=kinks_keyboard(state))


async def kinks_back_callback(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Kinks:", reply_markup=kinks_keyboard(st.get_state(update.effective_user.id)))


async def kinks_done_callback(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Saved.", reply_markup=main_menu())


async def skip_cmd(update, context):
    st.get_state(update.effective_user.id)["pending"] = None
    await update.effective_message.reply_text("Fine. /task.")


async def interval_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    state["pending"] = "interval"
    await update.effective_message.reply_text(
        f"Current: {state.get('min_interval')}-{state.get('max_interval')}. Send MIN MAX.")


async def schedule_cmd(update, context):
    await update.effective_message.reply_text("Auto tasks:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Enable", callback_data="sched_on")],
        [InlineKeyboardButton("Disable", callback_data="sched_off")]]))


async def schedule_callback(update, context):
    q = update.callback_query
    await q.answer()
    state = st.get_state(update.effective_user.id)
    if q.data == "sched_on":
        state["scheduling_enabled"] = True
        state["next_task_time"] = datetime.now(timezone.utc) + timedelta(
            minutes=random.randint(state.get("min_interval") or 30, state.get("max_interval") or 180))
        await q.edit_message_text("On.")
    else:
        state["scheduling_enabled"] = False
        state["next_task_time"] = None
        await q.edit_message_text("Off.")


async def issue_task(update, context, label="TASK"):
    user = update.effective_user
    state = st.get_state(user.id)
    session = st.get_session()
    try:
        if not state.get("gender"):
            await gender_cmd(update, context)
            return
        if not state.get("current_outfit"):
            await outfit_cmd(update, context)
            return
        if not state.get("location"):
            await location_cmd(update, context)
            return
        if session.query(st.Task).filter_by(user_id=user.id, status="pending").first():
            await update.effective_message.reply_text("You already have one pending.")
            return
        await update.effective_message.reply_text("Writing...")
        if random.random() < 0.3:
            try:
                png = await generate_render(state)
                await storage.deliver(context.bot, user.id, user.id, png,
                                      caption="This is what's waiting for you.", prefix="avatar")
            except Exception:
                logger.warning("pre-task avatar failed")
        text, risk = await tk.generate_task_text(state, session, voice=HARLEY_VOICE)
        now = datetime.now(timezone.utc)
        task = st.Task(user_id=user.id, task_text=text, risk_level=risk, created_at=now,
                       expires_at=now + timedelta(minutes=30))
        session.add(task)
        session.commit()
        context.job_queue.run_once(expire_task, when=timedelta(minutes=30),
                                   data={"user_id": user.id, "task_id": task.id})
        await update.effective_message.reply_text(
            f"{label}\n\n{text}\n\n30 minutes.", reply_markup=task_keyboard(task.id, get_title()))
    finally:
        session.close()


async def task_cmd(update, context):
    context.user_data.pop("blocked_task_id", None)
    await issue_task(update, context)


async def status_cmd(update, context):
    session = st.get_session()
    try:
        active = session.query(st.Task).filter_by(user_id=update.effective_user.id, status="pending").first()
        if not active:
            await update.effective_message.reply_text("Nothing pending. /task")
            return
        left = (active.expires_at - datetime.now(timezone.utc)).total_seconds() / 60
        await update.effective_message.reply_text(
            f"{max(0, left):.0f} min left\n\n{active.task_text[:200]}",
            reply_markup=task_keyboard(active.id, get_title()))
    finally:
        session.close()


async def rewards_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    need = tk.reward_threshold(state.get("streak") or 0)
    await update.effective_message.reply_text(
        f"Every {need} completions earns a reward.\nStreak {state.get('streak') or 0} | "
        f"progress {state.get('challenges_since_reward') or 0}/{need}\n\n/reward to trigger one now.")


async def complete_callback(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Send the photo.")


async def blocked_callback(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["blocked_task_id"] = int(q.data.split("_", 1)[1])
    st.get_state(update.effective_user.id)["pending"] = "blocker"
    await q.edit_message_text("What's in the way?")


async def blocker_input(update, context):
    user = update.effective_user
    state = st.get_state(user.id)
    task_id = context.user_data.get("blocked_task_id")
    session = st.get_session()
    try:
        task = session.query(st.Task).filter_by(id=task_id, user_id=user.id, status="pending").first()
        if not task:
            state["pending"] = None
            await update.effective_message.reply_text("Gone. /task")
            return
        if task.blocked_count >= 3:
            await update.effective_message.reply_text("Three reworks. Use Mercy.")
            return
        state["pending"] = None
        text = update.effective_message.text.strip()[:200]
        loc = state.get("location") or "Unknown"
        norm = text.lower().strip().rstrip(".")
        existing = None
        for c in session.query(st.LocationConstraint).filter_by(user_id=user.id, location=loc).all():
            if c.constraint.lower().strip().rstrip(".") == norm:
                existing = c
                break
        now = datetime.now(timezone.utc)
        if existing:
            existing.times_hit += 1
            existing.expires_at = now + timedelta(days=30 if existing.times_hit >= 2 else 3)
        else:
            session.add(st.LocationConstraint(user_id=user.id, location=loc, constraint=text,
                                              expires_at=now + timedelta(hours=3)))
        session.commit()
        await update.effective_message.reply_text("Fine. Again.")
        new, risk = await tk.generate_task_text(state, session, voice=HARLEY_VOICE)
        task.task_text, task.risk_level = new, risk
        task.verification_attempts, task.followup_target = 0, None
        task.expires_at = now + timedelta(minutes=30)
        task.blocked_count += 1
        session.commit()
        await update.effective_message.reply_text(
            f"REWORKED\n\n{new}\n\n30 minutes.", reply_markup=task_keyboard(task.id, get_title()))
    finally:
        session.close()


async def mercy_callback(update, context):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    state = st.get_state(user.id)
    session = st.get_session()
    try:
        task = session.query(st.Task).filter_by(id=int(q.data.split("_", 1)[1]),
                                                user_id=user.id, status="pending").first()
        if not task:
            await q.edit_message_text("Nothing pending.")
            return
        await q.edit_message_text("Fine. Something else...")
        new, risk = await tk.generate_task_text(state, session, voice=HARLEY_VOICE)
        now = datetime.now(timezone.utc)
        task.task_text, task.risk_level = new, risk
        task.verification_attempts, task.followup_target = 0, None
        task.expires_at = now + timedelta(minutes=30)
        session.commit()
        await context.bot.send_message(chat_id=user.id, text=f"NEW\n\n{new}\n\n30 minutes.",
                                       reply_markup=task_keyboard(task.id, get_title()))
    finally:
        session.close()


async def giveup_callback(update, context):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    state = st.get_state(user.id)
    session = st.get_session()
    try:
        task = session.query(st.Task).filter_by(id=int(q.data.split("_", 1)[1]), user_id=user.id).first()
        if task and task.status == "pending":
            task.status = "failed"
            state["points"] = max(0, (state.get("points") or 0) - 5)
            state["streak"] = 0
            session.commit()
            await q.edit_message_text("Abandoned. -5.")
    finally:
        session.close()


async def retry_callback(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Send the retry.")


async def handle_photo(update, context):
    user = update.effective_user
    state = st.get_state(user.id)
    session = st.get_session()
    try:
        task = session.query(st.Task).filter_by(user_id=user.id, status="pending").first()
        photo = update.message.photo[-1]
        await update.message.reply_text("Checking...")
        data = bytes(await (await context.bot.get_file(photo.file_id)).download_as_bytearray())
        if len(data) < 1000:
            await update.message.reply_text("Too small.")
            return
        kind = "verification" if task else "user_upload"
        up = await storage.upload(data, user.id, image_type=kind)
        session.add(st.UserImage(user_id=user.id, telegram_file_id=photo.file_id, image_type=kind,
                                 cloudinary_url=up["url"] if up else None,
                                 cloudinary_public_id=up["public_id"] if up else None))
        session.commit()
        if not task:
            await update.message.reply_text("Saved.")
            return
        now = datetime.now(timezone.utc)
        if now > task.expires_at:
            task.status = "expired"
            session.commit()
            await update.message.reply_text("Expired. /task")
            return
        verdict = await verify.verify_photo(data, task.task_text, task.followup_target)
        if verdict is None:
            await update.message.reply_text("Verifier down - resend, no attempt used.")
            return
        verified, missing, reason = verdict
        task.verification_attempts += 1
        task.sharpness = int(await asyncio.to_thread(render.sharpness_score, data))
        if up:
            task.cloudinary_url, task.cloudinary_public_id = up["url"], up["public_id"]

        if verified == "yes":
            task.status, task.completed_at, task.photo_url = "completed", now, photo.file_id
            task.followup_target = None
            context.user_data.pop("blocked_task_id", None)
            state["points"] = (state.get("points") or 0) + 10 * task.risk_level
            state["streak"] = (state.get("streak") or 0) + 1
            state["completed_tasks"] = (state.get("completed_tasks") or 0) + 1
            state["challenges_since_reward"] = (state.get("challenges_since_reward") or 0) + 1
            session.commit()
            gain = 10 * task.risk_level
            need = tk.reward_threshold(state["streak"])
            if state["challenges_since_reward"] >= need:
                state["challenges_since_reward"] = 0
                await update.message.reply_text(f"+{gain}. Streak {state['streak']}.")
                await ask_reward(update, context, user.id)
            else:
                await update.message.reply_text(f"+{gain}. Streak {state['streak']}.")
            return

        if task.verification_attempts >= 3:
            task.status = "failed"
            state["consecutive_failures"] = (state.get("consecutive_failures") or 0) + 1
            session.commit()
            await update.message.reply_text(f"Failed. {reason}")
            return
        task.followup_target = missing
        session.commit()
        left = 3 - task.verification_attempts
        body = (f"Can't see {missing}. Show me {missing}." if missing else f"No. {reason} Again.")
        await update.message.reply_text(
            f"{body}\n\n{left} {'try' if left == 1 else 'tries'} left.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Try Again", callback_data=f"retry_{task.id}"),
                InlineKeyboardButton("Give Up", callback_data=f"giveup_{task.id}")]]))
    except Exception:
        logger.exception("photo failed")
        await update.message.reply_text("Error with that photo.")
    finally:
        session.close()


async def expire_task(context):
    user_id, task_id = context.job.data["user_id"], context.job.data["task_id"]
    session = st.get_session()
    try:
        task = session.query(st.Task).filter_by(id=task_id).first()
        if not task or task.status != "pending":
            return
        task.status = "expired"
        state = st.get_state(user_id)
        state["points"] = max(0, (state.get("points") or 0) - 10)
        state["streak"] = 0
        state["consecutive_failures"] = (state.get("consecutive_failures") or 0) + 1
        session.commit()
        await context.bot.send_message(chat_id=user_id, text="Time's up. -10.")
    except Exception:
        logger.exception("expire failed")
    finally:
        session.close()


async def avatar_cmd(update, context):
    state = st.get_state(update.effective_user.id)
    await update.effective_message.reply_text("One sec...")
    try:
        png = await generate_render(state, scene_extra="three quarter shot")
    except Exception as exc:
        await update.effective_message.reply_text(f"Failed: {exc}")
        return
    await storage.deliver(context.bot, update.effective_chat.id, update.effective_user.id, png,
                          caption="", prefix="avatar")


async def debugavatar_cmd(update, context):
    user = update.effective_user
    await update.effective_message.reply_text("Rendering...")
    try:
        png = await generate_render(st.get_state(user.id))
    except Exception as exc:
        await update.effective_message.reply_text(f"Failed: {exc}")
        return
    await render.debug_send(context.bot, png, label="avatar", chat_id=user.id, upscale_pass=True)


async def tryn_cmd(update, context):
    user = update.effective_user
    await update.effective_message.reply_text("Batch...")
    try:
        png = await generate_render(st.get_state(user.id))
    except Exception as exc:
        await update.effective_message.reply_text(f"Failed: {exc}")
        return
    await render.debug_send(context.bot, png, label="tryn", chat_id=user.id, upscale_pass=True)


async def testreward_cmd(update, context):
    user = update.effective_user
    await update.effective_message.reply_text("Reward batch...")
    try:
        png = await generate_render(st.get_state(user.id), nude=True)
    except Exception as exc:
        await update.effective_message.reply_text(f"Failed: {exc}")
        return
    gate = await verify.nude_check(png)
    await render.debug_send(context.bot, png, label="testreward", chat_id=user.id,
                            upscale_pass=True, gate=gate)


async def reset_cmd(update, context):
    uid = update.effective_user.id
    state = st.get_state(uid)
    state["variant"] = DEFAULT_VARIANT
    state["privacy"] = "private"
    state["location"] = None
    state["location_notes"] = None
    state["rooms"] = []
    state["probes"] = []
    st.drop_state(uid)
    await update.effective_message.reply_text("Defaults restored.")


async def scheduled_check(context):
    session = st.get_session()
    try:
        now = datetime.now(timezone.utc)
        session.query(st.LocationConstraint).filter(st.LocationConstraint.expires_at < now).delete()
        session.commit()
        due = session.query(st.UserState).filter(st.UserState.scheduling_enabled == True,
                                                 st.UserState.next_task_time <= now).all()
        for row in due:
            if session.query(st.Task).filter_by(user_id=row.user_id, status="pending").first():
                continue
            state = st.get_state(row.user_id)
            if not state.get("gender") or not state.get("location"):
                state["next_task_time"] = now + timedelta(minutes=15)
                continue
            text, risk = await tk.generate_task_text(state, session, voice=HARLEY_VOICE)
            task = st.Task(user_id=row.user_id, task_text=text, risk_level=risk, created_at=now,
                           expires_at=now + timedelta(minutes=30))
            session.add(task)
            state["next_task_time"] = now + timedelta(
                minutes=random.randint(state.get("min_interval") or 30, state.get("max_interval") or 180))
            session.commit()
            context.job_queue.run_once(expire_task, when=timedelta(minutes=30),
                                       data={"user_id": row.user_id, "task_id": task.id})
            await context.bot.send_message(chat_id=row.user_id,
                                           text=f"AUTO\n\n{text}\n\n30 minutes.",
                                           reply_markup=task_keyboard(task.id, get_title()))
            await asyncio.sleep(1)
        for row in session.query(st.UserState).filter(st.UserState.avatar_enabled == True).all():
            last = row.last_avatar_sent_at
            if last and last + timedelta(minutes=row.avatar_interval_minutes or 60) > now:
                continue
            try:
                state = st.get_state(row.user_id)
                png = await generate_render(state)
                await storage.deliver(context.bot, row.user_id, row.user_id, png,
                                      caption=random.choice(["Thinking of you.", "Well?", "Look at me."]),
                                      prefix="avatar")
                state["last_avatar_sent_at"] = now
            except Exception:
                logger.exception("periodic avatar failed")
    except Exception:
        logger.exception("scheduled check failed")
    finally:
        session.close()


async def error_handler(update, context):
    logger.exception("handler error", exc_info=context.error)


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN empty")
    if not VENICE_API_KEY:
        raise SystemExit("VENICE_API_KEY empty")
    st.ensure_schema()
    venice.load_specs()
    storage.configure()

    app = Application.builder().token(TOKEN).read_timeout(180).write_timeout(180).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("menu", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("variant", variant_cmd))
    app.add_handler(CommandHandler("privacy", privacy_cmd))
    app.add_handler(CommandHandler("setreward", setreward_cmd))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("payload", payload_cmd))
    app.add_handler(CommandHandler("imgmodels", imgmodels_cmd))
    app.add_handler(CommandHandler("location", location_cmd))
    app.add_handler(CommandHandler("clear", clear_location))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("avatar", avatar_cmd))
    app.add_handler(CommandHandler("reward", reward_cmd))
    app.add_handler(CommandHandler("autopics", autopics_cmd))
    app.add_handler(CommandHandler("debugavatar", debugavatar_cmd))
    app.add_handler(CommandHandler("tryn", tryn_cmd))
    app.add_handler(CommandHandler("testreward", testreward_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("gender", gender_cmd))
    app.add_handler(CommandHandler("outfit", outfit_cmd))
    app.add_handler(CommandHandler("risk", risk_cmd))
    app.add_handler(CommandHandler("kinks", kinks_cmd))
    app.add_handler(CommandHandler("task", task_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("rewards", rewards_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("interval", interval_cmd))

    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(wish_skip_callback, pattern="^wish_skip$"))
    app.add_handler(CallbackQueryHandler(autopics_callback, pattern="^autopics_toggle$"))
    app.add_handler(CallbackQueryHandler(venue_callback, pattern="^venue_"))
    app.add_handler(CallbackQueryHandler(room_callback, pattern="^room_"))
    app.add_handler(CallbackQueryHandler(present_feature_callback, pattern="^pf_"))
    app.add_handler(CallbackQueryHandler(probe_callback, pattern="^probe_"))
    app.add_handler(CallbackQueryHandler(clear_location, pattern="^loc_clear$"))
    app.add_handler(CallbackQueryHandler(variant_callback, pattern="^variant_"))
    app.add_handler(CallbackQueryHandler(privacy_callback, pattern="^privacy_"))
    app.add_handler(CallbackQueryHandler(setreward_callback, pattern="^setreward_"))
    app.add_handler(CallbackQueryHandler(model_callback, pattern="^model_"))
    app.add_handler(CallbackQueryHandler(gender_callback, pattern="^gender_"))
    app.add_handler(CallbackQueryHandler(outfit_callback, pattern="^outfit_"))
    app.add_handler(CallbackQueryHandler(risk_callback, pattern="^risk_"))
    app.add_handler(CallbackQueryHandler(kink_menu_callback, pattern="^kinkmenu_"))
    app.add_handler(CallbackQueryHandler(kink_set_callback, pattern="^kinkset_"))
    app.add_handler(CallbackQueryHandler(kinks_back_callback, pattern="^kinks_back$"))
    app.add_handler(CallbackQueryHandler(kinks_done_callback, pattern="^kinks_done$"))
    app.add_handler(CallbackQueryHandler(complete_callback, pattern="^complete_"))
    app.add_handler(CallbackQueryHandler(blocked_callback, pattern="^blocked_"))
    app.add_handler(CallbackQueryHandler(giveup_callback, pattern="^giveup_"))
    app.add_handler(CallbackQueryHandler(retry_callback, pattern="^retry_"))
    app.add_handler(CallbackQueryHandler(mercy_callback, pattern="^mercy_"))
    app.add_handler(CallbackQueryHandler(schedule_callback, pattern="^sched_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_input))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)

    app.job_queue.run_repeating(scheduled_check, interval=60, first=10,
                                job_kwargs={"misfire_grace_time": 180, "coalesce": True, "max_instances": 1})
    logger.info("harley up")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()