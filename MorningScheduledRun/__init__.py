"""Azure Function entry point for the morning scheduled pipeline run."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from azure_function_helpers import morning_scheduled_run as main