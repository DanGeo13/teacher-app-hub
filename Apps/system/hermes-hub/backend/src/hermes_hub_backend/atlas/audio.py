"""Merge chunked transcripts into one timeline.

Recorders split long voice notes into overlapping chunks, each with
chunk-relative timestamps. Merging applies each chunk's global offset, then
drops the duplicate copy of any segment that appears in two overlapping
chunks (same words, overlapping time), keeping the higher-confidence copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .text import normalise_sentence

OVERLAP_TOLERANCE_SECONDS = 1.5


@dataclass(slots=True)
class Segment:
    start: float
    end: float
    text: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "timestamp": format_timestamp(self.start),
            "text": self.text,
            "confidence": round(self.confidence, 3),
        }


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _validate_chunk(chunk: dict[str, Any]) -> None:
    offset = chunk.get("offsetSeconds")
    if not isinstance(offset, (int, float)) or offset < 0:
        raise ValueError("each chunk needs a non-negative offsetSeconds")
    for segment in chunk.get("segments", []):
        if segment["end"] < segment["start"] or segment["start"] < 0:
            raise ValueError("segment end must not precede start")


def _same_words(a: str, b: str) -> bool:
    na, nb = normalise_sentence(a), normalise_sentence(b)
    return bool(na) and (na == nb or na in nb or nb in na)


def _overlaps(a: Segment, b: Segment) -> bool:
    return a.start <= b.end + OVERLAP_TOLERANCE_SECONDS and b.start <= a.end + OVERLAP_TOLERANCE_SECONDS


def merge_chunks(chunks: Iterable[dict[str, Any]]) -> list[Segment]:
    segments: list[Segment] = []
    for chunk in chunks:
        _validate_chunk(chunk)
        offset = float(chunk["offsetSeconds"])
        for raw in chunk.get("segments", []):
            segments.append(
                Segment(
                    start=offset + float(raw["start"]),
                    end=offset + float(raw["end"]),
                    text=str(raw["text"]).strip(),
                    confidence=float(raw.get("confidence", 0.0)),
                )
            )
    segments.sort(key=lambda s: (s.start, s.end))
    kept: list[Segment] = []
    for segment in segments:
        duplicate = next(
            (k for k in kept if _overlaps(k, segment) and _same_words(k.text, segment.text)), None
        )
        if duplicate is None:
            kept.append(segment)
        elif segment.confidence > duplicate.confidence:
            kept[kept.index(duplicate)] = segment
    kept.sort(key=lambda s: (s.start, s.end))
    return kept
