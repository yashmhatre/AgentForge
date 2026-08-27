-- Daily order revenue. The comment names payments_backup, which is not a table
-- this query touches, and the extractor must not say that it is.
INSERT INTO analytics.daily_revenue
SELECT
    o.order_id,
    customer_name,
    SUM(o.total) AS revenue
FROM analytics.orders o
JOIN `customers` c ON c.id = o.customer_id
WHERE o.status = 'shipped from payments_backup'
GROUP BY o.order_id, customer_name;
