import subprocess
import sys

import pytest

from storywell.models import SourceBook
from storywell.sources import (
    AudibleSource,
    SourceError,
    available_sources,
    make_source,
)


def test_available_sources_includes_audible():
    assert "audible" in available_sources()


def test_make_source_builds_audible_with_options():
    src = make_source("audible", auth_file=None, profile="uk")
    assert isinstance(src, AudibleSource)
    assert src.name == "audible"
    assert src.profile == "uk"


def test_make_source_drops_options_the_source_does_not_accept():
    src = make_source("audible", profile="us", goodreads_csv="ignored.csv")
    assert isinstance(src, AudibleSource)
    assert src.profile == "us"


def test_make_source_unknown_name_lists_available():
    with pytest.raises(SourceError, match="Unknown source 'kindle'.*audible"):
        make_source("kindle")


def test_source_book_key_namespaces_by_source():
    book = SourceBook(source="goodreads", source_id="12345", title="Dune")
    assert book.key == "goodreads:12345"


def test_audible_source_declares_audio_format():
    assert AudibleSource.media_format == "audio"
    assert make_source("audible").media_format == "audio"


def test_importing_stats_does_not_pull_in_the_audible_sdk():
    # The stats CSV reader lives under storywell.sources but must stay light enough to
    # run under Pyodide in the browser, where the Audible SDK can't be installed. Run in a
    # fresh interpreter so earlier tests' imports don't mask a regression.
    code = (
        "import sys, storywell.stats.export\n"
        "assert 'storywell.sources.audible' not in sys.modules, 'audible source imported'\n"
        "assert 'audible' not in sys.modules, 'audible SDK imported'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_unknown_attribute_raises_attribute_error():
    import storywell.sources as sources

    missing = "NopeSource"
    with pytest.raises(AttributeError):
        getattr(sources, missing)
