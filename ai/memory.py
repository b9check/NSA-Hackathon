"""Lessons store. Append-only JSONL of generalisable claims the LLM
extracted from past games (reflexion-style).

For Phase C we keep retrieval simple — recency-weighted, no embeddings.
Up to 100 lessons; oldest get archived when over cap.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# Repo-root-relative paths — match the AI_CONTROL.md spec.
ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "memory"
LESSONS_PATH = MEMORY_DIR / "lessons.jsonl"

# Hard cap on lessons; oldest beyond this are dropped.
MAX_LESSONS = 100


@dataclass
class Lesson:
    """One generalisable claim drawn from a past game."""
    id: str
    claim: str                          # 1-sentence assertion ("hold radar dark…")
    tags: Dict[str, Any] = field(default_factory=dict)
    source_game_id: str = ""
    source_turn: int = 0
    side: str = ""                      # "blue" / "red" / "" if symmetric
    outcome: str = ""                   # "win" / "loss" / "draw"
    created_ts: float = 0.0

    @classmethod
    def new(
        cls,
        claim: str,
        *,
        tags: Optional[Dict[str, Any]] = None,
        source_game_id: str = "",
        source_turn: int = 0,
        side: str = "",
        outcome: str = "",
    ) -> "Lesson":
        return cls(
            id="lsn_" + uuid.uuid4().hex[:6],
            claim=claim.strip(),
            tags=tags or {},
            source_game_id=source_game_id,
            source_turn=source_turn,
            side=side,
            outcome=outcome,
            created_ts=time.time(),
        )

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Lesson":
        return cls(
            id=d.get("id") or "lsn_" + uuid.uuid4().hex[:6],
            claim=str(d.get("claim", "")).strip(),
            tags=dict(d.get("tags", {})),
            source_game_id=str(d.get("source_game_id", "")),
            source_turn=int(d.get("source_turn", 0)),
            side=str(d.get("side", "")),
            outcome=str(d.get("outcome", "")),
            created_ts=float(d.get("created_ts", time.time())),
        )


def load_lessons() -> List[Lesson]:
    """Read all lessons from the jsonl file. Returns [] if file is missing."""
    if not LESSONS_PATH.exists():
        return []
    out: List[Lesson] = []
    for line in LESSONS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(Lesson.from_dict(d))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


def append_lessons(lessons: List[Lesson]) -> None:
    """Append lessons to the jsonl file. Creates the file + parent dir if
    missing. Enforces MAX_LESSONS by dropping oldest after append."""
    if not lessons:
        return
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    # Append efficiently; rewrite if we trim.
    with LESSONS_PATH.open("a", encoding="utf-8") as f:
        for ln in lessons:
            f.write(ln.to_jsonl() + "\n")
    # Cap enforcement.
    all_lessons = load_lessons()
    if len(all_lessons) > MAX_LESSONS:
        keep = sorted(all_lessons, key=lambda x: x.created_ts)[-MAX_LESSONS:]
        LESSONS_PATH.write_text(
            "\n".join(l.to_jsonl() for l in keep) + "\n", encoding="utf-8",
        )


def top_k(lessons: List[Lesson], k: int = 5) -> List[Lesson]:
    """Return the k most recent lessons. v1 retrieval = recency only —
    embeddings + tag-match scoring is a Phase C+ refinement."""
    if not lessons:
        return []
    return sorted(lessons, key=lambda x: -x.created_ts)[:k]


def render_for_prompt(lessons: List[Lesson]) -> str:
    """Render lessons for the LLM prompt. Compact, citation-style."""
    if not lessons:
        return "  (no lessons yet — first game)"
    out: List[str] = []
    for l in lessons:
        tag_bits: List[str] = []
        if l.side:
            tag_bits.append(l.side)
        if l.outcome:
            tag_bits.append(l.outcome)
        phase = l.tags.get("phase")
        if phase:
            tag_bits.append(str(phase))
        tags = " ".join(tag_bits) if tag_bits else "—"
        out.append(f"  [{l.id} | {tags}] {l.claim}")
    return "\n".join(out)
