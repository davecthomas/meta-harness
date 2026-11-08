variable "commit" {
  description = "The meta-harness commit the VM runs. A new value replaces the VM."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.commit))
    error_message = "commit is a full 40-character SHA, so the VM and the edge image are the same code."
  }
}

variable "edge_image" {
  description = "The edge image CI built from that commit, in Artifact Registry."
  type        = string
}

variable "machine_type" {
  # 8 vCPU and 32 GB: the control plane, plus MH_MAX_RUNNING (2) per-run sandboxes at their profiles'
  # 2 vCPU and 6 GB each, while each one installs and tests a Python repo.
  type    = string
  default = "e2-standard-8"
}
