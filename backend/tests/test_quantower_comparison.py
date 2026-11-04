from datetime import datetime, timedelta, timezone
from tools.compare_quantower_trades import compare, trade, read_quantower


def test_repeated_prints_are_preserved_and_not_used_as_unique_pairs():
    at = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
    buy = trade(at, 100, 2, 'buy')
    sell = trade(at, 100, 2, 'sell')
    result = compare([buy, sell], [buy], at, at + timedelta(seconds=1))
    assert result['quantower']['trades'] == 2
    assert result['quantower']['delta_contracts'] == '0'
    assert result['quantower_unmatched_occurrences'] == 1
    assert result['ambiguous_matching_keys'] == 1
    assert result['unique_pair_label_agreements'] == 0
    assert result['exact_timestamp_price_size_side_multiset_matches'] == 1


def test_unique_direction_mismatch_and_exclusive_end():
    at = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
    end = at + timedelta(seconds=1)
    result = compare([trade(at, 100, 3, 'buy'), trade(end, 100, 1, 'buy')],
                     [trade(at, 100, 3, 'sell')], at, end)
    assert result['unique_pair_label_disagreements'] == 1
    assert result['quantower']['contracts']['buy'] == '3'
    assert result['projectx']['delta_contracts'] == '-3'
    assert result['exact_timestamp_price_size_side_multiset_matches'] == 0


def test_same_totals_do_not_hide_label_differences_between_prints():
    at = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
    later = at + timedelta(milliseconds=1)
    qt = [trade(at, 100, 2, 'buy'), trade(later, 100, 2, 'sell')]
    px = [trade(at, 100, 2, 'sell'), trade(later, 100, 2, 'buy')]
    result = compare(qt, px, at, at + timedelta(seconds=1))
    assert result['quantower'] == result['projectx']
    assert result['unique_pair_label_disagreements'] == 2
    assert result['exact_timestamp_price_size_side_multiset_matches'] == 0


def test_csv_local_day_converts_across_utc_midnight(tmp_path):
    path = tmp_path / 'sample.csv'
    path.write_text('Time,Last,Size,Aggressor,\n8:59:06.744 PM,27871.75,1,Sell,\n')
    rows = read_quantower(path, '2026-11-03', 'America/New_York')
    assert rows[0][0].isoformat() == '2026-11-04T01:59:06.744000+00:00'
    assert rows[0][3] == 'sell'
