import sys
import io
from pathlib import Path

# Fix pythonw.exe windowless execution: sys.stdout and sys.stderr are None
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cascadegateway.tray.app import main

if __name__ == "__main__":
    main()
