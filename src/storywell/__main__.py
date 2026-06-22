"""``python -m storywell`` and the packaged Briefcase app both launch the desktop GUI.

Briefcase runs the module matching the app name (``storywell``) as ``__main__``; the
windowed app's job is the GUI, so this delegates to the desktop launcher rather than the
CLI. The terminal CLI stays available as the ``storywell`` console script.
"""

from __future__ import annotations

from .desktop.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
