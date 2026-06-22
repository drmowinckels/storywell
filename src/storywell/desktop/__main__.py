"""``python -m storywell.desktop`` launches the desktop window."""

from __future__ import annotations

import sys

from .app import DesktopDependencyError, run


def main() -> int:
    headed = "--headed" in sys.argv[1:]
    debug = "--debug" in sys.argv[1:]
    try:
        run(headless=not headed, debug=debug)
    except DesktopDependencyError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
