"""Checks the app's translations against the English texts.

English lives in desktop/FpvDvrCleaner.Desktop/UiText.cs (the user interface) and in the
engine's own messages (src/flugbild/*.py). Every language file in Locales/ must translate
all of them, with the same {0} placeholders, and nothing that no longer exists.

    python tools/check_translations.py            check every language, exit 1 on problems
    python tools/check_translations.py --english  print the English catalogue as JSON
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "FpvDvrCleaner.Desktop"
LOCALES = DESKTOP / "Locales"
LANGUAGES = ["de", "fr", "es", "it", "pt-BR", "nl", "pl", "cs", "ru", "uk", "tr", "ja", "zh-Hans", "ko"]

_PROPERTY = re.compile(r'public static string (\w+) \{ get; private set; \} = "((?:[^"\\]|\\.)*)";')
_PLACEHOLDER = re.compile(r"\{(\d+)(?::[^}]*)?\}")
# raise ValueError("..."), raise ValueError(f"..."), and result messages such as Laufergebnis(..., False, "...").
_RAISED = re.compile(r'raise \w+\((f?)"((?:[^"\\]|\\.)*)"\)')
_RESULT = re.compile(r'(?:ergebnis\([^()]*False,\s*|\bor\s+)(f?)"((?:[^"\\]|\\.)*)"')


def _unescape(text: str) -> str:
    return re.sub(r"\\u([0-9A-Fa-f]{4})|\\(.)", lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(2), text)


def interface_texts() -> dict[str, str]:
    """The English user interface texts by name, as UiText.cs defines them."""
    source = (DESKTOP / "UiText.cs").read_text(encoding="utf-8-sig")
    return {name: _unescape(text) for name, text in _PROPERTY.findall(source)}


def engine_messages() -> list[str]:
    """The engine's English messages; f-string parts become {0}, {1}, ..."""
    found: list[str] = []
    for path in sorted((ROOT / "src" / "flugbild").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for pattern in (_RAISED, _RESULT):
            for is_format, text in pattern.findall(source):
                text = _unescape(text)
                if is_format:
                    counter = iter(range(10))
                    text = re.sub(r"\{[^{}]+\}", lambda _: "{%d}" % next(counter), text)
                if text not in found and re.search(r"[A-Za-z]{3}", text):
                    found.append(text)
    return found


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def check(language: str, english: dict[str, str], engine: list[str]) -> list[str]:
    problems: list[str] = []
    path = LOCALES / f"{language}.json"
    if not path.exists():
        return [f"{language}: {path.name} is missing"]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        return [f"{language}: not valid JSON: {error}"]
    for section, source in (("ui", english), ("engine", {text: text for text in engine})):
        translated = data.get(section, {})
        for key in source:
            if not isinstance(translated.get(key), str) or not translated[key].strip():
                problems.append(f"{language}: {section} '{key}' is not translated")
            elif placeholders(translated[key]) != placeholders(source[key]):
                problems.append(f"{language}: {section} '{key}' has placeholders {sorted(placeholders(translated[key]))},"
                                f" English has {sorted(placeholders(source[key]))}")
        for key in translated:
            if key not in source:
                problems.append(f"{language}: {section} '{key}' does not exist in English")
    return problems


def main() -> int:
    english, engine = interface_texts(), engine_messages()
    if "--english" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        json.dump({"ui": english, "engine": {text: text for text in engine}}, sys.stdout, ensure_ascii=False, indent=1)
        return 0
    problems = [problem for language in LANGUAGES for problem in check(language, english, engine)]
    for problem in problems[:200]:
        print(problem)
    print(f"{len(LANGUAGES)} languages, {len(english)} interface texts, {len(engine)} engine messages: "
          f"{'OK' if not problems else f'{len(problems)} problems'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
