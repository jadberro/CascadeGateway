"""
Backward-compatibility stub for test_cascade.py
"""
import sys
import subprocess

if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "tests/test_gateway.py"]))
