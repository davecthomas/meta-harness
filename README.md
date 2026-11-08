# meta-harness

The meta-harness is a control plane that turns goals into pull requests: it
plans tasks from GitHub issues across registered repos, picks a harness
profile and model for each task, runs it in an isolated sandbox, and records
the outcome. Tasks run independently, so one task's failure or one model
provider's outage leaves the others running.

## Learn more

- [`docs/technical-design.md`](docs/technical-design.md) is the design: the
  components, what is built versus planned, and the build plan.
- [`docs/requirements.md`](docs/requirements.md) has the requirements.
- [`AGENTS.md`](AGENTS.md) has the rules for coding agents working in this
  repo: how to build, test and lint, and what a task must never do.
- [`deploy/README.md`](deploy/README.md) explains the cloud deployment: two
  Terraform stacks under `deploy/terraform` (`platform` and `release`), a
  deploy on every merge to main, and the `make platform`, `make deploy` and
  `make deploy-status` targets.
- [`console/`](console/) is the operator console, the web app operators use
  to run the meta-harness: goals, tasks, live runs, cancel, the kill switch
  and configuration.
