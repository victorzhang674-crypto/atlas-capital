"""Stage 12: live download of the Loughran-McDonald lexicon (`-m live`, deselected by default)."""

from pathlib import Path

import pytest

from atlas.core.config import SentimentConfig
from atlas.data.sentiment import download_lm_lexicon, load_lexicon, score_text

pytestmark = pytest.mark.live


def test_live_download_lm_lexicon(tmp_path: Path) -> None:
    settings = SentimentConfig(lexicon_file=tmp_path / "lm_lexicon.csv")
    lexicon = load_lexicon(download_lm_lexicon(settings))
    assert len(lexicon.negative) >= 2000
    assert len(lexicon.positive) >= 300
    assert len(lexicon.uncertainty) >= 250
    assert score_text("Profit declined on weak demand", lexicon).score == -1.0
