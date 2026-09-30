"""PyInstaller 진입점 (패키지 상대 import 를 쓰는 __main__ 대신 사용)."""

import sys

from workreport.app import main

if __name__ == "__main__":
    sys.exit(main())
