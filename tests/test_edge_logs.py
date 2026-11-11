"""No row of the Logs page is blank: every log entry says something, whatever shape it has."""

from meta_harness.edge.logs import blank_line, entry_message, entry_row


def test_a_request_cloud_run_served_reads_as_its_method_path_status_and_time():
    entry = {
        "timestamp": "2026-11-11T15:06:26Z",
        "severity": "INFO",
        "httpRequest": {
            "requestMethod": "GET",
            "requestUrl": "https://mh-edge-abc.a.run.app/v1/tasks?status=running",
            "status": 200,
            "latency": "0.035s",
        },
    }
    row = entry_row(entry)
    assert row["message"] == "GET /v1/tasks?status=running 200, 35 ms"
    assert (row["service"], row["severity"]) == ("edge", "INFO")

    failed = entry_row(
        {"severity": "INFO", "httpRequest": {"requestMethod": "POST", "requestUrl": "/v1/goals", "status": 502}}
    )
    assert failed["message"] == "POST /v1/goals 502" and failed["severity"] == "ERROR"


def test_a_services_line_is_its_text():
    journal = {"jsonPayload": {"_SYSTEMD_UNIT": "mh-tasks.service", "MESSAGE": "[tasks] task t-1 failed"}}
    assert entry_row(journal)["message"] == "[tasks] task t-1 failed"
    assert entry_row(journal)["service"] == "mh-tasks"
    assert entry_message({"textPayload": "Starting the edge"}) == "Starting the edge"
    assert entry_message({"jsonPayload": {"message": {"event": "sign_in", "who": "dave"}}}) == (
        '{"event": "sign_in", "who": "dave"}'
    )


def test_an_entry_with_no_text_of_its_own_is_described_by_what_it_records():
    audit = {"protoPayload": {"methodName": "google.cloud.run.v1.Services.ReplaceService", "status": {"message": "ok"}}}
    assert entry_message(audit) == "google.cloud.run.v1.Services.ReplaceService ok"
    structured = {"jsonPayload": {"level": "warn", "retries": 3}}
    assert entry_message(structured) == '{"level": "warn", "retries": 3}'
    bare = {"logName": "projects/p/logs/run.googleapis.com%2Fvarlog%2Fsystem", "severity": "WARNING"}
    assert entry_message(bare) == "(an entry with no text, in run.googleapis.com%2Fvarlog%2Fsystem)"
    for entry in (audit, structured, bare, {}, {"jsonPayload": {"MESSAGE": "   "}}):
        assert entry_message(entry).strip(), "no entry has an empty message"


def test_an_empty_line_a_service_printed_is_left_out():
    assert blank_line({"jsonPayload": {"_SYSTEMD_UNIT": "mh-worker.service", "MESSAGE": ""}})
    assert blank_line({"jsonPayload": {"_SYSTEMD_UNIT": "mh-worker.service", "MESSAGE": "  "}})
    assert not blank_line({"jsonPayload": {"_SYSTEMD_UNIT": "mh-worker.service", "MESSAGE": "x"}})
    assert not blank_line({"httpRequest": {"status": 200}})
