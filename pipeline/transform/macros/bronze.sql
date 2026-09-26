{# Raw CSV rows of one bronze table, every column as varchar plus the source file path. #}
{% macro bronze(table, partitioned) -%}
    {%- set path = var('bronze_root') ~ '/data/' ~ table ~ ('/*/*/*/*.csv' if partitioned else '.csv') -%}
    read_csv('{{ path }}', header = true, all_varchar = true, filename = true,
             hive_partitioning = false, union_by_name = true)
{%- endmacro %}


{# Latest ingestion record per source file, from the bronze run manifests. #}
{% macro bronze_manifest() -%}
    select source_key, run_id, ingested_at::timestamp as ingested_at
    from read_json('{{ var("bronze_root") }}/_manifest/*.jsonl', format = 'newline_delimited')
    qualify row_number() over (partition by source_key order by ingested_at desc) = 1
{%- endmacro %}


{# Cast a varchar column to its documented type; NULL when the value doesn't fit. #}
{% macro to_type(col, type) -%}
    {%- if type == 'varchar' -%}
        {{ col }}
    {%- elif type == 'integer' -%}
        {#- the source writes integers as floats ("180.0"); reject real fractions -#}
        case when try_cast({{ col }} as double) = floor(try_cast({{ col }} as double))
             then try_cast({{ col }} as double)::integer end
    {%- else -%}
        try_cast({{ col }} as {{ type }})
    {%- endif -%}
{%- endmacro %}


{# Post-hook: export the silver table to the lake as Parquet (monthly partitions for facts). #}
{% macro export_parquet(partitioned) -%}
    {%- if partitioned -%}
        copy {{ this }} to '{{ var("lake_root") }}/silver/{{ this.name }}'
            (format parquet, compression zstd, partition_by (year, month), overwrite true)
    {%- else -%}
        copy {{ this }} to '{{ var("lake_root") }}/silver/{{ this.name }}.parquet'
            (format parquet, compression zstd)
    {%- endif -%}
{%- endmacro %}
