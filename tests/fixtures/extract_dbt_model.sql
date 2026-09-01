{{
    config(
        materialized='incremental',
        unique_key='order_id'
    )
}}

-- Late-arriving facts land here before they reach fct_orders.
-- Do not read stg_returns from this model: see ADR-0010 in the analytics repo.

with orders as (

    select
        o.order_id,
        o.customer_id,
        o.ordered_at,
        o.status
    from {{ ref('stg_orders') }} as o

),

payments as (

    select
        p.order_id,
        p.payment_method,
        p.amount
    from {{ ref('finance', 'stg_payments') }} as p

),

regions as (

    select
        r.region_id,
        r.region_name
    from {{ source('crm', 'regions') }} as r

)

select
    orders.order_id,
    orders.customer_id,
    payments.payment_method,
    regions.region_name,
    sum(payments.amount) as order_total
from orders
left join payments on orders.order_id = payments.order_id
left join regions on orders.customer_id = regions.region_id
where orders.status != 'cancelled'
group by 1, 2, 3, 4
