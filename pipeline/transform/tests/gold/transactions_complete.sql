-- Gold keeps the full history: every silver transaction must be in bank.transactions.
select (select count(*) from {{ ref('transactions') }}) as silver_rows,
       (select count(*) from {{ ref('bank_transactions') }}) as gold_rows
where silver_rows <> gold_rows
