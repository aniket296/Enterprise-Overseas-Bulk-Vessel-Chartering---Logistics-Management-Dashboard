import sys
import pathlib

# Add backend/ to sys.path so `apps.*` imports resolve cleanly
sys.path.insert(0, str(pathlib.Path(__file__).parent / "backend"))
