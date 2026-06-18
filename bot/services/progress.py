from __future__ import annotations

from bot.utils.time import format_duration


def format_progress_bar(
    elapsed_seconds: int,
    duration_seconds: int | None,
    *,
    width: int = 18,
) -> str:
    if duration_seconds is None or duration_seconds <= 0:
        return f"{format_duration(elapsed_seconds)} {'━' * width} Unknown"

    elapsed_seconds = max(0, min(elapsed_seconds, duration_seconds))
    ratio = elapsed_seconds / duration_seconds
    marker_index = min(width - 1, max(0, int(ratio * width)))
    bar = "━" * marker_index + "●" + "─" * (width - marker_index - 1)
    return f"{format_duration(elapsed_seconds)} {bar} {format_duration(duration_seconds)}"
