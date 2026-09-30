"""Regression for the historical population viewer's lazy plotting imports."""
import datetime

import pytest


def test_gregorian_axis_formatter_is_available_after_lazy_import():
    matplotlib = pytest.importorskip('matplotlib')
    matplotlib.use('Agg')
    import NSGAIIpopulation as population

    population._ensure_plotting()
    viewer = population.NSGAII_outerloop_population.__new__(population.NSGAII_outerloop_population)
    # This is the same formatter constructed by the viewer's Gregorian axes.
    formatter = population.ticker.FuncFormatter(viewer.format_date)
    value = population.dates.date2num(datetime.datetime(2000, 1, 1))
    assert formatter(value, 0) == '2000-01-01'
