import importlib.util
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('check', Path(__file__).parents[1] / 'tools/check_topbot.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
NOW = datetime(2026, 10, 22, 14, 15, tzinfo=timezone.utc)

def sample():
    distribution = {'probability_net_positive': .6, 'probability_net_nonpositive': .4,
                    'probability_stop': .3, 'probability_time_exit': .4, 'probability_target': .3,
                    'expected_net_usd': 1.0}
    forecast = {'event':'observation_forecast','observation_id':'test:1','contract_id':'test',
                'decision_close':'2026-10-22T13:40:00Z','recorded_at':'2026-10-22T13:40:01Z',
                'validation_status':'unvalidated','execution_mode':'dry_run','broker_orders':False,
                'training_stale':True,'artifact_sha256':'test','forecasts':{'BUY':distribution,'SELL':distribution}}
    outcome = {**forecast,'event':'observation_outcome','recorded_at':'2026-10-22T13:55:01Z',
               'outcome_status':'observed','outcomes':{'BUY':{'hypothetical_net_usd':2},'SELL':{'hypothetical_net_usd':-3}}}
    return forecast,outcome

def summarize(records):
    return check.summarize_observations(records,NOW,'2026-10-22','test')

class Tests(unittest.TestCase):
    def test_retry_not_double_counted(self):
        f,o=sample()
        result=summarize([f,o,deepcopy(f),deepcopy(o)])
        self.assertEqual((result['forecasts'],result['matched_observed_outcomes'],result['duplicates']), (1,1,2))
    def test_missing_and_unknown_are_not_successes(self):
        f,o=sample()
        self.assertEqual(summarize([f])['overdue_missing_outcomes'],['test:1'])
        o['outcome_status']='unknown_missing_bars_or_contract_roll'
        result=summarize([f,o])
        self.assertEqual(result['unknown_outcomes'],1)
        self.assertEqual(result['matched_observed_outcomes'],0)
    def test_premature_result_rejected(self):
        f,o=sample();o['recorded_at']='2026-10-22T13:45:01Z'
        self.assertEqual(summarize([f,o])['invalid_outcomes'],['test:1'])
    def test_bad_modes_and_probabilities_rejected(self):
        f,o=sample();f['broker_orders']=True
        self.assertEqual(summarize([f])['invalid_forecasts'],['test:1'])
        f,o=sample();f['forecasts']['BUY']['probability_stop']=2
        self.assertEqual(summarize([f])['invalid_forecasts'],['test:1'])
    def test_other_session_not_counted(self):
        f,o=sample();f['decision_close']='2026-10-21T13:40:00Z'
        self.assertEqual(summarize([f])['forecasts'],0)
    def test_conflicts_flagged(self):
        f,o=sample();changed=deepcopy(f);changed['broker_orders']=True
        self.assertEqual(summarize([f,changed])['conflicting_duplicates'],1)
    def test_mutating_broker_routes_denied(self):
        for endpoint in ('/api/Order/place','/api/Order/cancel','/api/Order/modify','/api/Position/closeContract','/api/Position/partialCloseContract'):
            self.assertFalse(check.broker_is_readonly('POST',endpoint))
        self.assertFalse(check.broker_is_readonly('DELETE','/api/Order/search'))
        self.assertTrue(check.broker_is_readonly('POST','/api/Order/search'))

def evaluation(close, **changes):
    close = check.stamp(close)
    row = dict(contract='test', run=9, close=close.isoformat(), at=(close+timedelta(seconds=20)).isoformat(),
               action='HOLD', status='held', stage='volatility_risk_cap', outcome_status='no_forecast',
               forecast={'forecasts': None, 'reason_code': 'volatility_above_risk_cap',
                         'data_status': 'fresh', 'candle_close_timestamp': close.isoformat()},
               # Deliberately not used to classify collection or count forecasts.
               volatility_counterfactual={'status': 'observed', 'outcomes': {'BUY': {}, 'SELL': {}}})
    row.update(changes)
    return row


def health(records=None, observations=None, **kwargs):
    return check.collection_health(
        records if records is not None else [evaluation('2026-10-22T14:05:00Z'), evaluation('2026-10-22T14:10:00Z')],
        observations or summarize(sample()), now=kwargs.pop('now', NOW), contract='test', run_id=9,
        boundary_reason=kwargs.pop('boundary_reason', lambda _: None), **kwargs)


class CollectionTests(unittest.TestCase):
    def test_stale_forecast_with_continuous_fresh_risk_holds_is_expected(self):
        result = health()
        self.assertTrue(result['healthy'])
        self.assertEqual(result['status'], 'expected_risk_hold')
        self.assertEqual(result['latest_forecast_age_seconds'], 35*60)
        self.assertEqual((result['forecasts'], result['matched_observed_outcomes']), (1, 1))

    def test_entire_session_risk_blocked_can_have_zero_forecasts(self):
        result = health(observations=summarize([]))
        self.assertTrue(result['healthy'])
        self.assertEqual(result['forecasts'], 0)
        self.assertIsNone(result['latest_forecast_age_seconds'])

    def test_old_block_and_recent_process_heartbeat_do_not_explain_missing_evaluations(self):
        result = health([evaluation('2026-10-22T13:50:00Z')])
        self.assertFalse(result['healthy'])
        self.assertEqual(len(result['missing_expected_closes']), 2)

    def test_recent_single_block_does_not_hide_an_evaluation_gap(self):
        self.assertFalse(health([evaluation('2026-10-22T14:10:00Z')])['healthy'])

    def test_stale_invalid_or_unexplained_inputs_still_alert(self):
        for status in ('stale', 'invalid', 'missing'):
            with self.subTest(status=status):
                rows = [evaluation('2026-10-22T14:05:00Z'), evaluation('2026-10-22T14:10:00Z')]
                rows[-1]['forecast']['data_status'] = status
                self.assertFalse(health(rows)['healthy'])
        rows[-1]['forecast']['data_status'] = 'fresh'
        rows[-1]['forecast']['reason_code'] = 'artifact_integrity_failure'
        self.assertFalse(health(rows)['healthy'])

    def test_hold_label_or_what_if_result_alone_is_insufficient(self):
        for patch in ({'stage': 'strategy_hold'}, {'action': 'BUY'}, {'status': 'failed'},
                      {'forecast': {}}, {'outcome_status': 'pending'}):
            with self.subTest(patch=patch):
                self.assertFalse(health([evaluation('2026-10-22T14:05:00Z'), evaluation('2026-10-22T14:10:00Z', **patch)])['healthy'])

    def test_other_run_and_other_contract_cannot_mask_missing_evaluations(self):
        for patch in ({'run': 8}, {'contract': 'other'}):
            rows = [evaluation('2026-10-22T14:05:00Z', **patch), evaluation('2026-10-22T14:10:00Z', **patch)]
            self.assertFalse(health(rows)['healthy'])

    def test_future_late_unaligned_and_duplicate_records_alert(self):
        base = [evaluation('2026-10-22T14:05:00Z'), evaluation('2026-10-22T14:10:00Z')]
        for patch in ({'at': '2026-10-22T14:16:00Z'}, {'at': '2026-10-22T14:12:01Z'},
                      {'close': '2026-10-22T14:10:01Z'}, {'at': '2026-10-22T14:09:59Z'}):
            rows = deepcopy(base); rows[-1].update(patch)
            self.assertFalse(health(rows)['healthy'])
        self.assertFalse(health(base + [deepcopy(base[-1])])['healthy'])

    def test_available_forecast_with_broken_log_is_not_excused_by_previous_holds(self):
        rows = [evaluation('2026-10-22T14:05:00Z'), evaluation('2026-10-22T14:10:00Z')]
        rows[-1]['forecast']['forecasts'] = sample()[0]['forecasts']
        self.assertFalse(health(rows)['healthy'])
        summary = summarize(sample())
        summary['recorded_forecast_closes'].append(check.stamp(rows[-1]['close']))
        self.assertEqual(health(rows, summary)['status'], 'collecting_forecasts')

    def test_actual_session_gate_is_required_for_expected_session_hold(self):
        rows = [evaluation('2026-10-22T14:05:00Z', stage='outside_entry_window', reason='NO TRADE: outside_research_session.'),
                evaluation('2026-10-22T14:10:00Z', stage='outside_entry_window', reason='NO TRADE: outside_research_session.')]
        self.assertFalse(health(rows)['healthy'])
        self.assertEqual(health(rows, boundary_reason=lambda _: 'outside_research_session')['status'], 'expected_session_hold')

    def test_delivery_budget_handles_bar_boundary_and_requires_next_bar_after_grace(self):
        self.assertTrue(health(now=NOW+timedelta(seconds=119))['healthy'])
        self.assertFalse(health(now=NOW+timedelta(seconds=121))['healthy'])

    def test_naive_timestamp_is_not_interpreted_in_local_timezone(self):
        with self.assertRaises(ValueError):
            check.stamp(datetime(2026, 10, 22, 14, 15))


if __name__=='__main__':
    unittest.main()
