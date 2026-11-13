from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def rule(name):
    if name not in {"tutor", "knowledge", "classify", "vision", "questions", "grade", "hint", "auto-review"}:
        raise ValueError("unknown rule")
    return (Path(__file__).parent / "rules" / (name + ".md")).read_text()
