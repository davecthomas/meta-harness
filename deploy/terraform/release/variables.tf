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
  # 16 vCPU and 64 GB: MH_MAX_SANDBOXES (8, deploy/vm/boot.sh) per-run sandboxes at their profiles' cap of
  # 6 GB each, 48 GB at most, and 16 GB for Postgres, Temporal, Redis, the HTTP Gateway, the egress proxy
  # and the services, and for each run's own Postgres beside its sandbox (512 MB each, 4 GB for eight).
  # A sandbox's 2 vCPU is a cap, not a reservation, and an agent spends most of a run
  # waiting on its model, so eight share 16 vCPU and slow down, not fail, when several test at once.
  # tests/test_deploy.py checks this sum. A change here replaces the VM, which stops the runs on it.
  type    = string
  default = "e2-standard-16"
}
