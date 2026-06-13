import os
import sys

# Make the top-level universis_mcp_server module importable from tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
