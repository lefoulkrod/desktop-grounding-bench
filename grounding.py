"""Validate normalized visual targeting responses before producing any input."""

import json
import math
import re


def targeting_prompt(target: str) -> str:
    """Use the single-target prompt selected by the repeated Linux desktop benchmark."""
    return target.strip() + "\n" + (
        "Point to the visual center of that control. Use native image coordinates normalized "
        'to 0–1000 for x and y. Return only {"x": ..., "y": ...}. '
        'Do not convert into pixels or do resolution arithmetic. If absent return {"found":false}.'
    )


def locate_target(answer: str, width: int, height: int) -> tuple[int, int] | None:
    content = answer.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.DOTALL)
    if fence:
        content = fence.group(1)
    try:
        target = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise ValueError('Vision model must return JSON coordinates or {"found":false}.') from exc
    if not isinstance(target, dict):
        raise ValueError("Vision model returned an invalid target.")
    if target == {"found": False}:
        return None
    if set(target) != {"x", "y"}:
        raise ValueError("Vision model must return only x and y coordinates.")
    for axis in ("x", "y"):
        value = target[axis]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1000
        ):
            raise ValueError("Vision coordinates must be finite numbers between 0 and 1000.")
    if width < 1 or height < 1:
        raise ValueError("Invalid screenshot dimensions.")
    return round(target["x"] * (width - 1) / 1000), round(target["y"] * (height - 1) / 1000)
