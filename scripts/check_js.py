"""Check every frontend script with Node; no shell-specific glob expansion."""
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for path in sorted((root / "src/sctutor/static").glob("*.js")):
    subprocess.run(["node", "--check", str(path)], check=True)
print("All frontend JavaScript syntax checks passed.")
