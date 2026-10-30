"""V2 price-boundary regressions; no signal or risk thresholds are relaxed."""
import math

import numpy as np
import pytest

from app.services.probabilistic_strategy import Costs, price_paths


def mirrored(paths, side):
    return paths if side == 1 else -paths[:, :, [0, 2, 1, 3]]


def scenario(last):
    return np.array([[[0., 1., -1., 0.], [0., 1., -1., 0.], last]])


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('offset', [1, 2])
def test_observed_exact_stop_touch_survives_normalize_rescale(side, offset):
    # MNQU6, 2026-08-18 09:45 ET. The final low exactly touches the
    # offset-2 BUY stop at 29605; the mirrored SELL must behave identically.
    absolute = np.array([[
        [29687.75, 29700.5, 29644.5, 29657.5],
        [29657.25, 29683.0, 29633.75, 29637.25],
        [29637.5, 29665.75, 29605.0, 29647.5],
    ]])
    scale = 18.95504550245132 * math.sqrt(3)
    paths = (absolute - absolute[0, 1, 0]) / scale
    net, kind, duration = price_paths(mirrored(paths, side), stop=33., side=side,
                                    costs=Costs(), scale=scale, entry_offset_bars=offset)
    assert net[0] == pytest.approx(-68.22)
    assert kind[0] == 0
    assert duration[0] == 3 - offset


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('offset', [1, 2])
@pytest.mark.parametrize('event', ['stop', 'target', 'ambiguous', 'trade_through'])
def test_roundoff_at_intrabar_boundaries_preserves_stop_first(side, offset, event):
    # Filled entry .5; stop -3.5; target 6.5. One representable step is
    # normalization roundoff, not a market-price change or a smaller bracket.
    low = np.nextafter(-3.5, np.inf) if event in ('stop', 'ambiguous') else -1.
    target = 6.75 if event == 'trade_through' else 6.5
    high = np.nextafter(target, -np.inf) if event != 'stop' else 1.
    net, kind, duration = price_paths(mirrored(scenario([0., high, low, 0.]), side),
        stop=4., side=side, costs=Costs(), scale=1., entry_offset_bars=offset,
        trade_through_ticks=int(event == 'trade_through'))
    stopped = event in ('stop', 'ambiguous')
    assert kind[0] == (0 if stopped else 2)
    assert net[0] == pytest.approx(-10.22 if stopped else 9.78)
    assert duration[0] == 3 - offset


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('event', ['stop', 'target'])
def test_roundoff_opening_touch_is_ordered_before_intrabar_moves(side, event):
    # A gap requires an already open position, so this exercises two-bar entry.
    opening = np.nextafter(-3.5, np.inf) if event == 'stop' else np.nextafter(6.5, -np.inf)
    last = [opening] * 4 if event == 'stop' else [opening, 7., -10., 0.]
    net, kind, duration = price_paths(mirrored(scenario(last), side), stop=4., side=side,
                                    costs=Costs(), scale=1., entry_offset_bars=1)
    assert kind[0] == (0 if event == 'stop' else 2)
    assert net[0] == pytest.approx(-10.22 if event == 'stop' else 9.78)
    assert duration[0] == 2


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('event', ['stop', 'target'])
def test_true_gaps_keep_adverse_stop_fills_and_no_target_bonus(side, event):
    opening = -3.75 if event == 'stop' else 6.75
    last = [opening] * 4 if event == 'stop' else [opening, 7., -10., 0.]
    net, kind, duration = price_paths(mirrored(scenario(last), side), stop=4., side=side,
                                    costs=Costs(), scale=1., entry_offset_bars=1)
    assert kind[0] == (0 if event == 'stop' else 2)
    assert net[0] == pytest.approx(-10.72 if event == 'stop' else 9.78)
    assert duration[0] == 2


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('offset', [1, 2])
@pytest.mark.parametrize('distance', [1e-9, .25])
@pytest.mark.parametrize('event', ['stop', 'target', 'trade_through'])
def test_real_near_misses_are_not_rounded_to_fills(side, offset, distance, event):
    low = -3.5 + distance if event == 'stop' else -1.
    target = 6.75 if event == 'trade_through' else 6.5
    high = target - distance if event != 'stop' else 1.
    net, kind, duration = price_paths(mirrored(scenario([0., high, low, 0.]), side),
        stop=4., side=side, costs=Costs(), scale=1., entry_offset_bars=offset,
        trade_through_ticks=int(event == 'trade_through'))
    assert kind[0] == 1
    assert net[0] == pytest.approx(-3.22)
    assert duration[0] == 3 - offset


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('event', ['stop', 'target', 'ambiguous'])
def test_legacy_three_bar_pricing_keeps_its_exact_original_comparisons(side, event):
    low = np.nextafter(-3.5, np.inf) if event != 'target' else -1.
    high = np.nextafter(6.5, -np.inf) if event != 'stop' else 1.
    net, kind, duration = price_paths(mirrored(scenario([0., high, low, 0.]), side),
                                    stop=4., side=side, costs=Costs(), scale=1.)
    # Historical legacy artifacts are not numerically reinterpreted by the fix.
    assert kind[0] == 1
    assert net[0] == pytest.approx(-3.22)
    assert duration[0] == 3
