{# Use a model's custom schema as is (gold -> "bank"), instead of dbt's default "<target>_<custom>". #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
