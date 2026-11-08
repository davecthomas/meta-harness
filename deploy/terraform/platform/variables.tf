variable "project" {
  type    = string
  default = "meta-harness-prod"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
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
  default     = "davecthomas/meta-harness"
}

variable "state_bucket" {
  description = "The bucket holding both stacks' Terraform state (make platform creates it)."
  type        = string
  default     = "meta-harness-prod-terraform"
}

variable "operators" {
  description = "Who may open the console: the Google accounts the edge lets sign in."
  type        = list(string)
  default     = ["davidcthomas@gmail.com"]
}

variable "secrets" {
  description = "The Secret Manager secrets the VM and the edge read. Values are added out of band."
  type        = list(string)
  default = [
    "MH_POSTGRES_PASSWORD", "MH_SERVICE_KEY", "MH_GATEWAY_API_KEY",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_GEMINI_API_KEY",
    "MH_REPOS_CONFIG", "MH_GITHUB_APPS", "MH_GITHUB_APP_KEY_davecthomas",
  ]
}

variable "console_site" {
  description = "The Firebase Hosting site, and so the console's address: https://<console_site>.web.app."
  type        = string
  default     = "meta-harness"
}

variable "google_client_id" {
  description = "The OAuth client the console's Google sign-in uses (a client id is public)."
  type        = string
  default     = "57473592166-7gaei6qjh7rp5p21j0fnmaaqtbr4ovqn.apps.googleusercontent.com"
}
