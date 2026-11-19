import re


def is_ascii_number(value: str) -> bool:
    return re.fullmatch(r"[0-9]+", value) is not None
