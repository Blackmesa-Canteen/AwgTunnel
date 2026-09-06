import sys
from pathlib import Path

# Allow `pytest tests/` from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
