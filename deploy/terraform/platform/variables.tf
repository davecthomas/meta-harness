# The deployment's settings come from deploy/environment.json, passed with -var-file: the project, its region
# and zone, the state bucket, the console's site, the OAuth client and the repo that may deploy. None of them
# has a default here, so a forgotten setting fails the plan instead of pointing at some other project.

variable "project" {
  type = string
}

variable "project_number" {
  description = "The project's number. CI's sign-in names the workload identity provider by it, so the plan checks it against the project."
  type        = string
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "subnet_range" {
  type    = string
  default = "10.10.0.0/24"
}

variable "vm_ip" {
  description = "The VM's fixed internal address, which the edge is told."
  type        = string
  default     = "10.10.0.10"
}

variable "data_disk_gb" {
  description = "Postgres data, Temporal's history and run workspaces."
  type        = number
  default     = 100
}

variable "github_repo" {
  description = "The repository whose main branch may deploy."
  type        = string
}

variable "state_bucket" {
  description = "The bucket holding both stacks' Terraform state (make platform creates it)."
  type        = string
}

variable "operators" {
  description = "Who may open the console: the Google accounts the edge lets sign in. Kept out of the public repo: CI sets it from the MH_OPERATORS secret, and `make platform` from deploy/.env (see deploy/.env.example)."
  type        = list(string)
  sensitive   = true
}

variable "admins" {
  description = "Who of the operators may change configuration, use the kill switch and open the Admin menu. Empty: every operator."
  type        = list(string)
  default     = []
}

variable "secrets" {
  description = "The Secret Manager secrets the VM and the edge read. Values are added out of band."
  type        = list(string)
  default = [
    "MH_POSTGRES_PASSWORD", "MH_SERVICE_KEY", "MH_GATEWAY_API_KEY",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_GEMINI_API_KEY",
    "MH_REPOS_CONFIG", "MH_GITHUB_APPS", "MH_GITHUB_APP_KEY_davecthomas",
    # The Jira account the Jira tracker signs in as: its email, and an API token of that account. Optional: a
    # deployment with no value in them has no Jira sign-in, and the console says so.
    "MH_JIRA_EMAIL", "MH_JIRA_TOKEN",
  ]
}

variable "console_site" {
  description = "The Firebase Hosting site, and so the console's address: https://<console_site>.web.app. Unique across Firebase."
  type        = string
}

variable "google_client_id" {
  description = "The OAuth client the console's Google sign-in uses (a client id is public). Made by hand in the project (deploy/README.md); empty until then, and the release stack refuses to deploy without it."
  type        = string
}
