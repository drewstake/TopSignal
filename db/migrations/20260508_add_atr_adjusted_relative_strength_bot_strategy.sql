alter table bot_configs
  drop constraint if exists bot_configs_strategy_type_check;

update bot_configs
set strategy_type = 'sma_cross'
where strategy_type is null
   or strategy_type not in (
     'sma_cross',
     'support_resistance',
     'bollinger_rsi_reversal',
     'macd_support_resistance',
     'delayed_orb_confirmation',
     'ema_trend_pullback',
     'ema_scalping',
     'vwap_atr_mean_reversion',
     'fisher_transform_mean_reversion',
     'atr_adjusted_relative_strength',
     'fvg_sweep_mss',
     'orb_fibonacci_pullback',
     'pullback_trap_reversal',
     'supertrend_pivot',
     'bollinger_mean_reversion',
     'vwap_gap_retrace'
   );

alter table bot_configs
  alter column strategy_type set default 'sma_cross';

alter table bot_configs
  add constraint bot_configs_strategy_type_check
  check (
    strategy_type in (
      'sma_cross',
      'support_resistance',
      'bollinger_rsi_reversal',
      'macd_support_resistance',
      'delayed_orb_confirmation',
      'ema_trend_pullback',
      'ema_scalping',
      'vwap_atr_mean_reversion',
      'fisher_transform_mean_reversion',
      'atr_adjusted_relative_strength',
      'fvg_sweep_mss',
      'orb_fibonacci_pullback',
      'pullback_trap_reversal',
      'supertrend_pivot',
      'bollinger_mean_reversion',
      'vwap_gap_retrace'
    )
  );
