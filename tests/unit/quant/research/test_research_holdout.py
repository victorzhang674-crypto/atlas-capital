"""Phase 2 checkpoint, Tier 2: does a research window read the hold-out (spec A7)?"""

from datetime import date

import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.quant.research import HoldoutCheck, holdout_check

CAL = TradingCalendar()
END = date(2021, 12, 31)  # the default `backtest.in_sample_end`, a session
HORIZONS = (1, 5, 21, 63)
LATEST = CAL.offset(END, -63)  # the last decision whose 63-session label closes on END


def test_labels_that_close_on_the_in_sample_end_stay_in_sample() -> None:
    check = holdout_check([date(2021, 1, 4), LATEST], HORIZONS)
    assert isinstance(check, HoldoutCheck)
    assert check.in_sample_end == END
    assert check.last_decision == LATEST
    assert check.last_label_session == END
    assert check.latest_decision_in_sample == LATEST
    assert check.reads_holdout is False


def test_one_session_later_reads_the_holdout() -> None:
    check = holdout_check([date(2021, 1, 4), CAL.next_session(LATEST)], HORIZONS)
    assert check.last_label_session == date(2022, 1, 3)
    assert check.reads_holdout is True
    assert check.latest_decision_in_sample == LATEST


def test_the_longest_horizon_sets_the_reach() -> None:
    day = CAL.offset(END, -1)
    assert holdout_check([day], (1,)).reads_holdout is False
    assert holdout_check([day], (1, 5)).reads_holdout is True
    assert holdout_check([day], (5, 1)).last_label_session == CAL.offset(day, 5)
    assert holdout_check([day], (1,)).latest_decision_in_sample == day


def test_in_sample_end_on_a_closed_day_means_the_session_before() -> None:
    cfg = AtlasConfig.model_validate({"backtest": {"in_sample_end": "2022-01-01"}})  # Saturday
    check = holdout_check([LATEST], HORIZONS, config=cfg)
    assert check.in_sample_end == date(2022, 1, 1)
    assert check.reads_holdout is False
    assert check.latest_decision_in_sample == LATEST
    assert holdout_check([CAL.next_session(LATEST)], HORIZONS, config=cfg).reads_holdout is True


def test_default_horizons_come_from_the_config() -> None:
    cfg = AtlasConfig.model_validate({"quant": {"research": {"horizons": [1, 2]}}})
    assert holdout_check([END], config=cfg).last_label_session == CAL.offset(END, 2)
    assert holdout_check([END]).last_label_session == CAL.offset(END, 63)


def test_bad_inputs_raise() -> None:
    with pytest.raises(ValueError, match="dates"):
        holdout_check([], HORIZONS)
    with pytest.raises(ValueError, match="horizons"):
        holdout_check([END], ())
    with pytest.raises(ValueError, match="horizons"):
        holdout_check([END], (0,))
