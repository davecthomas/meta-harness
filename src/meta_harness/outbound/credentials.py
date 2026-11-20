"""Credential redaction: what the outbound pipeline's redact stage takes out of a text, and whether it runs.

It is off unless the task policy says `credential_redaction: true`. On, it replaces credentials with
`[redacted]` in every text the control plane posts for people: access tokens, API keys, passwords inside a
URL, private key blocks, and the secret values this service itself holds. It looks for nothing else. It does
not look for personal data (names, email addresses, phone numbers, addresses): that is not its job, and the
HTTP Gateway has its own setting for it.

Two kinds of match:

    by shape   SHAPES below: each issuer gives its tokens a prefix and a length that ordinary text does not
               have. A shape here is specific enough to leave prose and code alone.
    by value   the values of this process's own secrets (`own_values`): an environment variable whose name
               ends in KEY, TOKEN, SECRET or PASSWORD, and the password inside any variable that is a URL.
               A secret with no shape, such as a random service key, is caught this way.

A text with a credential in it was written from a run, a log or an error that held one. So each redaction is
logged as a warning naming what kind was found, never the value: the credential itself needs replacing.
"""

import logging
import os
import re
from collections import Counter

from meta_harness.config import store as config_store

log = logging.getLogger(__name__)

SETTING = "credential_redaction"
REDACTED = "[redacted]"
OWN = "one of this service's own secrets"
# Each credential found by its shape: what to call it in a record, and the pattern. In order: a longer shape
# that holds a shorter one comes first.
SHAPES: tuple[tuple[str, str], ...] = (
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("Slack webhook URL", r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    ("user name and password in a URL", r"(?<=://)[^\s/:@]+:(?!\[redacted\]@)[^\s/@]+(?=@)"),
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("GitHub token", r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    ("Anthropic API key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("OpenAI API key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    ("AWS access key ID", r"\bAKIA[0-9A-Z]{16}\b"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("Google OAuth token", r"\bya29\.[A-Za-z0-9._-]{20,}"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    ("Atlassian API token", r"\bATATT3[A-Za-z0-9_=-]{20,}\b"),
    ("npm token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("PyPI token", r"\bpypi-[A-Za-z0-9_-]{50,}"),
    ("JSON Web Token", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("Basic authorization header", r"\b[Aa]uthorization:\s*[Bb]asic\s+[A-Za-z0-9+/]{8,}={0,2}"),
    ("bearer token", r"\b(?:[Aa]uthorization:\s*)?[Bb]earer\s+[A-Za-z0-9._~+/=-]{24,}"),
)
_COMPILED = tuple((name, re.compile(pattern)) for name, pattern in SHAPES)
# An environment variable that holds a secret, by its name's ending.
SECRET_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)$", re.IGNORECASE)
URL_PASSWORD = re.compile(r"://[^\s/:@]+:([^\s/@]+)@")
# The shortest value redacted by value: a shorter one is a word that ordinary text may hold.
MIN_OWN_CHARS = 12

# The switch as it was last read, for when the task policy cannot be read. Off until it has been read once.
_last = False


def enabled() -> bool:
    """Whether credentials are redacted: only when the task policy says `credential_redaction: true`. A
    policy that cannot be read answers what the last read said, so a failed read neither turns redaction on
    for a deployment that left it off nor lets a credential out of one that turned it on."""
    global _last  # noqa: PLW0603 - one remembered switch per process
    try:
        _last = config_store.current_document("task_policy").get(SETTING) is True
    except Exception as err:  # noqa: BLE001 - an unreadable policy must not fail a post
        log.warning("the task policy could not be read (%s): %s stays %s", err, SETTING, "on" if _last else "off")
    return _last


def own_values() -> list[str]:
    """The secret values this process holds, longest first: each environment variable named as a secret, and
    the password in each one that is a URL. A path to a file is not a secret, and neither is a short value."""
    found: set[str] = set()
    for name, value in os.environ.items():
        found.update(URL_PASSWORD.findall(value))
        if SECRET_NAME.search(name) and not value.startswith(("/", "~", ".")):
            found.add(value.strip())
    return sorted((value for value in found if len(value) >= MIN_OWN_CHARS), key=len, reverse=True)


def redact(text: str) -> tuple[str, Counter]:
    """The text with each credential replaced by [redacted], and how many of each kind were found."""
    found: Counter = Counter()
    for value in own_values():
        if value in text:
            found[OWN] += text.count(value)
            text = text.replace(value, REDACTED)
    for name, pattern in _COMPILED:
        text, count = pattern.subn(REDACTED, text)
        if count:
            found[name] += count
    return text, found
