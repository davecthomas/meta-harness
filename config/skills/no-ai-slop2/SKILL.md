---
name: no-ai-slop2
description: "Check the text you are about to write for people to read (a pull request summary, a review summary or finding, an issue draft, a comment) for AI slop language patterns, and write the cleansed text in its place. A script beside this file, no-ai-slop-scan.py, decides the mechanical half: excess vocabulary, puffery, transition crutches, sycophancy and false candor, worthiness framing, trailing offers, fancy words, redundant pairs, empty intensifiers, sentences over 25 words, em-dash density, curly quotes, numbered headings and emoji openers, with code, quotes and frontmatter masked. You judge the rest: antithesis, inverted claims, hollow enumeration, bloat, metaphor and overstated opinion. Never alters code, quoted text, links or numbers."
---

# No AI Slop (2)

## Keywords

no-ai-slop2, ai slop 2, slop scan, de-slop, deslop, cleanse response, sounds like ai, reads like chatgpt, too wordy, corporate speak, contrast slop, this not that, antithesis, inverted claim, hollow enumeration, rule of three, puffery, excess vocabulary, em dash overuse, curly quotes, sycophancy, false candor, worthiness framing, trailing offer, conversation residue, two-topic heading, "what it does and why it matters", "what each part does", counted lead-in, "two examples:", "three reasons", metaphor, overstated opinion, no-ai-slop-scan.py, deterministic slop scan

## When to Use This Skill

Use it on the text you are about to write, before it leaves you, whenever a person will read it:

- the summary of your work that ends a run, which becomes the pull request's description;
- a review's summary and each finding, before you call `submit_review`;
- an issue draft's title and body, before you call `create_issue`;
- any comment or message meant for an operator.

Write your draft first. Then run this skill on the draft, and write the cleansed text as your actual output in its place. The target is always that draft, the content you are about to produce next, never something you said earlier.

Leave alone what is not prose for people: code, diffs, commands, logs, check output, and quoted text.

---

## The script decides the mechanical half

```bash
# The script sits next to this file. Put your draft in a file outside the repository, then:
python3 <this skill's directory>/no-ai-slop-scan.py --file /tmp/draft.md   # or pipe the draft on stdin
```

This skill's directory is the one this SKILL.md was read from. `--report` prints the findings as lines instead of JSON. Never write the draft inside the repository: it would land in the pull request.

| It decides | Rule it encodes |
|---|---|
| `decides: true` hits | The match **is** the finding; report it as given. Covers E, P, T, S, N, the C word lists and sentence length, and the F formatting tells except numbered headings |
| `decides: false` candidates | A trigger to confirm with the category's own test: A, M, R, U, D, the N two-topic heading and counted lead-in, the C passive-voice heuristic, the B templates, the O absolutes, and F numbered headings |
| Precedence | One label per phrase, earliest category wins: A, I, H, B, N, D, O, P, M, V, R, S, T, U, F, C, E. Structural and functional tells rank first, formatting next, the bare word lists last, so a meaningful tell always beats a word match |
| What is never scanned | Fenced blocks of any width, inline code spans, blockquote lines, closed YAML frontmatter, a cited title, and indented code. Indent is measured in columns: four outside a list, the open item's content column plus four inside one, so a list continuation stays prose |
| Unterminated delimiters | An unclosed fence or frontmatter is scanned, never masked, and flagged in `skipped`. Swallowing the rest of a document reads identically to a clean scan |
| `skipped` | How much was masked, so a suspiciously empty scan is explainable |
| `in_unterminated_region` | A hit after an unclosed fence opener. The gate compares that region byte-for-byte, so report it and never apply it: applying guarantees a revert, and the chunked retry fails the same way. Excluded from `counts`, `decided` and `candidates`, and totalled in `frozen`, so a loop recording those numbers does not repeat an unfixable phrase every pass |

**Never restate these lists in prose, and never re-derive a decided hit.** 

**Overuse and clustering are the signal.** Every word the script matches appears in good human writing. A decided hit says the pattern is present. The rewrite still asks whether the word is doing puffery work or piling up.

Two keep-cases survive the decided tier. An N sentence carrying what the reader needs first — an ordering rule, a scope boundary — stays. An S acknowledgment that is brief, specific and tied to a correction stays. Record either as dropped, with its reason.

## What the skill still judges

The scan cannot read a sentence. These categories need it, and stay here in full.

### A — antithesis, confirmation only

The scan flags `not just`, `not only`, `rather than`, `instead of`, `more than just`, `isn't just`, `it's not about`, `gone are the days`, `X, not Y`, `X — not Y`. Each has a legitimate use, so confirm before reporting.

It is antithesis when the negated half carries no information — a contrast correcting a misconception nobody had. State the claim positively and delete the foil.

"not just fast, but reliable" → "fast and reliable". "wraps rather than truncating" → "wraps to the next row".

It is not antithesis when both halves are real and distinct. Nor when the sentence rules a real alternative out for the reader: "use the remote ref, instead of the local branch, whose tip may be behind".

### I — inverted claim

One claim split across a main clause and a demoted tail. Every word is clean English; the defect is the split, which is why no pattern finds it.

**The test: could one phrase carry this?** Flag when the sentence asserts one relationship, not two events, and the tail is dependent. Dependent means it cannot stand alone, or it restates what the main clause implies.

Then branch on the tail. One carrying a fact is a `demoted actor`: promote it to the main verb. One restating the clause is a `derived coda`: delete it.

- Coordinated clause: "A job step states what it produces, and the platform holds it to that."
  → "The platform holds each job step to the contract it declares."
- Anaphoric cleft (`, so that's`, `, and that's`, `, which is why`):
  "The rewritten FAQ is new text, so that's where the hits are." → "The rewritten FAQ is new text."
- Relative clause with a smuggling `the`: "Setup generates the secrets nobody can fetch." → "Nobody can fetch the secrets setup generates."
- Participial adjunct: "A finished job answers with what it produced, labeled by step." → "A finished job labels its output by step."

Leave alone: two real events; a compound condition whose halves each stand alone; a serial comma; a restrictive relative that identifies which.

### H — hollow enumeration

A general claim trailed by three example items that name instances of the claim.

Flag when all three hold. The list runs to about three items hung off a general claim, often after a colon, `such as`, `like`, or `including`. No item constrains the claim. The set is open.

"Three refusals answer with a friendly message: an unknown command, an unknown caller, and a caller we do not permit."

→ "Refusals answer with a friendly message."

Keep the list when it is the contract — a closed set the reader acts on, or items carrying detail the claim omits. When the set is closed, say so.

### B — bloat

- **Tricolon padding**: three modifiers stacked for rhythm ("fast, simple, and powerful"). Keep the ones carrying meaning. Three enumerated instances go to H, which outranks this.
- **Container-restating tautology**: a lead-in re-asserting what its own heading, label, or column already says — "Decisions — these are settled", "Risks: these could go wrong". Delete it, or replace it with the constraint the label does not imply.
- **Self-count**: a count of the text's own parts or of the work behind it — "This document has fifteen interactive diagrams", "I reviewed 42 files". The reader gains nothing from the number. Delete it, or state the fact about the topic the count stood in for. A count stays when the reader acts on it ("3 tests fail") or when the quantity is the subject. A count of the list that follows is N's counted lead-in.
- **Section-summary restatement**: a closing sentence re-saying the paragraph. Delete.
- **Over-sell fragment**: a short emphatic line restating the previous sentence — "No fluff.", "It just works." Delete.
- **Hedge filler**: "designed to", "aims to", "can help to". State what it does.
- **Audience padding**: "whether you're a beginner or a pro". Name the audience once.
- **Two-beat rhythm pairs**: "announce, then orient" as an ornamental label. Use one plain label.

### V — metaphor that obscures the plain fact

Could a reader restate the sentence's fact without the figure, and would it read clearer? If yes, replace the figure with the fact: "move the needle on retention" → "raise retention"; "a different beast" → name the difference. Flag every abstract use of `shape` and name the property meant — structure, scope, plan, schedule.

A figure stays when it clears any one of three passes. Merited by context: it carries precision the literal phrasing loses. An established term of art: "attack surface", "cache stampede", `spawn` a worker. A dead metaphor with no plainer form: "run the script".

### O — overstated opinion, confirmation only

The scan flags absolutes (`obviously`, `by far`, `clearly the best`). Confirm that the certainty outruns the support, then state the observable fact instead. Also judge, unaided: unsupported disparagement ("a clunky, dated approach") and verdict adjectives presented as properties (`elegant`, `ugly`, `boring`). A supported, proportionate opinion stays.

### M, R, U, D — confirmation tests

- **M melodrama**: sweep versus support. A figure adding precision or carrying the argument stays; the tell is colour standing in for a nameable fact. `saga` for three incidents is a hit; "cache stampede" is the precise term.
- **R residue**: does the phrase point at an exchange the reader never saw? A changelog or migration note, where the prior state is the subject, is exempt.
- **U unrequested effort**: a closing question stays when finishing the task needs the user's input; a fishing offer does not. For volunteered scope, keep the change and cut the announcement framing.
- **D density**: a participial tail adding tone rather than content is a hit; one carrying a consequence stays. A nominalization stays where the noun is the subject under discussion.
- **N two-topic heading**: a heading or lead-in made of two topic clauses joined by "and" — "What each part does, and the numbers it has to hit", "How it works and why it matters". It names what is coming and states none of it. Replace it with a plain label ("Components and targets") or with the claim itself. It stays when a template the reader expects fixes the heading's wording, or when a checklist item names content the reader must produce ("What the PM landed on and why").
- **N counted lead-in**: a count announcing the list that follows — "Two examples:", "Three reasons why it fails:", "Here are four ways to fix it". The reader can see how many items there are, so the count adds nothing. Delete it and lead with the list, or name what the items share ("It fails when:"). It stays when the reader acts on the number ("3 tests fail:").
- **C jargon**: no pattern finds it. Judge whether a domain term is one this audience needs, and gloss it in plain words when it is not.
- **F, unaided**: a parenthetical heading, whole-sentence bold, and every-bullet-bold lists are yours to judge. The script measures em dashes, curly quotes and emoji openers, and offers numbered headings as a candidate.
- **F numbered headings**: confirm before dropping the number. Numbers are structural when the numbering is deep (`2.3.1`), or when the document type conventionally numbers one — a spec, contract, RFC, standard, or paper. They are decoration when added for visual structure in prose that never refers back. The scan emits nothing at all when the body cites its own sections (`Section 2.1`, `§3.4`, `clause 4.2`): the numbers are load-bearing there, and stripping one breaks every reference to it.

### The rewrite

Always the skill's. The scan names what to change and never writes a replacement.

## Execution checklist

1. **Run the scan** on the target text. `--report` for reading, JSON when composing.
2. **Confirm the candidates.** Each `decides: false` hit is kept or dropped by its category's test above. Dropping one is normal and needs no note.
3. **Read for the judgment categories** — I, H, B, V, the unaided parts of O and F, and C-jargon. The scan is silent on all of them, so a scan with no hits does not mean clean text.
4. **Report** (Step 2 below), then **rewrite** (Step 3 below).

## Step 2: Find what to fix

These findings are for your own use: they never go into the pull request, review, issue or comment. Note a `Categories` line naming each category that fired and its count, most-frequent first, using the category names rather than the bare letters. Then one line per hit: the exact phrase in quotes, the category name with its letter, and a one-line positive rewrite.

```
Categories: false candor / sycophancy ×3, antithesis ×2, inverted claim ×2, excess vocabulary ×1

Slop found:
- "Color, not clutter" — antithesis (A: X, not Y) → "Color-coded fields"
- "A job step states what it produces, and the platform holds it to that" — inverted claim (I: demoted actor) → "The platform holds each job step to the contract it declares"
- "delve into the details" — excess vocabulary (E) → "cover the details"
- "you're absolutely right" — sycophancy / false candor (S) → delete; address the correction directly
```

Nothing found, and the judgment categories read clean: write the draft as it is.

## Step 3: Write the cleansed text

Write the **entire** draft with every flagged item fixed, as the content itself: the summary, the finding, the issue body. No heading of its own, and no list of what changed.

- State each claim positively; delete the negated foil.
- Change only the slop. Preserve every fact, number, name, link, emoji, list, heading, and code or quoted block verbatim.
- Add no claims, sections, or commentary, and do not summarize — output the full text so it can be used directly.
- Keep the author's voice and length. Favor verbs and specifics over abstract, noun-heavy phrasing.

## Saying it ran

End your run's final message with one line saying the pass ran on the text you wrote, after that text and never inside it: `no-ai-slop2: 5 hits (scan 3, judged 2)`, or `no-ai-slop2: clean`.

## Hard rules

- **The scan runs first, every time.** It is cheap. A judgment pass starting from its output is a smaller job than one starting from raw text.
- **A decided hit is not re-litigated.** Confirm candidates; never re-judge what the script decided.
- **A clean scan is not clean text.** I, H, B, V, C-jargon and parts of O and F are invisible to it. Read the report's masked counts before believing a zero.
- **Never alter code, inline code, or quoted text.** The scan masks them; the rewrite must respect the same boundary.
- **Preserve meaning.** Cleansing removes hollow framing; it never drops a claim, number, name, or caveat.

## References

- Reinhart et al., "Do LLMs write like humans? Variation in grammatical and rhetorical styles," *PNAS* 122(8) (2025) — [doi:10.1073/pnas.2422455122](https://www.pnas.org/doi/10.1073/pnas.2422455122)
- Kobak et al., "Delving into LLM-assisted writing in biomedical publications through excess vocabulary," *Science Advances* (2025) — [arXiv:2406.07016](https://arxiv.org/abs/2406.07016)
- Liang et al., "Monitoring AI-Modified Content at Scale," *ICML* (2024) — [arXiv:2403.07183](https://arxiv.org/abs/2403.07183)
- Berber Sardinha, "AI-generated vs human-authored texts: A multidimensional comparison," *Applied Corpus Linguistics* (2024) — [doi:10.1016/j.acorp.2023.100083](https://doi.org/10.1016/j.acorp.2023.100083)

The lexical and grammatical tells (E, D) are measured in the papers above. The structural and functional tells (A, I, H, N, O, M, V, R, S, U, F) are widely observed in practitioner writing; treat them as heuristics.
