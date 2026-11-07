"""Where the meta-harness run service finds its configuration and keeps local runs.

REPO_ROOT is the checkout this package runs from. The environment overrides
each path (HARNESS_PROFILES, AI_MODELS_CONFIG, RUNS_DIR), as a deployed
service does.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
