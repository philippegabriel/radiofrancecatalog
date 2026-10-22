-- Serialize pilot imports so shared immutable records are checked consistently.
SELECT pg_advisory_xact_lock(741831221);
SELECT to_regclass('public.import_run');
