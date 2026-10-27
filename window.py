"""Legacy script entry point for the shared desktop interface."""
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from b2t.cli import create_app, _detect_preferred_language


def main() -> None:
    create_app(_detect_preferred_language())(["window", *sys.argv[1:]], prog_name="bili2text")


if __name__ == "__main__":
    main()
