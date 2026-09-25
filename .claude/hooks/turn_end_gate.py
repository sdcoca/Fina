#!/usr/bin/env python3
"""Stop hook: enforces CLAUDE.md rules 2 and 21 mechanically at the end of every turn.

Rule 2  -- a turn-ending message that asks the user anything must carry an explicit
           recommendation ("Recomendación: ..." / "(Recomendada)").
Rule 4  -- lists are numbered; no unnumbered bullets in what the user reads.
Rule 21 -- the user must get a PushNotification when a turn ends waiting on them, or when a
           job finishes after they may have walked away (>= AWAY_SECONDS since their last
           message, with no background task of this turn still running).

Blocks the stop (decision=block) with the reason when a rule is broken; never traps the
session: at most MAX_BLOCKS_PER_TURN blocks per turn, and any unreadable input allows.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AWAY_SECONDS = 90
MAX_BLOCKS_PER_TURN = 2
PUSH_TOOL = "PushNotification"

_TOOL_USE_ID = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")
_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"https?://\S+")
_BULLET = re.compile(r"^[ \t]*[-*\u2022][ \t]+\S", re.MULTILINE)
# A marked pick, not just the word: "seguimos con tus recomendaciones" must not pass.
_RECOMMENDATION = re.compile(
    r"\(recomendad[ao]\)|recomendacion\s*:|mi recomendacion|recomiendo"
)

Entry = dict[str, Any]


def _content(entry: Entry) -> Any:
    return (entry.get("message") or {}).get("content")


def _blocks(entry: Entry) -> list[dict[str, Any]]:
    content = _content(entry)
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _is_tool_result(entry: Entry) -> bool:
    return any(b.get("type") == "tool_result" for b in _blocks(entry))


def _text(entry: Entry) -> str:
    content = _content(entry)
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in _blocks(entry) if b.get("type") == "text")


def _main_loop(entries: list[Entry], kind: str) -> list[tuple[int, Entry]]:
    return [
        (i, e)
        for i, e in enumerate(entries)
        if e.get("type") == kind and not e.get("isSidechain")
    ]


def turn_boundary(entries: list[Entry]) -> int:
    """Index of the message that opened this turn: the newest non-meta, non-tool-result
    user entry (a human message or a background-task notification); -1 if none."""
    for i, e in reversed(_main_loop(entries, "user")):
        if not e.get("isMeta") and not _is_tool_result(e):
            return i
    return -1


def last_human_index(entries: list[Entry]) -> int:
    for i, e in reversed(_main_loop(entries, "user")):
        if e.get("isMeta") or _is_tool_result(e):
            continue
        if (e.get("origin") or {}).get("kind", "human") == "human":
            return i
    return -1


def final_text(entries: list[Entry], boundary: int) -> str:
    """Text the user actually reads at the end of the turn: assistant text written after the
    turn's last tool result. A PushNotification's own result is not a boundary -- otherwise
    notifying would hide the very question the notification is about."""
    push_ids = {b.get("id") for b in tool_uses(entries, boundary) if b.get("name") == PUSH_TOOL}
    start = boundary
    for i, e in _main_loop(entries, "user"):
        results = [b for b in _blocks(e) if b.get("type") == "tool_result"]
        if i > boundary and results and not all(b.get("tool_use_id") in push_ids for b in results):
            start = i
    return "\n".join(_text(e) for i, e in _main_loop(entries, "assistant") if i > start).strip()


def tool_uses(entries: list[Entry], after: int) -> list[dict[str, Any]]:
    return [
        b
        for i, e in _main_loop(entries, "assistant")
        if i > after
        for b in _blocks(e)
        if b.get("type") == "tool_use"
    ]


def pending_background_tasks(entries: list[Entry], since: int) -> set[str]:
    """tool_use ids started in the background since `since` whose completion notification
    has not arrived yet."""
    started: set[str] = set()
    for block in tool_uses(entries, since):
        tool_input = block.get("input") or {}
        flag = tool_input.get("run_in_background")
        # Agent runs in the background unless told otherwise; Bash only when asked to.
        backgrounded = flag is True or (block.get("name") == "Agent" and flag is not False)
        if backgrounded and isinstance(block.get("id"), str):
            started.add(block["id"])
    for i, e in _main_loop(entries, "user"):
        if i <= since:
            continue
        for b in _blocks(e):
            if b.get("type") == "tool_result" and "moved to the background" in json.dumps(
                b.get("content")
            ):
                started.add(str(b.get("tool_use_id")))
    # A completion notice lands as a user message between turns, but as an `attachment` (and
    # a `queue-operation`) entry when the task finishes mid-turn -- scan every non-assistant
    # entry, never assistant ones (the agent's own tool inputs may quote the tag).
    finished = {
        tool_id
        for i, e in enumerate(entries)
        if i > since and e.get("type") != "assistant"
        for tool_id in _TOOL_USE_ID.findall(json.dumps(e, ensure_ascii=False))
    }
    return started - finished


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def asks_user(text: str) -> bool:
    visible = _URL.sub("", _INLINE_CODE.sub("", _FENCED.sub("", text)))
    return "?" in visible


def has_unnumbered_bullets(text: str) -> bool:
    return _BULLET.search(_FENCED.sub("", text)) is not None


def has_recommendation(text: str) -> bool:
    return _RECOMMENDATION.search(_fold(text)) is not None


def _timestamp(entry: Entry) -> datetime | None:
    raw = entry.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def problems(entries: list[Entry], now: datetime) -> list[str]:
    boundary = turn_boundary(entries)
    if boundary < 0:
        return []
    text = final_text(entries, boundary)
    asking = asks_user(text)
    found: list[str] = []

    if asking and not has_recommendation(text):
        found.append(
            "CLAUDE.md regla 2: tu mensaje final hace una pregunta al usuario sin "
            "recomendación. Reescríbelo: opciones concretas (A, B...) con la recomendada "
            "marcada '(Recomendada)' y su razón, o 'Recomendación: ...' si es una pregunta "
            "factual/aclaratoria."
        )

    if has_unnumbered_bullets(text):
        found.append(
            "CLAUDE.md regla 4: tu mensaje final usa viñetas sin numerar ('-', '*', '•'). "
            "Todas las listas van numeradas (1., 2.; 1.1, 1.2 dentro de secciones)."
        )

    pushed = any(b.get("name") == PUSH_TOOL for b in tool_uses(entries, boundary))
    if not pushed:
        human = last_human_index(entries)
        human_at = _timestamp(entries[human]) if human >= 0 else None
        away = human_at is not None and (now - human_at).total_seconds() >= AWAY_SECONDS
        job_done = away and not pending_background_tasks(entries, max(human, 0))
        if asking or job_done:
            why = "terminas esperando una respuesta suya" if asking else (
                "ha terminado un trabajo y el usuario puede no estar mirando"
            )
            found.append(
                f"CLAUDE.md regla 21: {why}, pero no has enviado PushNotification en este "
                "turno. Envíala (una línea, <200 caracteres, con lo que tiene que hacer o "
                "decidir) antes de terminar."
            )
    return found


def _state_path() -> Path:
    # $HOME, never the repo: an untracked state file would trip the git-status stop hook.
    return Path(os.path.expanduser("~")) / ".fina-turn-gate" / "state.json"


def _bump(turn_key: str) -> int:
    path = _state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    count = state.get("count", 0) + 1 if state.get("turn") == turn_key else 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"turn": turn_key, "count": count}), encoding="utf-8")
    except OSError:
        pass
    return count


def read_transcript(path: str) -> list[Entry]:
    entries: list[Entry] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    entries.append(parsed)
    except OSError:
        return []
    return entries


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        entries = read_transcript(str(payload.get("transcript_path") or ""))
        found = problems(entries, datetime.now(UTC))
    except Exception:  # a broken gate must never trap the session
        return
    if not found:
        return
    boundary = entries[turn_boundary(entries)]
    turn_key = str(boundary.get("uuid") or boundary.get("timestamp"))
    if _bump(turn_key) > MAX_BLOCKS_PER_TURN:
        return
    json.dump({"decision": "block", "reason": " ".join(found)}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
