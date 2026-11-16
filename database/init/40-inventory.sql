-- Also apply explicitly with psql -f to upgrade an existing database.
\connect radiofrance
SET ROLE radiofrance;
BEGIN;

-- Dynamic SQL is needed because table names cannot be SQL parameters.
-- STABLE makes all counts use the calling statement's snapshot.
CREATE OR REPLACE FUNCTION public.inventory_counts()
RETURNS TABLE (table_name text, record_count bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT n.nspname, c.relname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
          AND NOT c.relispartition
          AND n.nspname <> 'information_schema'
          AND n.nspname !~ '^pg_'
        ORDER BY n.nspname, c.relname
    LOOP
        RETURN QUERY EXECUTE format(
            'SELECT %L::text, count(*) FROM %I.%I',
            target.nspname || '.' || target.relname,
            target.nspname, target.relname);
    END LOOP;
END;
$$;

CREATE OR REPLACE VIEW public.inventory WITH (security_invoker = true) AS
SELECT table_name, record_count FROM public.inventory_counts();

COMMENT ON VIEW public.inventory IS
    'Exact row counts for user tables, discovered on each query; partition children are counted through their parent. Requires caller SELECT access to all counted tables.';

COMMIT;
RESET ROLE;
