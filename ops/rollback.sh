#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEPLOY_ROOT="/opt/gents-saloon"
readonly COMPOSE_ENV="/etc/gents-saloon/compose.env"
release_id="${1:-}"

if [[ ! "${release_id}" =~ ^[a-f0-9]{40}$ ]]; then
  echo "rollback rejected: release ID must be a full Git SHA" >&2
  exit 2
fi

release_dir="${DEPLOY_ROOT}/releases/${release_id}"
for required in compose.prod.yml Caddyfile release.env release-manifest.json; do
  if [[ ! -f "${release_dir}/${required}" ]]; then
    echo "rollback rejected: release artifact ${required} is missing" >&2
    exit 2
  fi
done

image_ref="$(sed -n 's/^BACKEND_IMAGE=//p' "${release_dir}/release.env")"
if [[ ! "${image_ref}" =~ @sha256:[a-f0-9]{64}$ ]]; then
  echo "rollback rejected: stored image is not immutable" >&2
  exit 2
fi

compose=(docker compose --project-directory "${release_dir}" --env-file "${COMPOSE_ENV}" --env-file "${release_dir}/release.env" -f "${release_dir}/compose.prod.yml")
"${compose[@]}" up -d --remove-orphans --wait --wait-timeout 120
ln -sfn "${release_dir}" "${DEPLOY_ROOT}/current.next"
mv -Tf "${DEPLOY_ROOT}/current.next" "${DEPLOY_ROOT}/current"
printf '%s\n' "${release_id}" >"${DEPLOY_ROOT}/last-successful-release"
echo "rolled back application containers to ${release_id}; database migrations were not reversed"
