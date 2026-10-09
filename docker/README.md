# Radio France Docker images

`airflow/` defines the reusable dependency environment for GitHub Actions.
It extends the official `apache/airflow:3.3.2-python3.14` image with the project
Python requirements, AWS CLI, Graphviz, and PostgreSQL 17 client tools.
PostgreSQL **server** remains a separate `postgres:17` service container.

## Files

- `airflow/Dockerfile`: image definition; inherits Airflow's non-root user and entrypoint.
- `airflow/packages.txt`: system packages, one package name per line (no comments).
- `airflow/Dockerfile.dockerignore`: build-context allowlist.
- `airflow/smoke-test.sh`: checks versions, Python imports and installed tools.
- `../.github/workflows/airflow-image.yml`: builds, checks and optionally publishes.

The build context is the repository root so `airflow/requirements.txt` remains
the shared dependency source. The Dockerfile-specific ignore file admits only
that requirements file and the Docker build files. Local AWS configuration,
credentials, transcripts, database backups, virtualenvs and DAGs are excluded.
Runtime secrets and project code are supplied when using the image.

Edit `airflow/packages.txt` to change system dependencies and the repository's
`airflow/requirements.txt` to change Python dependencies. Package-list changes
rebuild the system installation layer; Python-only changes reuse that layer.

## Build locally

Run from the repository root with a working Docker daemon:

```bash
docker build -f docker/airflow/Dockerfile -t radiofrance-airflow:test .
docker run --rm --entrypoint bash radiofrance-airflow:test /opt/radiofrance/smoke-test.sh
docker image inspect radiofrance-airflow:test --format '{{.Size}}'
```

The size reported by Docker is the local uncompressed size, not the registry's
compressed download size. PostgreSQL tools are explicitly version 17 so dumps
remain compatible with the PostgreSQL 17 service. AWS CLI comes from Debian's
package manager, isolated from Airflow's Python environment.

## GitHub build and publication

**Build Airflow image** runs on changes to `docker/`, `airflow/requirements.txt`,
or its workflow file. Pushes to main/v2 build and publish; the development branch
and pull requests build and test without publishing. Manual invocation defaults
to build-only; select `publish` on main or v2 to publish explicitly. There is no
scheduled build currently. GitHub's manual-run UI requires the workflow to exist
on the default branch first.

The workflow uses `GITHUB_TOKEN` with `packages: write`; no additional registry
secret or AWS access is needed. It builds linux/amd64 for the existing GitHub
runner and publishes only after the built image passes smoke checks. BuildKit's
GitHub cache reuses layers between builds.

The image name is `ghcr.io/<repository-owner>/radiofrance-airflow`. Tags are:

- The full Git commit SHA: identifies the tested build.
- `main` or `v2`: moving convenience tags for each publishing branch.

When adopting the image in a workflow, pin the tested SHA tag or image digest.
For deliberate rebuilds, pin a digest if exact bytes must remain fixed: a manual
rebuild of the same commit can obtain newer OS packages or base-image layers.
Both the Airflow base image and smoke checks must be updated when changing the
Airflow or Python version in requirements. PostgreSQL client upgrades must be
coordinated with service versions and snapshot namespaces.

GHCR packages are private when first published by default. The repository's
workflows need package read access to consume a private image; they should
supply `GITHUB_TOKEN` credentials with `packages: read`. Public visibility can be
chosen separately. The workflow does not change package visibility automatically.

## Adopting the image

This branch introduces image building only; Register and Download continue to
use their existing virtualenv installation. The next integration step is to run
those jobs in the tested image, use hostname `postgres` for the database service,
and replace host-side `docker exec` and `venv/airflow/bin/python` assumptions
with direct PostgreSQL client and Python calls inside the job container.
