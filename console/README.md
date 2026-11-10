# The operator console

The React and TypeScript web app operators run the meta-harness from. It
calls only the REST API, through the edge (`src/meta_harness/edge/app.py`).

- Build: `npm ci`, then `npm run build`. Test: `npm test` and `npm run typecheck`.
- The app runs on a data router (`createBrowserRouter` in `src/main.tsx`), so
  an editor can hold a navigation while it asks about unsaved changes.

## Where code goes

Each feature has one folder, `src/features/<feature>/`: its pages,
components, tests, its REST calls and types (`api.ts`), and an `index.tsx`
that exports its `feature`, the routes and navigation entries it adds. So two
tasks on different features edit different files.

- A new feature is a new folder plus one line in `src/features/index.ts`.
- A task touches `src/App.tsx`, `src/api.ts` or `src/features/index.ts` only
  to register a feature, not to add its code there.
- `src/api.ts` holds what every feature shares (`Goal`, `Task`, events) and
  spreads each feature's calls into `api`; `src/client.ts` makes the requests.
- `src/components/` holds only what several features use: the command
  palette, the inbox, the terminal, shared UI pieces and `UnsavedChanges`.

## Editors

Every editor, a form whose work is lost when the operator leaves it, uses
`useUnsavedChanges` (`src/components/UnsavedChanges.tsx`). It passes whether
it is dirty, a line naming what is unsaved, its own save and discard, and
renders the hook's `element` once. Leaving a dirty editor asks to save,
discard or keep editing: through links, the back button and URL changes, and,
with the browser's own prompt, closing or reloading the tab. A leave the
router doesn't see, such as an editor's Cancel button, goes through
`confirm`, and a successful save calls `release` before it navigates.

Not editors: one-line inputs that act at once, such as the kill switch's
reason, and toggles that save as they change.
