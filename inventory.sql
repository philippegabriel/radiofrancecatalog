-- Convenience report; the reusable table inventory lives in public.inventory.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

-- Tables are discovered dynamically by the view's helper function.
SELECT table_name, record_count FROM public.inventory ORDER BY table_name;

SELECT show, count(*) AS record_count
FROM episodes_external
GROUP BY show
ORDER BY show;

COMMIT;
