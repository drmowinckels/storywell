import storywell.__main__ as pkg_main
from storywell.desktop.__main__ import main as desktop_main


def test_package_main_launches_the_desktop_gui():
    # `python -m storywell` (and the Briefcase app) must open the GUI, not the CLI.
    assert pkg_main.main is desktop_main
