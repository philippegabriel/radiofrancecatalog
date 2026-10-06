# Python changes

After every set of edits to Python files, run Pyright from the project root:

```bash
venv/airflow/bin/pyright
```

Resolve reported errors before declaring the work complete. If the check cannot
run, report the blocker and do not claim that type checking passed.

Use `TypedDict` for dictionaries with known fields and the `type` keyword for
other type aliases. Annotate new or modified Python functions.
