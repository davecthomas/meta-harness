-- Created by the meta-harness in run t-45c4a1fc56e9:run-1 on harness:pi-careful/claude:claude-opus-5-5.

-- The Fix with AI draft an operator accepted while the issue's task was running (planner/issue_fix.py):
-- {"parts": [{"part", "text"}], "by", "kept_at"}. The issue was left as it was; Fix with AI offers this draft
-- again once the task ends. Null: none kept. An accept that writes the issue clears it.
ALTER TABLE issue_link ADD COLUMN fix_kept jsonb;
