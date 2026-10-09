# Radio France Docker images

`airflow/` defines the reusable dependency environment for GitHub Actions.
It extends the official `apache/airflow:slim-3.3.2-python3.14` image with the project
Python runtime requirements, AWS CLI, and PostgreSQL 17 client tools.
Graphviz remains optional for local DAG rendering and is excluded from the image.
PostgreSQL **server** remains a separate `postgres:17` service container.

## Files

- `airflow/Dockerfile`: image definition; inherits Airflow's non-root user and entrypoint.
- `airflow/packages.txt`: system packages, one package name per line (no comments).
- `airflow/Dockerfile.dockerignore`: build-context allowlist.
- `airflow/smoke-test.sh`: checks versions, Python imports and installed tools.
- `../.github/workflows/airflow-image.yml`: builds, checks and optionally publishes.

The build context is the repository root so `airflow/requirements-runtime.txt` remains
the shared dependency source. The Dockerfile-specific ignore file admits only
that requirements file and the Docker build files. Local AWS configuration,
credentials, transcripts, database backups, virtualenvs and DAGs are excluded.
Runtime secrets and project code are supplied when using the image.

Edit `airflow/packages.txt` to change system dependencies and the repository's
`airflow/requirements-runtime.txt` to change Python dependencies.
The local `airflow/requirements.txt` includes this file and adds Graphviz. Package-list changes
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
or its workflow file. Pushes to main, v2 and `codex/airflow-container-image` build and publish;
pull requests build and test without publishing. Manual invocation defaults
to build-only; select `publish` on one of these branches to publish explicitly. There is no
scheduled build currently. GitHub's manual-run UI requires the workflow to exist
on the default branch first.

The workflow uses `GITHUB_TOKEN` with `packages: write`; no additional registry
secret or AWS access is needed. It builds linux/amd64 for the existing GitHub
runner and publishes only after the built image passes smoke checks. BuildKit's
GitHub cache reuses layers between builds.

The image name is `ghcr.io/<repository-owner>/radiofrance-airflow`. Tags are:

- The full Git commit SHA: identifies the tested build.
- `main`, `v2` or `codex-airflow-container-image`: moving convenience tags
  for each publishing branch (slashes are replaced with hyphens).

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

Download uses the published image pinned to its full commit tag, with
`packages: read` and `GITHUB_TOKEN` credentials. PostgreSQL remains a separate
service at hostname `postgres`. Register still uses its host virtualenv;
snapshot and measurement helpers support both execution modes.
The local wrapper defaults to the virtualenv; container jobs set
`RF_AIRFLOW_PYTHON=python` and `RF_AIRFLOW_COMMAND=airflow`.

Job steps run as root so checkout and artifact actions can write runner mounts.
The image retains its non-root default outside this workflow. The upstream
entrypoint is bypassed in the job container.

For runtime comparisons, include container initialization (image pull and
PostgreSQL startup), dependency setup, DAG execution, and full job duration.
Use the same show and note that new episodes and cache state affect DAG runtime.
Timing and size reports remain in the summary and artifact. Download still
checks compatibility without running migrations.

## Measuring the footprint

The image build runs `bash docker/measure-image.sh radiofrance-airflow:test`.
It records uncompressed base and final image sizes, layer sizes, major directory
sizes, the largest Python package directories, and Debian installed-package sizes
in the job summary. These views overlap and must not be added together. Debian
Installed-Size is package metadata in KiB; directory sizes are measured disk use.
No credentials, project data or metadata database are mounted into this check.
