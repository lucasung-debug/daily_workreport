"""python -m workreport"""

import sys


def main() -> int:
    from .app import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
