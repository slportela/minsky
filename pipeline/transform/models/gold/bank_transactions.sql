-- Every transaction (full history), with amount_usd filled in: the source leaves it null for all USD
-- transactions and for ~5 % of ARS/COP ones (docs/known_issues.md). Location and branch are left out.
{{ config(alias='transactions', post_hook='{{ export_gold() }}') }}

with usd_rates as (
    select "date" as rate_date, source_currency, exchange_rate
    from {{ ref('daily_exchange_rates') }}
    where target_currency = 'USD'
)

select
    t.transaction_id,
    t.customer_id,
    t.product_id,
    t.transaction_date,
    t.process_date,
    t.transaction_type,
    t.transaction_category,
    t.amount,
    t.currency,
    case
        when t.amount_usd is not null then t.amount_usd
        when t.currency = 'USD' then t.amount
        else round(t.amount * r.exchange_rate, 2)
    end::decimal(15, 2) as amount_usd,
    case
        when t.amount_usd is not null then 'reported'
        when t.currency = 'USD' then 'native_usd'
        when r.exchange_rate is not null then 'converted'
    end as amount_usd_source,
    t.channel,
    t.merchant_name,
    t.merchant_category,
    t.transaction_country,
    t.transaction_city,
    t.transaction_status,
    t.response_code,
    t.is_fraud,
    t.fraud_score
from {{ ref('transactions') }} as t
-- the source's own conversions match the rate of the process date (known_issues.md)
left join usd_rates as r on r.rate_date = t.process_date and r.source_currency = t.currency
