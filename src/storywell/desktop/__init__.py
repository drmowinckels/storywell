"""Desktop GUI front end for Storywell.

A thin shell around :mod:`storywell.service`: :mod:`storywell.desktop.bridge` exposes the
engine to a web UI as JSON-able calls run off the UI thread, and :mod:`storywell.desktop.app`
wires that bridge into a native pywebview window. The shell adds no sync logic of its own.
"""
