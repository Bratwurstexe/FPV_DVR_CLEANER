from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class Arbeitsrezept:
    """User intent, independent from any FFmpeg command syntax."""

    signalart: str = "analog"
    farbberuhigung: int = 35
    blockpflege: int = 0
    kantendetail: int = 15
    vergroesserung_prozent: int = 100
    bewegungsspur: int = 0
    schnittbeginn: float = 0.0
    schnittende: float = 0.0
    untertiteltext: str = ""
    kompressionswert: int = 20
    dateiziel_mb: int = 0
    osd_entfernen: bool = False
    rauschminderung: int = 0

    def validate(self) -> None:
        if self.signalart not in {"analog", "digital", "neutral"}:
            raise ValueError("Unknown signal profile")
        for name in ("farbberuhigung", "blockpflege", "kantendetail", "bewegungsspur", "rauschminderung"):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if self.vergroesserung_prozent not in {100, 150, 200}:
            raise ValueError("Invalid target size")
        if self.schnittbeginn < 0 or self.schnittende < 0:
            raise ValueError("Cut times cannot be negative")
        if self.schnittende and self.schnittende <= self.schnittbeginn:
            raise ValueError("The out point must follow the in point")
        if not 14 <= self.kompressionswert <= 32:
            raise ValueError("Quality must be between 14 and 32")
        if self.dateiziel_mb < 0:
            raise ValueError("Size cap cannot be negative")
        if len(self.untertiteltext) > 300:
            raise ValueError("Caption is too long")
        if not isinstance(self.osd_entfernen, bool):
            raise ValueError("OSD removal must be on or off")

    def save(self, path: Path) -> None:
        self.validate()
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Arbeitsrezept:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("Incomplete or unknown recipe")
        recipe = cls(**data)
        recipe.validate()
        return recipe
