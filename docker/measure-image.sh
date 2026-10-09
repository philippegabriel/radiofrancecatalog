#!/usr/bin/env bash
# Report overlapping views of image size; no runtime data or credentials needed.
set -euo pipefail
image=${1:?Supply the built image name}
base=$(awk '$1 == "FROM" {print $2; exit}' docker/airflow/Dockerfile)
docker pull "$base" > /dev/null
echo '### Uncompressed image sizes (bytes)'
echo '```text'
docker image inspect "$base" "$image" --format '{{index .RepoTags 0}} {{.Size}}'
echo '```'
echo '### Final image layers (newest first)'
echo '```text'
docker history "$image" --format '{{.Size}} {{.CreatedBy}}'
echo '```'
docker run --rm -i --entrypoint bash "$image" -s <<'SHELL'
set -euo pipefail
echo '### Major directories (disk bytes; overlapping views)'
echo '```text'
du -x -B1 -s /opt/venv /usr/lib /usr/local /usr/share /var/lib
echo '```'
echo '### Largest Airflow Python package directories (disk bytes)'
echo '```text'
du -x -B1 -s /opt/venv/lib/python*/site-packages/* | sort -n | tail -25
echo '```'
echo '### Largest Debian packages (Installed-Size in KiB)'
echo '```text'
dpkg-query -W -f='${Installed-Size}\t${Package}\n' | sort -n | tail -25
echo '```'
echo '### System Python packages, including AWS CLI (disk bytes)'
echo '```text'
du -x -B1 -s /usr/lib/python3/dist-packages/* | sort -n | tail -15
echo '```'
SHELL
