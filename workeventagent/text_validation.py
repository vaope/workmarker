from __future__ import annotations


def is_single_printable_line(value: str) -> bool:
    """Return whether value is non-empty text without any line separator/control."""

    return bool(value.strip()) and len(value.splitlines()) == 1 and value.isprintable()
