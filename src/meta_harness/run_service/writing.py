"""The writing pass: the control plane's last look at text it is about to post for people to read.

A run is told to apply its writing skill to its own summary, review or drafts
as it writes them. This pass checks the result once more, after the run, and
needs no run: a pull request description, a review's summary and findings, a
comment, an issue a plan drafted.

    1. The text goes through the writing skill's scanner, the script the shipped
       skill holds (the skill is locked), run here with no model.
    2. Text with findings gets one rewrite on the model `config/planner.json`'s
       `writing_pass` names, through the HTTP Gateway.
    3. The rewrite is kept only when it has no findings left and keeps every
       heading, link, code span, code block and number of the original.
       Otherwise it is dropped and the original is posted.

The pass never stops a post: a scanner that cannot run, a HTTP Gateway that
does not answer, or a rewrite that fails its checks all leave the original
text, with the reason in the outcome. The task policy's `writing_pass: false`
turns the pass off. Only future text goes through it; nothing posted is changed.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import httpx

from meta_harness import retrying
from meta_harness.config import store as config_store
from meta_harness.run_service.paths import REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "planner.json"
# The longest text the pass rewrites: a longer one is scanned, and posted as it is.
MAX_REWRITE_CHARS = 20_000
SCAN_SECONDS = 20
REWRITE_SECONDS = 45
# The longest the pass takes over the texts of one post, inside the activity that posts them: past it, the
# texts not yet done are posted as they were.
PASS_SECONDS = 70

REWRITE_SYSTEM_PROMPT = (
    "You edit text that software is about to post on a code host for people to read: a pull request "
    "description, a code review, an issue or a comment. A checker found the listed wording problems in it. "
    "Rewrite the text so that none of them remains, in plain words and short sentences. Keep the meaning and "
    "every fact. Keep every heading, link, code span, code block, number, file path and name exactly as it is, "
    "and keep the Markdown structure. A finding that counts a sentence's words quotes the sentence: make it two "
    "sentences, or cut a clause, so that no sentence you leave has more than 25 words. Change nothing the "
    "findings do not require. Add nothing: no preface, no closing remark. Return the whole text."
)
REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


class WritingError(RuntimeError):
    """The pass could not check or rewrite a text. The text is posted as it was."""


@dataclass
class Outcome:
    """What the pass did with one text. `text` is what to post: the rewrite when it was kept, else the original."""

    text: str
    # Findings the scanner decided in the original, and in the rewrite when one was made.
    findings: int = 0
    findings_after: int | None = None
    categories: dict[str, int] = field(default_factory=dict)
    rewritten: bool = False
    kept: bool = False
    # Why the text was left as it was: empty when it had no findings, or its rewrite was kept.
    reason: str = ""
    model: str = ""
    usd_cost: str = "0"
    before: str = ""


def record(kind: str, outcomes: list[Outcome | None], **extra: Any) -> dict[str, Any] | None:
    """The `writing_checked` event's payload for the texts of one post: a pull request description, a review
    with its findings, a comment, an issue. None when the pass looked at none of them (it is off, or they
    were empty). The original and the rewrite are carried only for a text that changed, so a clean post
    records a line and not its body."""
    looked = [outcome for outcome in outcomes if outcome is not None]
    if not looked:
        return None
    categories: Counter = Counter()
    for outcome in looked:
        categories.update(outcome.categories)
    cost = sum((Decimal(outcome.usd_cost or "0") for outcome in looked), Decimal(0))
    return {
        "kind": kind,
        "texts": len(looked),
        "findings": sum(outcome.findings for outcome in looked),
        "categories": dict(categories),
        "rewritten": sum(1 for outcome in looked if outcome.rewritten),
        "kept": sum(1 for outcome in looked if outcome.kept),
        "reasons": list(dict.fromkeys(outcome.reason for outcome in looked if outcome.reason)),
        "model": next((outcome.model for outcome in looked if outcome.model), ""),
        "usd_cost": str(cost),
        "changes": [{"before": outcome.before, "after": outcome.text} for outcome in looked if outcome.kept],
        **extra,
    }


@cache
def settings() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["writing_pass"]


def check_settings() -> None:
    """Raises RuntimeError naming what is wrong with config/planner.json's writing_pass."""
    try:
        config = settings()
    except (OSError, ValueError, KeyError) as err:
        raise RuntimeError(f"{CONFIG_PATH.name}: no writing_pass settings ({err})") from err
    if not isinstance(config, dict):
        raise RuntimeError(f"{CONFIG_PATH.name}: writing_pass must be an object")
    for key in ("skill", "scanner", "rules", "engine", "model"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise RuntimeError(f"{CONFIG_PATH.name}: writing_pass.{key} must name one")
    names = config.get("names", [])
    if not isinstance(names, list) or not all(isinstance(name, str) and name.strip() for name in names):
        raise RuntimeError(
            f"{CONFIG_PATH.name}: writing_pass.names must be a list of names the scanner should not count as findings; use [] for none"
        )
    tokens = config.get("max_response_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0:
        raise RuntimeError(f"{CONFIG_PATH.name}: writing_pass.max_response_tokens must be a positive whole number")


def enabled() -> bool:
    """Whether the pass runs: on unless the task policy says `writing_pass: false`. A policy that cannot be
    read leaves it on, since the pass never stops a post."""
    try:
        return config_store.current_document("task_policy").get("writing_pass") is not False
    except Exception:  # noqa: BLE001 - an unreadable policy must not fail a post
        return True


def scanner_source() -> str:
    """The scanner script's text, from the shipped skill: config/skills/<skill>/<scanner>. Never from the
    stored `skills` configuration: this process runs the script, so what it holds is decided by a pull
    request, and the skill is locked (config/skills.py)."""
    from meta_harness.config import skills

    config = settings()
    path = skills.skills_dir() / config["skill"] / config["scanner"]
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as err:
        raise WritingError(f"the shipped scanner could not be read: {err}") from err
    if not source.strip():
        raise WritingError(f"the shipped scanner {path} is empty")
    return source


def scan(text: str, source: str | None = None) -> dict[str, Any]:
    """The scanner's report on text. The script runs as a child process with no model, isolated from this
    process's Python path and given none of its environment, so it holds no credential. It is given the
    text on standard input."""
    with tempfile.TemporaryDirectory(prefix="mh-writing-") as folder:
        script = Path(folder) / "scan.py"
        script.write_text(source if source is not None else scanner_source(), encoding="utf-8")
        try:
            done = subprocess.run(  # noqa: S603 - the script is the deployment's own configuration
                [sys.executable, "-I", "-X", "utf8", str(script), "--compact"],
                input=text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=SCAN_SECONDS,
                cwd=folder,
                env={},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            raise WritingError(f"the scanner did not run: {err}") from err
    try:
        report = json.loads(done.stdout)
    except ValueError as err:
        raise WritingError(f"the scanner printed no report (exit {done.returncode})") from err
    if done.returncode != 0 or not isinstance(report, dict) or "error" in report:
        raise WritingError(f"the scanner failed: {report.get('error') if isinstance(report, dict) else report}")
    return report


def names() -> set[str]:
    """The words that are names in this deployment (`writing_pass.names`), in lower case. The scanner's word
    lists hold `harness`, which a model overuses as a verb and which is this product's name: counted as a
    finding, it sent every text that names the meta-harness to a rewrite that could only fail its check or
    rename the product."""
    return {str(name).strip().lower() for name in settings().get("names") or []}


def decided(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The hits the scanner decides by itself. A candidate, which needs a reader to confirm, is not a finding
    here, and neither is a hit the scanner says cannot be applied, nor a word that is a name here."""
    named = names()
    return [
        hit
        for hit in report.get("hits") or []
        if isinstance(hit, dict)
        and hit.get("decides")
        and not hit.get("in_unterminated_region")
        and str(hit.get("match") or "").strip().lower() not in named
    ]


FENCE = re.compile(r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)
CODE_SPAN = re.compile(r"`[^`\n]+`")
HEADING = re.compile(r"^#{1,6}[ \t]+.*$", re.MULTILINE)
LINK_TARGET = re.compile(r"\]\(([^)\s]+)")
URL = re.compile(r"https?://[^\s)>\]]+")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
WORD = re.compile(r"[a-z][a-z0-9]*")
# Lines the control plane reads back from an issue or a pull request, which a rewrite must leave as they are:
# an issue's paths and dependencies, what a pull request closes, and each line of an issue's acceptance checks.
READ_BACK = re.compile(r"^(?:Paths|Depends on|Closes|Fixes|Resolves)\b.*$", re.MULTILINE | re.IGNORECASE)
CHECKS_SECTION = re.compile(r"^#{1,6}[ \t]+Acceptance checks[ \t]*\n(.*?)(?=^#{1,6}[ \t]|\Z)", re.MULTILINE | re.DOTALL)


def read_back_lines(text: str) -> set[int]:
    """The numbers of the lines the control plane reads back (READ_BACK, and each line under an Acceptance
    checks heading), counted from 1. A rewrite must keep them as they are, so the scanner's hit on one, such
    as a long check counted as a long sentence, is no finding: no rewrite could clear it."""
    found = {text.count("\n", 0, match.start()) + 1 for match in READ_BACK.finditer(text)}
    for section in CHECKS_SECTION.finditer(text):
        first = text.count("\n", 0, section.start(1)) + 1
        found.update(first + offset for offset, line in enumerate(section.group(1).splitlines()) if line.strip())
    return found


def findings(text: str, source: str | None = None) -> list[dict[str, Any]]:
    """The scanner's findings in a text: its decided hits, but for those on a line a rewrite must keep."""
    kept = read_back_lines(text)
    return [hit for hit in decided(scan(text, source)) if hit.get("line") not in kept]


def kept_parts(text: str) -> dict[str, Counter]:
    """The parts of a text a rewrite must keep, each with how often it occurs: the lines the control plane
    reads back, fenced code blocks, code spans, headings, link targets, and the numbers and this deployment's
    names outside code."""
    blocks = [match.group(0) for match in FENCE.finditer(text)]
    prose = FENCE.sub("", text)
    spans = CODE_SPAN.findall(prose)
    outside = CODE_SPAN.sub("", prose)
    checks = [
        line.strip() for section in CHECKS_SECTION.findall(prose) for line in section.splitlines() if line.strip()
    ]
    return {
        "line the control plane reads": Counter([line.strip() for line in READ_BACK.findall(prose)] + checks),
        "code block": Counter(blocks),
        "code span": Counter(spans),
        "heading": Counter(line.rstrip() for line in HEADING.findall(outside)),
        "link": Counter(LINK_TARGET.findall(outside) + [url.rstrip(".,;:") for url in URL.findall(outside)]),
        "number": Counter(NUMBER.findall(outside)),
        "name": Counter(word for word in WORD.findall(outside.lower()) if word in names()),
    }


def lost(original: str, rewrite: str) -> str:
    """What a rewrite dropped that the original had, as a reason; empty when it kept everything."""
    before, after = kept_parts(original), kept_parts(rewrite)
    for name, parts in before.items():
        missing = parts - after[name]
        if missing:
            example = next(iter(missing))
            return f"the rewrite lost a {name}: {example[:80]}"
    return ""


LONG_SENTENCE = re.compile(r"^sentence of (\d+) words$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
SENTENCE_COUNT_SLACK = 4


def long_sentences(text: str, hits: list[dict[str, Any]]) -> dict[int, str]:
    """The sentence each long-sentence finding is about, by the finding's place in hits. The scanner reports
    such a finding as a line number and a word count, and a line of a description is a whole paragraph: told
    only that, the model rewriting it has to count lines and guess which of several sentences is meant. So
    the sentence is found here: on that line, the one not yet named whose word count is nearest."""
    lines = text.splitlines()
    taken: dict[int, set[int]] = {}
    found: dict[int, str] = {}
    for place, hit in enumerate(hits):
        counted = LONG_SENTENCE.match(str(hit.get("match") or ""))
        number = hit.get("line")
        if not counted or not isinstance(number, int) or not 0 < number <= len(lines):
            continue
        parts = [part.strip() for part in SENTENCE_END.split(lines[number - 1]) if part.strip()]
        used = taken.setdefault(number, set())
        # The scanner counts words after it masks code, so its count and this one may differ by a few.
        words = int(counted.group(1))
        near = [(abs(len(part.split()) - words), i) for i, part in enumerate(parts) if i not in used]
        if near and min(near)[0] <= SENTENCE_COUNT_SLACK:
            used.add(min(near)[1])
            found[place] = parts[min(near)[1]]
    return found


def _prompt(text: str, hits: list[dict[str, Any]]) -> str:
    sentences = long_sentences(text, hits)
    lines = ["--- findings ---"]
    for place, hit in enumerate(hits):
        fix = f" Fix: {hit['fix']}." if hit.get("fix") else ""
        which = f' The sentence: "{sentences[place]}"' if place in sentences else ""
        lines.append(f'line {hit.get("line")}: "{hit.get("match")}" ({hit.get("category_name")}).{fix}{which}')
    return "\n".join([*lines, "--- end of findings ---", "--- text ---", text, "--- end of text ---"])


async def rewrite(
    text: str, hits: list[dict[str, Any]], transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, str]:
    """One model call that rewrites text to clear the findings. Returns (the rewrite, what the call cost)."""
    # Here, not at the top: the rules module reads this one's settings.
    from meta_harness.outbound import rules as writing_rules

    config = settings()
    url = os.environ.get("GATEWAY_URL", "").rstrip("/")
    key = os.environ.get("GATEWAY_API_KEY", "")
    if not (url and key):
        raise WritingError("GATEWAY_URL and GATEWAY_API_KEY are not set")
    body = {
        "engine": config["engine"],
        "model": config["model"],
        "system_prompt": writing_rules.after(REWRITE_SYSTEM_PROMPT),
        "prompt": _prompt(text, hits),
        "response_schema": REWRITE_SCHEMA,
        "max_response_tokens": int(config["max_response_tokens"]),
    }
    async with httpx.AsyncClient(timeout=REWRITE_SECONDS, transport=retrying.async_transport(transport)) as http:
        try:
            response = await http.post(f"{url}/v1/structured", json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as err:
            raise WritingError(f"the HTTP Gateway is unreachable: {err}") from err
    if response.status_code != 200:
        raise WritingError(f"the HTTP Gateway answered {response.status_code}: {response.text[:200]}")
    result = response.json()
    data = result.get("data")
    if result.get("finish_reason") != "complete" or not isinstance(data, dict):
        raise WritingError(f"the model did not finish ({result.get('finish_reason')})")
    written = str(data.get("text") or "").strip()
    if not written:
        raise WritingError("the model wrote nothing")
    return written, str(result.get("usd_cost") or "0")


async def check(text: str, transport: httpx.AsyncBaseTransport | None = None) -> Outcome | None:
    """Run the pass on one text. Returns None when the pass is off or the text is empty: nothing to record.
    Never raises: whatever goes wrong, the outcome carries the original text and the reason."""
    if not text.strip() or not enabled():
        return None
    outcome = Outcome(text=text)
    try:
        source = await asyncio.to_thread(scanner_source)
        hits = await asyncio.to_thread(findings, text, source)
        outcome.findings = len(hits)
        outcome.categories = dict(Counter(str(hit.get("category_name") or hit.get("category")) for hit in hits))
        if not hits:
            return outcome
        if len(text) > MAX_REWRITE_CHARS:
            outcome.reason = f"the text is over {MAX_REWRITE_CHARS} characters, too long to rewrite"
            return outcome
        config = settings()
        outcome.model = f"{config['engine']}:{config['model']}"
        written, outcome.usd_cost = await rewrite(text, hits, transport)
        outcome.rewritten = True
        left = await asyncio.to_thread(findings, written, source)
        outcome.findings_after = len(left)
        if left:
            # Which ones, so a record of dropped rewrites shows what the model could not clear.
            named = ", ".join(dict.fromkeys(str(hit.get("match"))[:40] for hit in left[:3]))
            outcome.reason = f"the rewrite still has {len(left)} of the scanner's findings ({named})"
        elif reason := lost(text, written):
            outcome.reason = reason
        else:
            outcome.before, outcome.text, outcome.kept = text, written, True
    except WritingError as err:
        outcome.reason = str(err)
    except Exception as err:  # noqa: BLE001 - the pass never stops a post
        outcome.reason = f"the writing pass failed: {type(err).__name__}: {err}"
    return outcome


async def check_all(texts: list[str], transport: httpx.AsyncBaseTransport | None = None) -> list[Outcome | None]:
    """Run the pass on the texts of one post, at once, within PASS_SECONDS in all. A text the pass did not
    finish in time is posted as it was, and its outcome says so."""
    jobs = [asyncio.ensure_future(check(text, transport)) for text in texts]
    if not jobs:
        return []
    _, late = await asyncio.wait(jobs, timeout=PASS_SECONDS)
    outcomes: list[Outcome | None] = []
    for text, job in zip(texts, jobs, strict=True):
        if job in late:
            job.cancel()
            outcomes.append(Outcome(text=text, reason=f"the writing pass took over {PASS_SECONDS} seconds"))
        else:
            outcomes.append(job.result())
    return outcomes
