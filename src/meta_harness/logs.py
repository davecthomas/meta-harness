"""Logging for the services: one line per record on stdout, with its level where Cloud Logging can read it.

On the VM each service is a systemd unit, and its output reaches Cloud Logging through the journal. The
journal gives every line of a unit's output the same priority unless the line starts with a priority prefix
(`<4>` for a warning, `<3>` for an error), which systemd strips and records. Without the prefix a WARNING and
an ERROR arrive as INFO, and the console's Logs page, filtered to warnings and above, shows none of them. So
under systemd (JOURNAL_STREAM is set) each line carries its prefix; elsewhere lines are plain.
"""

import logging
import os
import sys

# The journal's priorities: debug 7, info 6, warning 4, error 3, critical 2.
PRIORITIES = ((logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6))


def priority(level: int) -> int:
    """The journal priority for a logging level."""
    return next((p for floor, p in PRIORITIES if level >= floor), 7)


class JournalFormatter(logging.Formatter):
    """Formats a record as one line, with the journal's priority prefix when the process runs under systemd.

    A message of several lines (a traceback) gets the prefix on each, since the journal reads them one by one.
    """

    def __init__(self, fmt: str = "%(message)s", journal: bool | None = None) -> None:
        super().__init__(fmt)
        self.journal = bool(os.environ.get("JOURNAL_STREAM")) if journal is None else journal

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self.journal:
            return text
        prefix = f"<{priority(record.levelno)}>"
        return "\n".join(prefix + line for line in text.splitlines())


def configure(fmt: str = "%(message)s", formatter: logging.Formatter | None = None) -> None:
    """Sends every logger's INFO and above to stdout through a JournalFormatter. A service calls it at start."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter or JournalFormatter(fmt))
    # force: an imported library may already have given the root logger a handler.
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # httpx logs every request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
