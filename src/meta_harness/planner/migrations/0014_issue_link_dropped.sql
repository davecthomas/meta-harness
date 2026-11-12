-- An issue an operator removed from its goal. The row stays, marked dropped, so the goal's sweep does not take
-- the issue in again by its label: the issue keeps its labels on the tracker, and nothing there changes. A
-- dropped issue is not one of the goal's any more: it is not listed, it does not count toward the goal being
-- met, and another goal may take it. Adding it back by ref clears the mark.

ALTER TABLE issue_link ADD COLUMN dropped boolean NOT NULL DEFAULT false;
