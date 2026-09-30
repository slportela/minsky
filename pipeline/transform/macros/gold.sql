{# Post-hook: export a gold table to the lake as one Parquet file (data/lake/gold/<table>.parquet). #}
{% macro export_gold() -%}
    copy {{ this }} to '{{ var("lake_root") }}/gold/{{ this.identifier }}.parquet' (format parquet, compression zstd)
{%- endmacro %}
