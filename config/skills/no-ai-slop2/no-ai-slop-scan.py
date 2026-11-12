#!/usr/bin/env python3
"""Deterministic slop scan for no-ai-slop2.

The half of `no-ai-slop` that a regular expression matches better than a model
does: word lists, phrase lists, and the measurable checks. Roughly 3,000 words
of tables that used to be re-read on every load, and that nobody could test.

Two tiers, because a match is not always a hit:

  * `decides: true` — the match *is* the finding. `delve`, a curly quote, a
    38-word sentence. The skill reports it as given.
  * `decides: false` — a trigger the agent confirms with the category's own
    test. `instead of` is usually antithesis and sometimes a plain statement
    of an alternative; only reading decides.

Code and quoted text are skipped by construction. `no-ai-slop`'s rule "never
alter code, inline code, or quoted text" was an instruction an agent had to
remember; here fenced blocks, inline spans, and blockquote lines are masked
before any rule runs, so a rule cannot see them.

Usage:
    no-ai-slop-scan.py --file PATH [--json|--report]
    no-ai-slop-scan.py < text

Prints one JSON object. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Precedence — `no-ai-slop`'s own tie-break, as data.
#
# "A phrase may match several categories; label it once, under the earliest in
# this order." Structural tells first, formatting next, the bare word lists
# last, so a meaningful tell always beats a word match.
# ---------------------------------------------------------------------------

PRECEDENCE = ["A", "I", "H", "B", "N", "D", "O", "P", "M", "V", "R", "S", "T", "U", "F", "C", "E"]
RANK = {c: i for i, c in enumerate(PRECEDENCE)}

CATEGORY_NAMES = {
    "A": "antithesis",
    "B": "bloat (padding / filler)",
    "C": "overly complex language",
    "D": "grammatical density",
    "E": "excess vocabulary",
    "F": "formatting tells",
    "H": "hollow enumeration",
    "I": "inverted claim",
    "M": "melodrama / emotive register",
    "N": "announcement in place of content",
    "O": "overstated opinion / editorializing",
    "P": "puffery",
    "R": "conversation residue",
    "S": "sycophancy / false candor",
    "T": "transition crutches",
    "U": "unrequested effort",
    "V": "metaphor",
}

# Categories no rule here can decide: they need a sentence read in context.
# Named so the skill can enumerate what it still owns, and so a reader can see
# the split is deliberate rather than an omission.
JUDGMENT_ONLY = {
    "I": "inverted claim — one claim split across a main clause and a demoted tail",
    "H": "hollow enumeration — three examples that add nothing to the claim",
    "B": "bloat — tautology, restatement, audience padding",
    "V": "metaphor that obscures the plain fact",
    "O": "overstated opinion — a verdict pushed past its evidence",
}

# ---------------------------------------------------------------------------
# Mechanical rules: a match is a hit.
# ---------------------------------------------------------------------------

# E — excess vocabulary (Kobak 2025, Liang 2024, Reinhart 2025, GPTZero 2026).
EXCESS_VOCAB = [
    "delve", "delving", "underscore", "underscores", "underscoring", "showcase",
    "showcases", "showcasing", "pivotal", "intricate", "intricacies", "tapestry",
    "palpable", "amidst", "camaraderie", "boasts", "realm", "garner", "garnered",
    "foster", "fostering", "robust", "vibrant", "testament", "align with",
    "aligns with", "crucial", "comprehensive", "enhance", "enhances", "enhancing",
    "leverage", "leverages", "leveraging", "harness", "harnesses", "commendable",
    "meticulous", "meticulously", "lucid", "nuanced", "seamless", "seamlessly",
    "significant", "implication", "implications", "navigate", "navigates",
    "navigating", "highlight", "highlights", "emphasize", "emphasizes",
    "emphasizing", "stark", "carries", "drift",
]

# P — puffery and significance/legacy clusters.
PUFFERY = [
    "stands as a testament to", "serves as", "plays a vital role",
    "plays a crucial role", "plays a pivotal role", "underscores its importance",
    "reflects a broader", "reflects broader", "leaves an indelible mark",
    "enduring legacy", "rich cultural tapestry", "rich cultural heritage",
    "rich tapestry", "groundbreaking", "renowned", "world-class", "game-changer",
    "game changer", "unlock", "unlocks", "unleash", "unleashes", "elevate",
    "elevates", "supercharge", "supercharges",
]

# T — transition crutches. Sentence-initial forms are marked in the pattern.
TRANSITION_INITIAL = ["moreover", "furthermore", "additionally", "in addition",
                      "in conclusion", "ultimately", "that said"]
TRANSITION_ANY = [
    "it's important to note that", "it is important to note that",
    "at the end of the day", "when it comes to", "in today's fast-paced world",
    "let's dive in", "lets dive in",
]

# S — sycophancy, false candor, faux-stakes.
SYCOPHANCY = [
    "you're absolutely right", "you are absolutely right", "you're so right",
    "you're right to push back", "that's a fair criticism", "that's a fair point",
    "fair point", "great question", "great point", "excellent point",
    "excellent question", "that's a great observation", "i appreciate you",
]
FALSE_CANDOR = [
    "honest answer", "the honest answer is", "to be honest", "if i'm being honest",
    "let me be honest", "the truth is", "truthfully", "i'll be direct",
    "the honest case", "an honest look at",
]
FAUX_STAKES = [
    "the only thing that matters", "here's what really matters",
    "here's what matters", "the part that matters", "what matters here is",
]

# U — unrequested effort: a trailing offer of work nobody asked for.
TRAILING_OFFER = [
    "want me to", "would you like me to", "let me know if you'd like me to",
    "let me know if you want me to", "i can also", "happy to", "should i",
]
# The other half of U: work announced rather than offered.
VOLUNTEERED_SCOPE = ["i also went ahead and", "while i was at it", "i took the liberty of",
                     "i went ahead and"]

# N — worthiness assertions. The writer grading their own material.
WORTHINESS = [
    "worth noting", "worth separating", "worth calling out", "worth flagging",
    "worth mentioning", "worth understanding", "the noteworthy part",
    "deserves attention", "bears repeating",
]
# N — forward-announcing preambles.
PREAMBLE = [
    "two things are worth separating", "there are three things",
    "a few points stand out", "here's the key distinction", "let me break this down",
    "some context first",
]
# N — two-topic heading: a heading or lead-in built from two topic clauses joined
# by "and" — "What each part does, and the numbers it has to hit", "How it
# works and why it matters". It names what is coming and states none of it.
# Anchored to a whole line, so a sentence that merely opens with "How" and
# carries on past a comma is not caught. "When" and "Which" are left out:
# "When the user asks and the branch exists" is a trigger condition, not a
# preamble. The opening word must be capitalized: a hard-wrapped prose line
# that happens to start with "what" is a continuation, not a heading. "How to"
# opens a task title ("How to install the CLI and the plugin"), so it is left
# out too, as are "Where to" and "Who to" in any case. The wh-word boundaries
# are lookarounds rather than \b, which fails against a closing "__". A second
# wh-word may stand alone ("What changed and why"); a second
# "the" needs a noun after it. A candidate, because a fixed template heading
# can be legitimate. The optional tail is grouped so the two whitespace runs
# cannot trade characters, which made a long trailing run quadratic.
TWO_TOPIC_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?"
    r"(?:(?:Here's|Here’s|Here is|This covers|Below is)\s+(?i:what|how|why|where|who)"
    r"|What|How|Why|Where|Who)(?![A-Za-z0-9])(?!(?i:\s+to\b))"
    r"(?i:[^,.;:!?\n]{1,60}?,?\s+and\s+"
    r"(?:(?:what|how|why|where|who)(?![a-z0-9])[^,.;:!?\n]{0,60}|the\b[^,.;:!?\n]{1,60}))"
    r"(?:\*\*|__)?(?:\s*[.:])?\s*$"
)

# N — counted lead-in: a count announcing the list that follows — "Two
# examples:", "Three reasons why it fails:", "Here are four ways to fix it".
# The reader can see how many items there are. Every form but "Here are" and
# "Below are" needs the colon that opens the list, so "Two tests fail." and
# "There are 12 open issues." are left alone, and no comma may come before
# that colon. A candidate, because a count the reader acts on ("3 tests
# fail:") is content. `lead` is the reported match, without list markup.
_COUNT = r"(?:two|three|four|five|six|seven|eight|nine|ten|\d{1,2})"
COUNTED_LEAD_IN = re.compile(
    r"(?:^\s*|[.!?]\s+)(?:[-*+]\s+|#{1,6}\s+)?(?:\*\*|__)?(?P<lead>"
    r"(?:here are|below are)\s+(?:the\s+)?" + _COUNT + r"\s+[a-z]+"
    r"|(?:there are\s+)?" + _COUNT + r"\s+(?:[a-z]+\s+){0,2}?[a-z]+s\b[^,:.!?\n]{0,40}:)",
    re.IGNORECASE,
)

# C — fancy words with a plain replacement (speak-simply's table A).
FANCY_WORDS = {
    "utilize": "use", "utilization": "use", "utilizing": "using",
    "implementation": "build", "functionality": "feature",
    "facilitate": "help", "facilitates": "helps", "aforementioned": "earlier",
    "subsequent": "next", "subsequently": "then", "prior to": "before",
    "in order to": "to", "with regard to": "about", "regarding": "about",
    "in the event that": "if", "due to the fact that": "because",
    "at this point in time": "now", "in close proximity to": "near",
    "demonstrate": "show", "demonstrates": "shows", "endeavor": "try",
    "obtain": "get", "commence": "start", "terminate": "end",
    "necessitate": "need", "necessitates": "needs", "approximately": "about",
    "numerous": "many", "sufficient": "enough",
}
# C — redundant pairs.
REDUNDANT_PAIRS = ["each and every", "first and foremost", "null and void",
                   "true and accurate", "various and sundry", "full and complete",
                   "past history", "future plans", "end result"]
# C — empty intensifiers and hedge filler.
EMPTY_INTENSIFIERS = ["really", "actually", "genuinely", "truly", "basically",
                      "essentially", "somewhat", "quite", "very", "extremely"]

LONG_SENTENCE_WORDS = 25
EM_DASHES_PER_PARAGRAPH = 2  # more than this in one paragraph is overuse
CURLY = "“”‘’"
HYPE_EMOJI = "\U0001F680\U0001F389\U0001F525✨\U0001F4AA\U0001F31F"

# F — numbered headings. A number is decoration in a blog post and structure in
# a spec, a contract, or a paper. The heading cannot tell them apart, so the hit
# is a candidate the agent confirms — and when the body cites its own numbers,
# the numbers are load-bearing and there is nothing to confirm, so no hit is
# emitted at all. Stripping one there breaks every cross-reference to it, and
# the gate would allow it: a heading may be retitled when the change maps to a
# hit in that pass.
NUMBERED_HEADING_RE = re.compile(r"^#{1,6}\s+\d+(\.\d+)*[.)]?\s+\S")
SECTION_CITATION_RE = re.compile(
    r"(?:\b(?:section|clause|article|appendix|annex|part|paragraph|figure|table|step)\s+|§\s*|\b(?:see|per|under|in)\s+)"
    r"\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Candidate rules: a trigger the agent confirms.
# ---------------------------------------------------------------------------

# A — antithesis triggers. Every one has a legitimate use, so none decides.
ANTITHESIS = [
    (r"\bnot just\b", "not just X, but Y"),
    (r"\bnot only\b", "not only X but Y"),
    (r"\brather than\b", "rather than Y"),
    (r"\binstead of\b", "instead of Y"),
    (r"\bmore than just\b", "more than just X"),
    (r"\bisn't just\b", "isn't just X — it's Y"),
    (r"\bit's not about\b", "it's not about X, it's about Y"),
    (r"\bgone are the days\b", "gone are the days of X"),
    (r"\b(compose|use|do|write|ship|keep|state)\b[^.;]{0,40},\s+(don't|do not)\b", "do X, don't Y"),
    (r",\s+not\s+\w+", "X, not Y"),
    (r"—\s*not\s+\w+", "X — not Y"),
]

# M — melodrama: narrative grandeur and emotive diction for routine work.
MELODRAMA = ["saga", "odyssey", "nightmare", "epic", "war story", "rollercoaster",
             "scar tissue", "battle-tested", "hard-won", "painful lessons",
             "baptism by fire", "bleeding edge", "aversion", "terrified of",
             "obsessed with", "brutal", "heroic", "traumatic"]

# R — conversation residue: text that points at an exchange the reader missed.
RESIDUE = ["as you requested", "per your feedback", "to address your point",
           "as we discussed", "you're right that", "the corrected version",
           "the updated approach", "as noted above", "we no longer use"]

# D — grammatical density.
PARTICIPIAL_TAIL = re.compile(
    r",\s+(highlighting|underscoring|reflecting|showcasing|emphasizing|demonstrating|"
    r"illustrating|showing|ensuring|allowing|enabling)\b", re.IGNORECASE)
NOMINALIZATION = re.compile(
    r"\b\w{4,}(?:tion|ment|ity|ance|ence)\b(?:\s+of\b)", re.IGNORECASE)
THAT_CLAUSE_SUBJECT = re.compile(r"(?:^|(?<=[.!?]\s))\s*That\s+the\b", re.MULTILINE)
PASSIVE = re.compile(
    r"\b(?:was|were|is|are|been|being)\s+(\w+ed|written|made|built|done|given|taken|"
    r"seen|shown|held|kept|found|sent|set|put|read)\b(?:\s+by\b)?", re.IGNORECASE)

# O — overstated opinion. Absolutes and verdict adjectives.
OVERSTATED = ["clearly the best", "simply wrong", "the only sane choice",
              "obviously", "everyone knows", "by far", "old and boring"]


def _lit(phrase: str) -> str:
    """A phrase pattern that tolerates any run of whitespace between words."""
    return r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b"


# ---------------------------------------------------------------------------
# Masking — code and quotes are removed before any rule runs.
# ---------------------------------------------------------------------------

# A fence opener of three or more of one character; the closer must match it,
# so a ````markdown block documenting ``` does not end at the inner fence.
# The indent is captured because CommonMark allows at most three columns over
# the container: four or more is indented code, not a fence.
FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
MAX_FENCE_INDENT = 3
# An indented code block: four spaces or a tab. A list continuation is
# indented the same way and is prose, so it is excluded by tracking whether
# the block follows a list item rather than by the indent alone.
# Any run of spaces and tabs before content; the width is measured in columns
# by `_column`, so a mixed "   \t" indent counts as the four columns it is.
INDENTED_CODE_RE = re.compile(r"^([ \t]+)\S")
LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+\S")
# A code span opens with a run of backticks and closes with a run of the same
# length. Pairing single ticks only left ``robust`` unmasked, and worse, the
# old pattern matched the empty gaps between the doubled ticks — so it counted
# spans as masked while the words inside stayed visible to every rule.
INLINE_CODE_RE = re.compile(r"(`+)[^\n]*?\1(?!`)")
FRONTMATTER_SCAN_LIMIT = 500
# Frontmatter opens with a YAML key; a horizontal rule is followed by prose.
# Without this a leading `---` rule was read as unterminated frontmatter, and
# fence parsing then started past a real code block, which was scanned.
YAML_KEY_RE = re.compile(r"^[\w.$-]+\s*:(\s|$)")
# A line carrying a citation marker, where a quoted span is a cited title.
CITATION_LINE_RE = re.compile(
    r"(https?://|doi:|arXiv:|\bet al\b|\(\d{4}\)|\b(19|20)\d{2}\b\s*[—–-]|\*[A-Z][^*]{2,}\*)")


def _indented_code_set(lines: list[str]) -> set[int]:
    """Indices masked as indented code, decided once.

    `mask()` and `indented_code_lines()` were two implementations of this one
    decision and disagreed on 1% of documents, so the gate row added to catch
    a misread line was blind exactly where the misreads happen.
    """
    spans, unclosed = fence_spans(lines, start=fence_start(lines))
    front = frontmatter_end(lines)
    literal = literal_lines(lines, spans, unclosed, front)
    container = container_indents(lines, literal)
    skip = set(literal)
    skip.update(spans)                       # opener lines
    for o, c in spans.items():
        skip.add(c)                          # closer lines
    skip.update(unclosed)
    out = set()
    for idx, line in enumerate(lines):
        if idx in skip or (front >= 0 and idx <= front):
            continue
        if line.lstrip().startswith(">"):
            continue
        if not INDENTED_CODE_RE.match(line):
            continue
        floor = 4 if not container[idx] else container[idx] + 4
        if _column(line[:len(line) - len(line.lstrip(" \t"))]) >= floor:
            out.add(idx)
    return out


def indented_code_lines(text: str) -> list[tuple[int, str]]:
    """(index, line) for every line masked as indented code.

    Exported so the gate can compare these lines the way it compares fenced
    blocks. Every blocking finding from four review rounds was the same shape
    — code scanned as prose, then rewritten — and the gate covered fenced
    blocks only, so it returned `stands` on the rewrite every time.
    """
    lines = text.split("\n")
    return [(i, lines[i]) for i in sorted(_indented_code_set(lines))]


def mask(text: str) -> tuple[list[str], dict, list[int]]:
    """Blank out code and quoted text, preserving line and column offsets.

    Returns the masked lines, the skipped counts, and the indices of any fence
    opener that never closed — the caller needs those to mark hits the gate
    will freeze.

    Every rule below runs on the masked copy, so "never alter code or quoted
    text" holds because the rule cannot see them, rather than because an agent
    remembered the caveat.
    """
    lines = text.split("\n")
    out: list[str] = []
    counts = {"fenced_blocks": 0, "inline_code_spans": 0, "quoted_lines": 0,
              "quoted_spans": 0, "frontmatter_lines": 0, "indented_code_lines": 0,
              "unterminated_frontmatter": False, "unterminated_fence": False}

    # Only treat a leading `---` as frontmatter when it actually closes. An
    # unterminated one masked every line, the scan returned zero hits, and the
    # loop called a document it never read CLEAN. Same for an unclosed fence.
    front_end = frontmatter_end(lines)
    unterminated_front = bool(_opens_frontmatter(lines) and front_end < 0)
    counts["unterminated_frontmatter"] = unterminated_front
    # Fence parsing starts after frontmatter, so a fence-shaped line in a YAML
    # block scalar cannot pair with a real block's opener. An *unterminated*
    # `---` needs the same protection: starting at 0 put the block scalar back
    # in the grammar and reproduced the bug this guard exists to prevent, so
    # skip to the first blank line, which ends any plausible YAML header.
    fence_close, unclosed = fence_spans(lines, start=fence_start(lines))
    counts["unterminated_fence"] = bool(unclosed)

    in_fence_until = -1
    # One decision, shared with `indented_code_lines` and therefore with the
    # gate row: tracking it inline here made the two disagree on 1% of
    # documents, and the gate was blind exactly where the scan misread a line.
    indented_code = _indented_code_set(lines)
    for idx, line in enumerate(lines):
        if front_end >= 0 and idx <= front_end:
            counts["frontmatter_lines"] += 1
            out.append("")
            continue
        if idx in fence_close:
            in_fence_until = fence_close[idx]
            counts["fenced_blocks"] += 1
            out.append("")
            continue
        if idx <= in_fence_until:
            out.append("")
            continue
        if idx in indented_code:
            counts["indented_code_lines"] += 1
            out.append("")
            continue
        if line.lstrip().startswith(">"):
            counts["quoted_lines"] += 1
            out.append("")
            continue
        masked, n = INLINE_CODE_RE.subn(lambda m: " " * len(m.group(0)), line)
        counts["inline_code_spans"] += n
        # A cited work is someone else's words, and rewriting it would be
        # wrong. Masking *every* quoted span was far too broad: prose about
        # writing quality quotes phrases constantly, and it made the whole
        # scan blind to any text a caller wrapped in quotes — including this
        # skill's own eval fixture. So mask only a citation: a quoted span on
        # a line that also carries a bibliographic marker.
        if CITATION_LINE_RE.search(masked):
            masked, q = re.subn(r'"[^"\n]{1,200}"', lambda m: " " * len(m.group(0)), masked)
            counts["quoted_spans"] += q
        out.append(masked)
    return out, counts, unclosed


def _column(prefix: str) -> int:
    """Width of an indent in columns, with CommonMark's 4-column tab stops.

    Counting characters made a tab measure 1, so a tab-indented code block
    never reached the four-column floor and was scanned as prose.
    """
    return len(prefix.expandtabs(4))


def _opens_frontmatter(lines: list[str]) -> bool:
    """A leading `---` followed by a YAML key, rather than a horizontal rule."""
    return bool(len(lines) > 1 and lines[0].strip() == "---"
                and YAML_KEY_RE.match(lines[1].strip()))


def frontmatter_end(lines: list[str]) -> int:
    """Index of the closing delimiter of leading frontmatter, or -1.

    Fence parsing has to skip it. A fence-shaped line inside a YAML block
    scalar is not an opener, and pairing it with a real block's opener made one
    span cover the whole document: the real code went unmasked, the headings
    row went inert, and the only cleanse the scan asked for was the one the
    gate forbade, so the document could never reach CLEAN.
    """
    if not _opens_frontmatter(lines):
        return -1
    for i in range(1, min(len(lines), FRONTMATTER_SCAN_LIMIT)):
        if lines[i].strip() in ("---", "..."):
            return i
    return -1


def fence_start(lines: list[str]) -> int:
    """The index fence parsing must begin at, skipping frontmatter.

    Shared, because computing it in one caller and not the other is how the
    YAML-block-scalar bug survived a round: the scan skipped the header and
    the gate did not, so the gate still paired a block scalar's fence with a
    real block's opener and swallowed every heading.
    """
    end = frontmatter_end(lines)
    if end >= 0:
        return end + 1
    if _opens_frontmatter(lines):
        # Unterminated: the header ends at the first line that cannot be inside
        # one. A blank line, a column-0 fence (a YAML block scalar's fence is
        # always indented), or an ATX heading all end it — stopping only at the
        # blank line skipped a real fenced block that preceded it.
        for i, ln in enumerate(lines):
            if not i:
                continue
            if (not ln.strip()) or FENCE_RE.match(ln) and not ln[:1].isspace() \
                    or ln.startswith("#"):
                return i
        return len(lines)
    return 0


def _is_fence(match: "re.Match[str]", container_indent: int) -> bool:
    """A fence is indented at most three columns over its container.

    Without the cap, a four-space example opener was a fence and took the next
    real block's opener as its closer: that block's code was scanned as prose,
    the heading between them vanished, and the gate proved nothing because it
    shares this parser.
    """
    if _column(match.group(1)) - container_indent > MAX_FENCE_INDENT:
        return False
    # CommonMark: a backtick fence's info string may not contain a backtick,
    # because that would be ambiguous with an inline code span. A tilde
    # fence's info string may contain anything, backticks included. Rejecting
    # both characters for both fences dropped two legal fences and leaked
    # their code into the prose stream.
    if match.group(2)[0] == "`" and "`" in match.group(3):
        return False
    return True


def container_indents(lines: list[str], literal: "frozenset[int] | set[int]" = frozenset()) -> list[int]:
    """Content column of the innermost open list item, per line.

    A fence inside a list item is legitimately indented to that item's content
    column, so the indent cap is relative to the container rather than to
    column 0.

    `literal` names lines whose content is code — a fence's interior, and
    frontmatter. They close nothing and open nothing: a `-` inside a ```diff
    block is a diff marker, and counting it as a list item held the container
    high for the rest of the document, so real indented code fell under the
    raised floor and was scanned as prose.
    """
    out, current = [], 0
    for i, line in enumerate(lines):
        if i in literal:
            out.append(current)
            continue
        item = LIST_ITEM_RE.match(line)
        if item:
            current = _column(item.group(0)[:-1])
        elif line.strip() and not line.startswith((" ", "\t")):
            # A column-0 line — a fence opener included — ends any open list.
            current = 0
        out.append(current)
    return out


def literal_lines(lines: list[str], spans: dict, unclosed: list, front_end: int) -> set[int]:
    """Line indices whose content is code rather than markdown structure."""
    out: set[int] = set()
    for open_i, close_i in spans.items():
        out.update(range(open_i + 1, close_i))
    for open_i in unclosed:
        out.update(range(open_i + 1, len(lines)))
    if front_end >= 0:
        out.update(range(0, front_end + 1))
    return out


def _container_indents(lines: list[str]) -> list[int]:
    """Best-effort containers for `_is_fence`, before spans are known.

    The circularity is real — spans need a container and the container needs
    spans — but an elevated value here only loosens the three-column cap, so
    it can never reject a fence that is genuinely one.
    """
    return container_indents(lines)


def fence_spans(lines: list[str], start: int = 0) -> tuple[dict[int, int], list[int]]:
    """({opening index: closing index} for every closed fence, [unclosed openers]).

    Every closed span is kept. Discarding them all when one opener never found
    a closer left already-closed code exposed to every rule, and a draft ending
    mid-fence is the common case rather than the exotic one. An unclosed opener
    and everything after it stay unmasked, and its index comes back so a caller
    comparing blocks can still compare that tail.
    """
    spans: dict[int, int] = {}
    unclosed: list[int] = []
    container = _container_indents(lines)
    i = max(start, 0)
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if not m or not _is_fence(m, container[i]):
            i += 1
            continue
        marker = m.group(2)
        char, width = marker[0], len(marker)
        for j in range(i + 1, len(lines)):
            c = FENCE_RE.match(lines[j])
            # The closer matches the opener's character, is at least as long,
            # and carries no info string — CommonMark forbids one on a closer,
            # and accepting it let ```python close a plain ``` block.
            if (c and _is_fence(c, container[j]) and c.group(2)[0] == char
                    and len(c.group(2)) >= width and not c.group(3).strip()):
                spans[i] = j
                i = j + 1
                break
        else:
            # This opener never closes. Leave it and its tail scannable, keep
            # every span already found, and move past the opener only.
            unclosed.append(i)
            i += 1
    return spans, unclosed


SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# A comma every few words is a list, not a sentence: a keyword line or an
# enumeration measured 57 "words" and was reported as an unreadable sentence.
LIST_COMMA_RATIO = 0.25


def _is_list(fragment: str) -> bool:
    n = len(fragment.split())
    return n > 0 and fragment.count(",") / n >= LIST_COMMA_RATIO


def sentences(line: str):
    """Yield each sentence-ish span on one line.

    A markdown table row is split on its cell separators first: the row is not
    a sentence, and measuring it whole reported every wide table as one. An
    escaped pipe stays inside its cell.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        segments = [c.replace("\x00", "\\|")
                    for c in stripped.strip("|").replace("\\|", "\x00").split("|")]
    else:
        segments = [line]
    for segment in segments:
        for part in SENTENCE_END.split(segment):
            if part.strip() and not _is_list(part):
                yield part


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _phrase_rules():
    """(compiled pattern, category, decides, fix) for every phrase list."""
    rules = []
    for w in EXCESS_VOCAB:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "E", True, "plain word or delete"))
    for w in PUFFERY:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "P", True, "state the concrete fact"))
    for w in TRANSITION_INITIAL:
        rules.append((re.compile(r"(?:^|(?<=[.!?]\s))\s*" + _lit(w) + r"\s*,?", re.IGNORECASE),
                      "T", True, "delete; make the point directly"))
    for w in TRANSITION_ANY:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "T", True, "delete; make the point directly"))
    for w in SYCOPHANCY:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "S", True, "delete; answer the substance"))
    for w in FALSE_CANDOR:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "S", True, "delete the framing, keep the claim"))
    for w in FAUX_STAKES:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "S", True, "name why it is decisive"))
    for w in TRAILING_OFFER:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "U", False, "delete if it offers unasked work"))
    for w in VOLUNTEERED_SCOPE:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "U", False, "keep the change, cut the framing"))
    for w in WORTHINESS:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "N", True, "delete the frame, keep the claim"))
    for w in PREAMBLE:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "N", True, "delete; state the content"))
    for w, plain in FANCY_WORDS.items():
        rules.append((re.compile(_lit(w), re.IGNORECASE), "C", True, f'use "{plain}"'))
    for w in REDUNDANT_PAIRS:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "C", True, "one word does the job"))
    for w in EMPTY_INTENSIFIERS:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "C", True, "delete; it carries no information"))
    for w in OVERSTATED:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "O", False, "state the observable fact"))
    for w in MELODRAMA:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "M", False, "name what actually happened"))
    for w in RESIDUE:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "R", False, "state the end state"))
    for pattern, label in ANTITHESIS:
        rules.append((re.compile(pattern, re.IGNORECASE), "A", False,
                      f"state the claim positively ({label})"))
    for w in ["challenges and future directions", "despite its"]:
        rules.append((re.compile(_lit(w), re.IGNORECASE), "B", False, "cut, or replace with specifics"))
    rules.append((THAT_CLAUSE_SUBJECT, "D", False, "rephrase so the subject is concrete"))
    rules.append((PARTICIPIAL_TAIL, "D", False, "delete, or state the consequence"))
    rules.append((NOMINALIZATION, "D", False, "restore the verb"))
    rules.append((PASSIVE, "C", False, "use the active form"))
    return rules


PHRASE_RULES = _phrase_rules()


def _flush_em_dashes(hits: list, block: list[str], line_no: int) -> None:
    n = sum(p.count("—") for p in block)
    if n > EM_DASHES_PER_PARAGRAPH:
        hits.append({"line": line_no, "category": "F", "category_name": CATEGORY_NAMES["F"],
                     "match": f"{n} em dashes in one paragraph", "decides": True,
                     "fix": "normalize most to commas or periods"})


def scan(text: str) -> dict:
    lines, skipped, unclosed = mask(text)
    raw = text.split("\n")
    hits: list[dict] = []

    # Read over the masked body, so a citation inside a code block does not
    # count. One document-level fact, computed once.
    cites_own_sections = any(
        SECTION_CITATION_RE.search(l) for l, r in zip(lines, raw)
        if not NUMBERED_HEADING_RE.match(r)
    )

    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        spans: list[tuple[int, int, str, bool, str, str]] = []
        for pattern, cat, decides, fix in PHRASE_RULES:
            for m in pattern.finditer(line):
                if m.group(0).strip():
                    spans.append((m.start(), m.end(), cat, decides, fix, m.group(0).strip()))
        # Precedence: one label per phrase, the earliest category wins.
        spans.sort(key=lambda s: (s[0], RANK[s[2]], -(s[1] - s[0])))
        taken: list[tuple[int, int]] = []
        for start, end, cat, decides, fix, matched in spans:
            if any(start < t_end and end > t_start for t_start, t_end in taken):
                continue
            taken.append((start, end))
            hit = {"line": i, "category": cat, "category_name": CATEGORY_NAMES[cat],
                   "match": matched, "decides": decides, "fix": fix}
            if not decides:
                hit["context"] = raw[i - 1].strip()[:160]
            hits.append(hit)

        # C — sentence length. Measured, so it decides.
        for sentence in sentences(line):
            n = len(sentence.split())
            if n > LONG_SENTENCE_WORDS:
                hits.append({"line": i, "category": "C", "category_name": CATEGORY_NAMES["C"],
                             "match": f"sentence of {n} words", "decides": True,
                             "fix": "split it, or cut a clause"})

        # N — two-topic heading. It spans the whole line, so it sits outside the
        # one-label-per-phrase pass, where it would hide every word hit inside it.
        # A line already carrying an N hit is not counted twice. The match is
        # the raw line, since masking blanks inline code.
        if TWO_TOPIC_HEADING.match(line) and not any(
                h["line"] == i and h["category"] == "N" for h in hits):
            hits.append({"line": i, "category": "N", "category_name": CATEGORY_NAMES["N"],
                         "match": raw[i - 1].strip(), "decides": False,
                         "context": raw[i - 1].strip()[:160],
                         "fix": 'use a plain label, or state the claim itself '
                                '(two-topic heading, like "What it does, and why it matters")'})

        # N — counted lead-in. Outside the phrase pass for the same reason, so
        # a long match cannot hide a word hit or demote a decided preamble.
        m = COUNTED_LEAD_IN.search(line)
        if m and not any(h["line"] == i and h["category"] == "N" for h in hits):
            hits.append({"line": i, "category": "N", "category_name": CATEGORY_NAMES["N"],
                         "match": m.group("lead").strip(), "decides": False,
                         "context": raw[i - 1].strip()[:160],
                         "fix": 'delete the count and lead with the list '
                                '(counted lead-in, like "Two examples:")'})

        # F — formatting tells.
        if any(ch in line for ch in CURLY):
            hits.append({"line": i, "category": "F", "category_name": CATEGORY_NAMES["F"],
                         "match": "curly quotes", "decides": True, "fix": "straighten to \" and '"})
        if NUMBERED_HEADING_RE.match(raw[i - 1]) and not cites_own_sections:
            hits.append({"line": i, "category": "F", "category_name": CATEGORY_NAMES["F"],
                         "match": "numbered heading", "decides": False,
                         "context": raw[i - 1].strip()[:160],
                         "fix": "drop the outline number, unless the numbering is structural"})
        if re.match(r"^\s*#{1,6}\s*[" + HYPE_EMOJI + r"]", raw[i - 1]) or \
                re.match(r"^\s*[" + HYPE_EMOJI + r"]", raw[i - 1]):
            hits.append({"line": i, "category": "F", "category_name": CATEGORY_NAMES["F"],
                         "match": "hype emoji opener", "decides": True, "fix": "drop it"})

    # F — em-dash overuse is a paragraph property, so it is counted per paragraph.
    para_start, para = 1, []
    for i, line in enumerate(lines + [""], start=1):
        if line.strip():
            # Each list item counts on its own; a reference list carrying one
            # em dash per citation is not one paragraph of em-dash overuse.
            if re.match(r"^\s*([-*+]|\d+[.)])\s", line) or line.strip().startswith("|"):
                if para:
                    _flush_em_dashes(hits, para, para_start)
                    para = []
                _flush_em_dashes(hits, [line], i)
                continue
            if not para:
                para_start = i
            para.append(line)
            continue
        if para:
            _flush_em_dashes(hits, para, para_start)
            para = []

    # The gate compares the unclosed tail byte-for-byte, so a hit inside it is
    # one the loop must report and never apply — otherwise every truncated
    # draft reverts, its chunked retry fails the same way, and it ends NOT CLEAN.
    if unclosed:
        frozen_from = min(unclosed) + 1
        for h in hits:
            if h["line"] >= frozen_from:
                h["in_unterminated_region"] = True
                h["decides"] = False
                h["fix"] = "report only; the gate compares this region byte-for-byte"

    hits.sort(key=lambda h: (h["line"], RANK[h["category"]]))
    # A frozen hit cannot be fixed, so counting it would make the loop repeat
    # the same phrase every pass and stop on "flagged twice without changing".
    # It is reported, and excluded from the numbers the loop records.
    live = [h for h in hits if not h.get("in_unterminated_region")]
    counts: dict[str, int] = {}
    for h in live:
        counts[h["category"]] = counts.get(h["category"], 0) + 1
    return {
        "hits": hits,
        "counts": counts,
        "frozen": len(hits) - len(live),
        "decided": sum(1 for h in live if h["decides"]),
        "candidates": sum(1 for h in live if not h["decides"]),
        "skipped": skipped,
        "judgment_categories": JUDGMENT_ONLY,
    }


def _unterminated_warning(skipped: dict) -> str:
    """Named in both branches: a hit from inside an unmasked fence is exactly
    the case where silence is dangerous."""
    which = [n for n, k in (("frontmatter", "unterminated_frontmatter"),
                            ("fence", "unterminated_fence")) if skipped.get(k)]
    if not which:
        return ""
    return (f" WARNING: unterminated {' and '.join(which)}; "
            "those lines were scanned rather than masked.")


def render(result: dict) -> str:
    """The `Categories` line `no-ai-slop` Step 2 opens with, from the scan alone."""
    warn = _unterminated_warning(result["skipped"])
    if not result["counts"]:
        masked = sum(v for v in result["skipped"].values()
                     if isinstance(v, int) and not isinstance(v, bool))
        frozen = result.get("frozen", 0)
        note = (f" {frozen} hit{'s' if frozen != 1 else ''} sit inside an unclosed "
                "region and are report-only." if frozen else "")
        return (f"scan: no fixable hits ({masked} spans/lines masked). "
                f"The judgment categories still need reading.{note}{warn}")
    ordered = sorted(result["counts"].items(), key=lambda kv: (-kv[1], RANK[kv[0]]))
    line = ", ".join(f"{CATEGORY_NAMES[c]} ×{n}" for c, n in ordered)
    out = [f"Categories: {line}"]
    if warn:
        out.append(warn.strip())
    out.append("")
    for h in result["hits"]:
        mark = "" if h["decides"] else "  [confirm]"
        out.append(f"- line {h['line']}: \"{h['match']}\" — {h['category_name']} "
                   f"({h['category']}) → {h['fix']}{mark}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=None, help="file to scan; omit to read stdin")
    ap.add_argument("--report", action="store_true", help="human-readable lines instead of JSON")
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args(argv)

    text = Path(args.file).read_text(errors="replace") if args.file else sys.stdin.read()
    result = scan(text)
    if args.report:
        print(render(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


def main_guarded(argv: list[str]) -> int:
    """Always emit JSON; the composing skill parses this output."""
    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main_guarded(sys.argv[1:]))
