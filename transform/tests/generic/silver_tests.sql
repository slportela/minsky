{# Rows whose _dq_issues contain an issue starting with `prefix` (e.g. 'cast_failed'). #}
{% test no_dq_issue(model, prefix) %}
    select *
    from {{ model }}
    where len(list_filter(_dq_issues, x -> starts_with(x, '{{ prefix }}:'))) > 0
{% endtest %}


{# Duplicate combinations of `columns` (composite primary keys). #}
{% test unique_combination(model, columns) %}
    select {{ columns | join(', ') }}, count(*) as n
    from {{ model }}
    group by all
    having count(*) > 1
{% endtest %}
