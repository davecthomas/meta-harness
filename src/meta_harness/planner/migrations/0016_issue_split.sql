-- The issues an operator split a held issue into (sizing_api.py), null until it is split. A split issue stays
-- held, saying what it was split into, across sweeps, and is not split again. The empty list claims the issue
-- while its smaller issues are being created.
ALTER TABLE issue_link ADD COLUMN split_into text[];
