-- Historical resolution times from complaints, by category and priority ('all' = every value).
-- Answers "how long will my case take?" and is the baseline of today's human service.
{{ config(alias='resolution_benchmarks', post_hook='{{ export_gold() }}') }}

select
    coalesce(category, 'all') as category,
    coalesce(priority, 'all') as priority,
    count(*)::bigint as cases,
    (count(*) filter (where status in ('Resolved', 'Closed')))::bigint as resolved_cases,
    (median(resolution_days) filter (where status in ('Resolved', 'Closed')))::double as median_resolution_days,
    (quantile_cont(resolution_days, 0.75) filter (where status in ('Resolved', 'Closed')))::double
        as p75_resolution_days,
    avg(sla_breached::int)::double as sla_breach_rate,
    avg((status = 'Rejected')::int)::double as rejection_rate
from {{ ref('complaints') }}
group by grouping sets ((category, priority), (category), ())
