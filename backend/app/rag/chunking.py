import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True, slots=True)
class TextChunk:
    heading: str
    text: str


def split_markdown(markdown: str, *, max_chars: int, overlap_chars: int) -> list[TextChunk]:
    sections: list[tuple[str, list[str]]] = []
    trail: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _HEADING.match(line)
        if match:
            level = len(match.group(1))
            trail = [*trail[: level - 1], match.group(2)]
            sections.append((" > ".join(trail), []))
            continue
        if not sections:
            sections.append(("", []))
        sections[-1][1].append(line)

    chunks: list[TextChunk] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if body:
            chunks += [TextChunk(heading, part) for part in split_text(body, max_chars, overlap_chars)]
    return chunks


def split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return [p for p in parts if p]
