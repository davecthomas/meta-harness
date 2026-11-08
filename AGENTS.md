# Repository guidelines

The meta-harness is the control plane in `docs/technical-design.md`: it turns
goals into pull requests on its registered repos, this one included. Read the
design's component table before changing a component; it says what is built
and what is planned.

## Build and test

- Install: `uv sync`.
- Test: `uv run pytest -q`. Tests marked `db` need Postgres at
  `MH_DATABASE_URL` and skip without it. No test calls a model provider.
- Lint and format: `uv run ruff check .` and `uv run ruff format --check src tests`.
  Run `uv run ruff format src tests` to fix formatting.
- The harness image, `images/harness-pi`, has its own tests: `npm ci`, then
  `npm test` and `npm run typecheck`. They need Node 22.19 or later.
- The console, `console/`, builds with `npm ci` and `npm run build`, and
  tests with `npm test`.
- CI also runs the zero-cost end-to-end check, which builds the images and
  runs whole tasks against a fake HTTP Gateway. A change to the run service,
  the harness image or the sandbox must keep it green.

## Rules

- Never call a model provider, the real HTTP Gateway, GitHub or Google Cloud
  from a test.
- Do not run `deploy/gcp.sh` or change anything in a Google Cloud project.
  A merge to main deploys itself (`.github/workflows/deploy.yml`).
- Code names no model and no harness profile: they live in `config/`.
- Every new or changed setting is checked at service startup, so a bad one
  stops the service with its name.
- Only the control plane writes to a code host. Nothing in a per-run sandbox
  may hold a code-host credential or write where the host will run it.
- Keep `docs/technical-design.md` true to the code: a change that alters a
  documented behavior updates the design in the same pull request.
- The project has no published version, so a change bumps none.

## Style

- US English in code, comments and docs.
- Type hints on every parameter and return value.
- Docstrings say what a function does and why, not how.
