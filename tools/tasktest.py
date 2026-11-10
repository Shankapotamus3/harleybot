"""Sample tasks across models and scenarios. Run with the bot's env loaded."""
import os, sys, asyncio, random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("IMAGE_MODEL", "chroma")

from core import state as st, tasks as tk, venue_ai  # noqa: E402

MODELS = ["venice-uncensored-role-play", "venice-uncensored", "qwen3-235b",
          "mistral-31-24b", "llama-3.3-70b", "hermes-3-llama-3.1-405b"]

VOICE = """You are the Domme. Composed, exacting, quietly amused. Short paragraphs.
State tasks as orders, present tense. Never break character. Never explain the format.
Telegram markdown only, no headers."""

SCENARIOS = [
    {"name": "hotel room, alone", "location": "Hotel", "privacy": "alone",
     "others": "alone", "outfit": "t-shirt and boxers", "hide": "the bathroom door"},
    {"name": "crowded bar, watched", "location": "Bar", "privacy": "watched",
     "others": "public", "outfit": "shirt, jeans and a chastity cage", "hide": "the far end of the bar"},
    {"name": "home, alone", "location": "Home", "privacy": "alone",
     "others": "alone", "outfit": "hoodie and sweatpants", "hide": "the hallway"},
]

KINKS = [k for k in tk.KINK_CATEGORIES]


def apply(state, sc):
    state["gender"] = "male"
    state["intensity"] = "standard"
    state["location"] = sc["location"]
    state["venue"] = sc["location"]
    state["venue_profile"] = None
    state["privacy"] = sc["privacy"]
    state["context_others"] = sc["others"]
    state["current_outfit"] = sc["outfit"]
    state["outfit_detail"] = sc["outfit"]
    state["outfit_items"] = [i.strip() for i in sc["outfit"].split(",")]
    state["hide_spot"] = sc["hide"]
    state["probes"] = []
    state["recent_settings"] = []
    state["recent_moves"] = []
    state["slot_skipped"] = []
    for k in KINKS:
        state[k] = "yes"


async def main():
    models = sys.argv[1:] or MODELS
    uid = 999_999_999
    state = st.get_state(uid)
    session = st.get_session()
    try:
        for sc in SCENARIOS:
            print(f"\n{'=' * 70}\n{sc['name'].upper()}\n{'=' * 70}")
            await venue_ai.profile(sc["location"])  # warm the profile once
            for model in models:
                apply(state, sc)
                state["venue_profile"] = await venue_ai.profile(sc["location"])
                random.seed(1234)
                try:
                    text, risk, _, exposure = await tk.generate_task_text(
                        state, session, voice=VOICE, model=model)
                except Exception as exc:
                    print(f"\n[{model}] FAILED {type(exc).__name__}: {exc}")
                    continue
                print(f"\n[{model}] risk {risk} exposure {exposure}\n{text}\n{'-' * 70}")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())