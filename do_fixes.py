# This file applies critical fixes
from pathlib import Path
import re

# FIX 1: Update _extract_client_pid
path_cc = Path('/c/Dev/AgentCLI/agent_runner/backends/claudecode.py')
