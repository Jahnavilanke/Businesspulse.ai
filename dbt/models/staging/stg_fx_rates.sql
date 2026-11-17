{{ config(enabled = var('enable_fx')) }}
-- SILVER: monthly average ECB reference rates (base GBP) from the Frankfurter API.
select month::date as month, currency, rate::double as rate_per_gbp, source
from {{ lake('silver/fx_rates/*.parquet') }}
