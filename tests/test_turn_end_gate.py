"""Tests for .claude/hooks/turn_end_gate.py -- the Stop hook enforcing CLAUDE.md rules 2
(every question carries a recommendation) and 21 (push-notify when the user may be away or
is being waited on). Transcripts are synthetic but shaped exactly like the CLI's own JSONL
entries (checked against a real session transcript)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "turn_end_gate.py"
_spec = importlib.util.spec_from_file_location("turn_end_gate", _HOOK)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


def _ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def human(text: str, at: float = 0) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": f"h-{at}",
        "timestamp": _ts(at),
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": text},
    }


def task_notification(tool_use_id: str, at: float) -> dict[str, Any]:
    body = f"<task-notification>\n<tool-use-id>{tool_use_id}</tool-use-id>\n</task-notification>"
    return {
        "type": "user",
        "uuid": f"t-{at}",
        "timestamp": _ts(at),
        "origin": {"kind": "task-notification"},
        "message": {"role": "user", "content": body},
    }


def hook_feedback(text: str, at: float) -> dict[str, Any]:
    return {
        "type": "user",
        "isMeta": True,
        "timestamp": _ts(at),
        "message": {"role": "user", "content": f"Stop hook feedback:\n{text}"},
    }


def say(text: str, at: float = 1) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": _ts(at),
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def call(name: str, tool_id: str, at: float = 1, **tool_input: Any) -> dict[str, Any]:
    block = {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}
    return {"type": "assistant", "timestamp": _ts(at), "message": {"content": [block]}}


def result(tool_id: str, text: str = "ok", at: float = 2) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": text}
    return {"type": "user", "timestamp": _ts(at), "message": {"content": [block]}}


def problems_at(entries: list[dict[str, Any]], seconds: float) -> list[str]:
    return gate.problems(entries, T0 + timedelta(seconds=seconds))


# ---------------------------------------------------------------------------
# Rule 2: questions must carry a recommendation
# ---------------------------------------------------------------------------


def test_question_without_recommendation_is_blocked() -> None:
    """The exact failure from this project's own history: an open question, no pick."""
    entries = [
        human("hazlo"),
        call(gate.PUSH_TOOL, "p1"),
        result("p1"),
        say("¿Prefieres que Bizum caiga en la regla genérica o mantengo las 3 reglas?"),
    ]
    found = problems_at(entries, 5)
    assert len(found) == 1
    assert "regla 2:" in found[0]


@pytest.mark.parametrize(
    "text",
    [
        "A. Opción uno (Recomendada). B. Opción dos. ¿Cuál prefieres?",
        "Recomendación: esperar. ¿Te parece?",
        "Mi recomendación es la A. ¿Confirmas?",
        "Te recomiendo la opción 1. ¿Seguimos?",
        "RECOMENDACIÓN: A. ¿OK?",
    ],
)
def test_question_with_recommendation_passes_rule_2(text: str) -> None:
    entries = [human("hola"), call(gate.PUSH_TOOL, "p1"), result("p1"), say(text)]
    assert problems_at(entries, 5) == []


def test_the_word_alone_is_not_a_recommendation() -> None:
    """Real false negative found replaying this project's history: 'recomendaciones' used in
    another sense must not satisfy rule 2."""
    entries = [
        human("hola"),
        call(gate.PUSH_TOOL, "p1"),
        result("p1"),
        say("Seguimos con tus recomendaciones de 2.1. ¿Te suena algún cargo de 5€?"),
    ]
    found = problems_at(entries, 5)
    assert len(found) == 1
    assert "regla 2:" in found[0]


def test_pushing_does_not_hide_the_question_from_rule_2() -> None:
    """The push's own tool result must not become the 'final text' boundary."""
    entries = [
        human("hola"),
        say("¿A o B?"),
        call(gate.PUSH_TOOL, "p1"),
        result("p1", "Mobile push requested."),
        say("Aviso enviado."),
    ]
    found = problems_at(entries, 5)
    assert len(found) == 1
    assert "regla 2:" in found[0]


@pytest.mark.parametrize(
    "text",
    [
        "Hecho: commit `abc` publicado.",
        "Ver https://example.com/a?b=c para más detalle.",
        "El filtro usa `x ? y : z` internamente.",
        "Ejemplo:\n```\nif a?:\n```\nListo.",
    ],
)
def test_no_question_means_no_rule_2_check(text: str) -> None:
    entries = [human("hola"), say(text)]
    assert problems_at(entries, 5) == []


def test_only_text_after_the_last_tool_result_counts() -> None:
    """A question asked mid-turn and then answered by the agent's own work is not what the
    user reads at the end."""
    entries = [
        human("revisa"),
        say("¿Qué columnas tiene el fichero?"),
        call("Bash", "b1"),
        result("b1", "6 columnas"),
        say("Tiene 6 columnas."),
    ]
    assert problems_at(entries, 5) == []


# ---------------------------------------------------------------------------
# Rule 4: numbered lists only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bullet", ["- ", "* ", "• ", "  - "])
def test_unnumbered_bullets_are_blocked(bullet: str) -> None:
    """Real failure from this project's history: the 5 EUR explanation used '-' bullets."""
    entries = [human("hola"), say(f"Dos cifras:\n\n{bullet}1.126,84 €\n{bullet}1.131,84 €")]
    found = problems_at(entries, 5)
    assert len(found) == 1
    assert "regla 4:" in found[0]


@pytest.mark.parametrize(
    "text",
    [
        "1. Uno\n2. Dos\n\n2.1. Sub",
        "**Pregunta:** nada\n\n*cursiva* y ---",
        "| a | b |\n|---|---|\n| 1 | 2 |",
        "Código:\n```\n- no es una lista\n```",
    ],
)
def test_numbered_lists_bold_tables_and_code_pass_rule_4(text: str) -> None:
    assert problems_at([human("hola"), say(text)], 5) == []


# ---------------------------------------------------------------------------
# Rule 21: push notifications
# ---------------------------------------------------------------------------


def test_ending_on_a_question_without_push_is_blocked() -> None:
    """The user's complaint: 'no me he dado cuenta de que estabas esperando'."""
    entries = [human("¿y ahora?"), say("Recomendación: A. ¿Confirmas A?")]
    found = problems_at(entries, 5)
    assert len(found) == 1
    assert "regla 21:" in found[0]
    assert "esperando" in found[0]


def test_ending_on_a_question_with_push_passes() -> None:
    entries = [
        human("¿y ahora?"),
        call(gate.PUSH_TOOL, "p1"),
        result("p1", "Mobile push requested."),
        say("Recomendación: A. ¿Confirmas A?"),
    ]
    assert problems_at(entries, 5) == []


def test_long_job_finished_without_push_is_blocked() -> None:
    entries = [
        human("implementa"),
        call("Bash", "b1", command="pytest"),
        result("b1", at=200),
        say("Hecho y publicado.", at=201),
    ]
    found = problems_at(entries, 202)
    assert len(found) == 1
    assert "ha terminado un trabajo" in found[0]


def test_short_answer_to_a_present_user_needs_no_push() -> None:
    entries = [human("¿usas mi fichero real?"), say("Sí, en una carpeta temporal.")]
    assert problems_at(entries, 20) == []


def test_intermediate_turn_with_background_task_still_running_needs_no_push() -> None:
    """mutmut finished but the full pytest run is still going: routine progress, not done."""
    entries = [
        human("adelante"),
        call("Bash", "bg1", command="mutmut run", run_in_background=True),
        result("bg1", "Command running in background with ID: x"),
        call("Bash", "bg2", command="pytest", run_in_background=True),
        result("bg2", "Command running in background with ID: y"),
        say("Lanzados."),
        task_notification("bg1", at=300),
        say("mutmut limpio; sigo esperando pytest.", at=301),
    ]
    assert problems_at(entries, 302) == []


def test_turn_after_the_last_background_task_completes_needs_push() -> None:
    entries = [
        human("adelante"),
        call("Bash", "bg1", command="pytest", run_in_background=True),
        result("bg1", "Command running in background with ID: x"),
        say("Lanzado."),
        task_notification("bg1", at=300),
        say("Todo verde, commit publicado.", at=301),
    ]
    found = problems_at(entries, 302)
    assert len(found) == 1
    assert "regla 21:" in found[0]


def test_completion_notice_delivered_mid_turn_as_attachment_counts_as_finished() -> None:
    """Real shape found replaying this project's history: a task finishing while the agent is
    still working arrives as an `attachment` entry, not a user message."""
    attachment = {
        "type": "attachment",
        "timestamp": _ts(150),
        "attachment": {
            "type": "queued_command",
            "prompt": "<task-notification>\n<tool-use-id>bg1</tool-use-id>\n</task-notification>",
        },
    }
    entries = [
        human("adelante"),
        call("Bash", "bg1", command="mutmut run", run_in_background=True),
        result("bg1", "Command running in background with ID: x"),
        attachment,
        say("mutmut terminado, commit publicado.", at=200),
    ]
    found = problems_at(entries, 201)
    assert len(found) == 1
    assert "regla 21:" in found[0]


def test_auto_backgrounded_command_counts_as_pending_until_notified() -> None:
    entries = [
        human("adelante"),
        call("Bash", "b1", command="pytest"),
        result("b1", "Command did not complete within its 120s timeout and was moved to the background"),
        say("Sigue en segundo plano.", at=130),
    ]
    assert problems_at(entries, 131) == []


def test_foreground_agent_is_not_pending() -> None:
    entries = [
        human("investiga"),
        call("Agent", "a1", run_in_background=False),
        result("a1", "informe", at=200),
        say("Informe listo.", at=201),
    ]
    assert len(problems_at(entries, 202)) == 1


def test_hook_feedback_does_not_start_a_new_turn() -> None:
    """A push sent before a hook re-prompt still counts for the same turn."""
    entries = [
        human("hazlo"),
        call(gate.PUSH_TOOL, "p1"),
        result("p1"),
        say("Recomendación: A. ¿Confirmas?"),
        hook_feedback("algo", at=10),
        say("Recomendación: A. ¿Confirmas?", at=11),
    ]
    assert problems_at(entries, 12) == []


def test_both_rules_reported_together() -> None:
    entries = [human("¿y ahora?"), say("¿A o B?")]
    found = problems_at(entries, 5)
    assert len(found) == 2


def test_no_user_message_at_all_allows() -> None:
    assert problems_at([say("hola")], 5) == []


# ---------------------------------------------------------------------------
# main(): I/O contract and loop protection
# ---------------------------------------------------------------------------


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str) -> str:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    gate.main()
    return out.getvalue()


def _write_transcript(tmp_path: Path, entries: list[dict[str, Any]]) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return json.dumps({"transcript_path": str(path)})


def test_main_blocks_then_gives_up_after_the_per_turn_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _write_transcript(tmp_path, [human("¿y?"), say("¿A o B?")])
    outputs = [_run_main(monkeypatch, tmp_path, payload) for _ in range(3)]
    assert json.loads(outputs[0])["decision"] == "block"
    assert json.loads(outputs[1])["decision"] == "block"
    assert outputs[2] == ""


def test_main_allows_on_clean_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # main() reads the real clock, so the user's message must be recent for "present user".
    just_now = human("hola")
    just_now["timestamp"] = datetime.now(UTC).isoformat()
    payload = _write_transcript(tmp_path, [just_now, say("Hecho.")])
    assert _run_main(monkeypatch, tmp_path, payload) == ""


@pytest.mark.parametrize("payload", ["", "no-json", '{"transcript_path": "/nonexistent"}'])
def test_main_never_traps_on_bad_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    assert _run_main(monkeypatch, tmp_path, payload) == ""
