"""Technical video QA and quarter-second visual review sheets (code in studio/shot_qa.py).

Usage: python scripts/shot_qa.py candidate.mp4 [--reference reference.mp4] [--out-dir qa]
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.shot_qa import *  # noqa: E402,F401,F403  - legacy scripts import from here
from studio.shot_qa import main  # noqa: E402

if __name__ == "__main__":
    main()
