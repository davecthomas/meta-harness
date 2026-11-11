"""The meta-harness control plane."""

import os

# The base version, which changes only with the VERSION file. A deployment runs the version worked out from
# main's history (versioning.py), which its deploy gives every service as MH_VERSION.
BASE_VERSION = "0.49.0"
__version__ = os.environ.get("MH_VERSION") or BASE_VERSION
