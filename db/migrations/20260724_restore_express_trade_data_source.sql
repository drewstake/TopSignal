-- Repair only provider-discovered Express accounts that could have been
-- converted by the retired trade-data-source toggle. Dedicated Topstep Live
-- import rows use a different name and remain csv_import accounts.
update accounts
set trade_data_source = 'projectx'
where provider = 'projectx'
  and trade_data_source = 'csv_import'
  and name ilike 'EXPRESS-%';
