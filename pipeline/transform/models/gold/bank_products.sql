-- The customer's products; card numbers are reduced to their last four digits.
{{ config(alias='products', post_hook='{{ export_gold() }}') }}

select
    product_id,
    customer_id,
    product_type,
    product_type in ('Credit Card', 'Debit Card') as is_card,
    right(product_number, 4) as product_number_last4,
    currency,
    product_status,
    opening_date,
    expiration_date,
    has_linked_app
from {{ ref('products') }}
