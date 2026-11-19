"""How the planner talks to the operator: its tone of voice, and how much technical detail it shares.

The planner exists to take friction out of the operator's decisions and to earn their trust. Words the
operator does not read do neither, so what the planner writes for the operator (its updates on a goal, and
its summary of a plan) follows two settings of the planning configuration's `voice`:

- `tone`: casual (as shipped), balanced or formal.
- `detail`: low (as shipped), medium or high. Low names no file, route, function or command, and keeps to what
  the operator needs to know and decide.

`instructions` turns the two into what a model is told. They shape only what is written for the operator: a
task's own description, which an agent works from, stays as exact as the work needs.
"""

from typing import Any

TONES = ("casual", "balanced", "formal")
DETAILS = ("low", "medium", "high")
DEFAULT = {"tone": "casual", "detail": "low"}

_TONE = {
    "casual": (
        "Tone: friendly and casual, the way a trusted colleague gives a quick update in person. Short, everyday "
        "words. Say 'I' for yourself and 'you' for the operator. No jargon, no formality, no hype and no praise."
    ),
    "balanced": (
        "Tone: plain and professional, as a capable colleague writes in a status note. Direct and warm, with no "
        "filler, no hype and no praise."
    ),
    "formal": "Tone: formal and precise, as in a written report. Complete sentences, no contractions, no praise.",
}
_DETAIL = {
    "low": (
        "Technical detail: as little as possible. Do not name files, paths, functions, routes, commands, branches "
        "or tools, and give no implementation detail. Say what is being done in terms of what the operator will "
        "be able to do or see when it is done. Raise only what the operator needs to know to trust the work or "
        "must decide; leave out assumptions and risks that need nothing from them."
    ),
    "medium": (
        "Technical detail: moderate. Name the part of the product a change touches and the approach in a phrase, "
        "but not files, functions or commands unless the operator must act on one."
    ),
    "high": (
        "Technical detail: full. Name the files, functions, routes and commands that matter, and state each "
        "assumption and risk."
    ),
}
# How long each kind of writing may be at each level of detail.
_LENGTH = {
    "update": {
        "low": "Length: a headline of under ten words and a body of one or two short sentences.",
        "medium": "Length: a headline of under twelve words and a body of two or three sentences.",
        "high": "Length: a headline of under fifteen words and a body of up to five sentences.",
    },
    "plan": {
        "low": "Length: a summary of two or three short sentences, and at most three gaps of one short sentence each.",
        "medium": "Length: a summary of up to four sentences, and each gap in a sentence.",
        "high": "Length: a summary of up to six sentences, and each gap in a sentence or two.",
    },
}


def of(planning: dict[str, Any]) -> dict[str, str]:
    """The planner's voice from the planning configuration, with what it leaves out."""
    voice = {**DEFAULT, **(planning.get("voice") or {})}
    return {
        "tone": voice["tone"] if voice["tone"] in TONES else DEFAULT["tone"],
        "detail": voice["detail"] if voice["detail"] in DETAILS else DEFAULT["detail"],
    }


def check(document: dict[str, Any]) -> list[str]:
    """Problems with a planning configuration's `voice`."""
    voice = document.get("voice", {})
    if not isinstance(voice, dict):
        return ["voice must be an object: the planner's tone and how much technical detail it shares"]
    reasons = []
    if voice.get("tone", DEFAULT["tone"]) not in TONES:
        reasons.append(f"voice.tone must be one of {', '.join(TONES)}: how the planner sounds to the operator")
    if voice.get("detail", DEFAULT["detail"]) not in DETAILS:
        reasons.append(
            f"voice.detail must be one of {', '.join(DETAILS)}: how much technical detail the planner shares"
        )
    return reasons


def instructions(planning: dict[str, Any], writing: str) -> str:
    """What a model is told about how to write for the operator. writing: `update`, for an update on a goal's
    page, or `plan`, for a plan's summary and gaps."""
    voice = of(planning)
    return " ".join([_TONE[voice["tone"]], _DETAIL[voice["detail"]], _LENGTH[writing][voice["detail"]]])
