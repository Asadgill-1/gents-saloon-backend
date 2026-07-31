#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEPLOY_ROOT="/opt/gents-saloon"
readonly INCOMING_DIR="${DEPLOY_ROOT}/incoming"
readonly RELEASES_DIR="${DEPLOY_ROOT}/releases"
readonly COMPOSE_ENV="/etc/gents-saloon/compose.env"
readonly LOCK_FILE="${DEPLOY_ROOT}/deploy.lock"

image_ref="${1:-}"
release_id="${2:-}"

if [[ ! "${image_ref}" =~ ^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+/backend@sha256:[a-f0-9]{64}$ ]]; then
  echo "deployment rejected: backend image must be a GHCR digest" >&2
  exit 2
fi
if [[ ! "${release_id}" =~ ^[a-f0-9]{40}$ ]]; then
  echo "deployment rejected: release ID must be a full Git SHA" >&2
  exit 2
fi
if [[ ! -f "${COMPOSE_ENV}" ]]; then
  echo "deployment rejected: protected Compose environment is missing" >&2
  exit 2
fi

for required in compose.prod.yml Caddyfile release-manifest.json; do
  if [[ ! -f "${INCOMING_DIR}/${required}" ]]; then
    echo "deployment rejected: ${required} is missing" >&2
    exit 2
  fi
done

install -d -m 0750 "${DEPLOY_ROOT}" "${INCOMING_DIR}" "${RELEASES_DIR}"
exec 9>"${LOCK_FILE}"
flock -n 9 || { echo "another deployment is active" >&2; exit 3; }

release_dir="${RELEASES_DIR}/${release_id}"
if [[ -e "${release_dir}" ]]; then
  echo "deployment rejected: release already exists" >&2
  exit 2
fi

install -d -m 0750 "${release_dir}"
install -m 0644 "${INCOMING_DIR}/compose.prod.yml" "${release_dir}/compose.prod.yml"
install -m 0644 "${INCOMING_DIR}/Caddyfile" "${release_dir}/Caddyfile"
install -m 0644 "${INCOMING_DIR}/release-manifest.json" "${release_dir}/release-manifest.json"
printf 'BACKEND_IMAGE=%s\n' "${image_ref}" >"${release_dir}/release.env"
chmod 0600 "${release_dir}/release.env"

previous_release=""
if [[ -L "${DEPLOY_ROOT}/current" ]]; then
  previous_release="$(readlink -f "${DEPLOY_ROOT}/current")"
fi

compose=(docker compose --project-directory "${release_dir}" --env-file "${COMPOSE_ENV}" --env-file "${release_dir}/release.env" -f "${release_dir}/compose.prod.yml")
"${compose[@]}" pull

restore_previous_release() {
  if [[ -n "${previous_release}" && -f "${previous_release}/release.env" ]]; then
    previous_compose=(docker compose --project-directory "${previous_release}" --env-file "${COMPOSE_ENV}" --env-file "${previous_release}/release.env" -f "${previous_release}/compose.prod.yml")
    "${previous_compose[@]}" up -d --remove-orphans --wait --wait-timeout 120
  fi
}

if ! "${compose[@]}" up -d --remove-orphans --wait --wait-timeout 120; then
  echo "deployment container gate failed; restoring the prior application release" >&2
  restore_previous_release
  exit 4
fi

api_domain="$(sed -n 's/^API_DOMAIN=//p' "${COMPOSE_ENV}" | tail -n 1)"
if [[ ! "${api_domain}" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "deployment failed: API_DOMAIN is invalid" >&2
  restore_previous_release
  exit 4
fi

healthy=0
for _attempt in {1..24}; do
  if curl --fail --silent --show-error --max-time 5 "https://${api_domain}/health/ready" >/dev/null; then
    healthy=1
    break
  fi
  sleep 5
done

if [[ "${healthy}" -ne 1 ]]; then
  echo "deployment health gate failed" >&2
  restore_previous_release
  exit 4
fi

ln -sfn "${release_dir}" "${DEPLOY_ROOT}/current.next"
mv -Tf "${DEPLOY_ROOT}/current.next" "${DEPLOY_ROOT}/current"
printf '%s\n' "${release_id}" >"${DEPLOY_ROOT}/last-successful-release"
echo "deployed ${release_id} as ${image_ref}"
