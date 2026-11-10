"""The services' logging: a record's level reaches the journal, so Cloud Logging files it by severity."""

import logging

from meta_harness import logs


def _record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord("meta_harness.tasks", level, __file__, 1, message, None, None)


def test_under_systemd_each_line_carries_its_journal_priority():
    formatter = logs.JournalFormatter("[tasks] %(message)s", journal=True)
    assert formatter.format(_record(logging.INFO, "started")) == "<6>[tasks] started"
    assert formatter.format(_record(logging.WARNING, "task t-1 is review_unresolved")) == (
        "<4>[tasks] task t-1 is review_unresolved"
    )
    assert formatter.format(_record(logging.ERROR, "task t-1 failed")) == "<3>[tasks] task t-1 failed"
    assert formatter.format(_record(logging.CRITICAL, "down")).startswith("<2>")
    assert formatter.format(_record(logging.DEBUG, "detail")).startswith("<7>")


def test_every_line_of_a_message_gets_the_prefix_since_the_journal_reads_lines():
    formatter = logs.JournalFormatter(journal=True)
    assert formatter.format(_record(logging.ERROR, "failed\nTraceback")) == "<3>failed\n<3>Traceback"


def test_outside_systemd_lines_are_plain(monkeypatch):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    assert logs.JournalFormatter().format(_record(logging.ERROR, "task t-1 failed")) == "task t-1 failed"
    monkeypatch.setenv("JOURNAL_STREAM", "8:1234")
    assert logs.JournalFormatter().format(_record(logging.ERROR, "task t-1 failed")) == "<3>task t-1 failed"


def test_configure_sends_info_and_above_through_the_formatter(capsys, monkeypatch):
    monkeypatch.setenv("JOURNAL_STREAM", "8:1234")
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        logs.configure("[tasks] %(message)s")
        logging.getLogger("meta_harness.tasks").warning("task t-1 is canceled")
        logging.getLogger("meta_harness.tasks").debug("not shown")
        assert capsys.readouterr().out == "<4>[tasks] task t-1 is canceled\n"
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
