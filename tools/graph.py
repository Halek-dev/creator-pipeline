"""Shared helpers for building ComfyUI API-format graphs (used by every workflow generator)."""


def by_title(wf):
    return {n["_meta"]["title"]: nid for nid, n in wf.items()}


def add(wf, class_type, title, inputs):
    """Append a node with a unique title; ids are max(existing) + 1 so they never collide after insertions."""
    if title in by_title(wf):
        raise ValueError(f"duplicate node title {title!r}")
    nid = str(max((int(k) for k in wf), default=0) + 1)
    wf[nid] = {"class_type": class_type, "_meta": {"title": title}, "inputs": inputs}
    return nid
