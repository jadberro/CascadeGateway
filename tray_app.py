"""
CascadeGateway System Tray Backward-Compatibility Wrapper
Delegates to cascadegateway.tray.app.
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cascadegateway.tray.app import main

if __name__ == "__main__":
    main()
