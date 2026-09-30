-- Who the customer is, for greeting, language register and handoff context. Data minimisation:
-- identity documents, contact details, address, income and credit score stay out of the read models.
{{ config(alias='customers', post_hook='{{ export_gold() }}') }}

select
    customer_id,
    first_name,
    country,
    segment,
    customer_status,
    detected_accent
from {{ ref('customers') }}
