-- Complaint history per customer as of the simulated today (var as_of_date), for policy rule D08.
-- One row per customer, so a lookup never has to treat "no row" as "no complaints".
{{ config(alias='customer_complaint_stats', post_hook='{{ export_gold() }}') }}

{% set as_of = "date '" ~ var('as_of_date') ~ "'" %}

select
    c.customer_id,
    count(x.complaint_id)::bigint as complaints_total,
    (count(x.complaint_id) filter (where x.creation_date >= {{ as_of }} - interval 90 day))::bigint
        as complaints_last_90d,
    max(x.creation_date) as last_complaint_at,
    count(x.complaint_id) filter (where x.creation_date >= {{ as_of }} - interval 90 day) > 0
        as is_repeat_complainer
from {{ ref('customers') }} as c
left join {{ ref('complaints') }} as x
    on x.customer_id = c.customer_id
    and x.creation_date < {{ as_of }} + interval 1 day
group by c.customer_id
