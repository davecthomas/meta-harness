-- A goal may hold issues an operator picked by ref, with no label at all.
-- Only relaxes the column: code from before this always sets a label.

ALTER TABLE goal ALTER COLUMN label DROP NOT NULL;
