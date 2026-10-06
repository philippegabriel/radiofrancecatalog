# Python changes

After every set of edits to Python files, run Pyright from the project root:

```bash
venv/airflow/bin/pyright
```

Resolve reported errors before declaring the work complete. If the check cannot
run, report the blocker and do not claim that type checking passed.

Use `TypedDict` for dictionaries with known fields and the `type` keyword for
other type aliases. Annotate new or modified Python functions.

# Airflow

Always document newly created DAGs and tasks:

- Give each DAG a clear description and documentation explaining its purpose,
  execution flow, required configuration, and how to run it.
- Give each task function a docstring explaining what it does, its inputs and
  outputs, and any relevant side effects such as writing files, accessing a
  database, or publishing assets.
- Keep this documentation current when DAG or task behavior changes.
