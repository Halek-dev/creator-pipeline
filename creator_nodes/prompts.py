"""Prompt helpers for the Creator nodes.

No ComfyUI imports here, so this module can be unit-tested on its own.
"""
import json
import random
from math import prod
from pathlib import Path

LIBRARY_DIR = Path(__file__).parent / "prompt_library"


def join_parts(*parts: str) -> str:
    """Join non-empty prompt fragments with commas."""
    cleaned = [p.strip().strip(",").strip() for p in parts if p and p.strip()]
    return ", ".join(cleaned)


def build_character_prompt(trigger: str, shot: str, scene: str, style: str = "") -> str:
    """Put the LoRA trigger word first, then the shot, scene and style."""
    if not trigger.strip():
        raise ValueError("Trigger word is empty. Use the word your LoRA was trained with.")
    return join_parts(trigger, shot, scene, style)


def list_libraries() -> list[str]:
    return sorted(p.stem for p in LIBRARY_DIR.glob("*.json"))


def load_library(name: str) -> dict:
    path = LIBRARY_DIR / f"{name}.json"
    if not path.exists():
        raise ValueError(f"Prompt library '{name}' not found in {LIBRARY_DIR}")
    library = json.loads(path.read_text(encoding="utf-8"))
    slots = library.get("slots", {})
    for slot in _template_slots(library["template"]):
        if not slots.get(slot):
            raise ValueError(f"Library '{name}' template uses {{{slot}}} but has no entries for it")
    return library


def combination_count(library: dict) -> int:
    return prod(len(library["slots"][s]) for s in _template_slots(library["template"]))


def fill_template(library: dict, mode: str = "random", seed: int = 0) -> str:
    """Fill the library template.

    random:     pick each slot with a seeded RNG, so the same seed gives the same prompt.
    sequential: treat seed as an index and walk every combination in order, so a
                batch of N runs never repeats until all combinations are used.
    """
    slot_names = _template_slots(library["template"])
    slots = library["slots"]
    chosen = {}
    if mode == "sequential":
        index = seed % combination_count(library)
        for name in slot_names:
            options = slots[name]
            index, pick = divmod(index, len(options))
            chosen[name] = options[pick]
    elif mode == "random":
        rng = random.Random(seed)
        for name in slot_names:
            chosen[name] = rng.choice(slots[name])
    else:
        raise ValueError(f"Unknown mode '{mode}'. Use 'random' or 'sequential'.")
    return library["template"].format_map(chosen)


def _template_slots(template: str) -> list[str]:
    import string

    names = []
    for _, field, _, _ in string.Formatter().parse(template):
        if field and field not in names:
            names.append(field)
    return names
