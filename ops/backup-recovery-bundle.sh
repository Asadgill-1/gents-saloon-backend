#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly CONFIG_ROOT="/etc/gents-saloon"
readonly RELEASE_ROOT="/opt/gents-saloon"
recipient_file="${AGE_RECIPIENTS_FILE:-}"
destination="${S3_BACKUP_URI:-}"

if [[ -z "${recipient_file}" || ! -f "${recipient_file}" ]]; then
  echo "backup rejected: AGE_RECIPIENTS_FILE is missing" >&2
  exit 2
fi
if [[ ! "${destination}" =~ ^s3://[A-Za-z0-9._/-]+$ ]]; then
  echo "backup rejected: S3_BACKUP_URI must be an explicit s3:// path" >&2
  exit 2
fi

for command_name in age aws sha256sum tar; do
  command -v "${command_name}" >/dev/null || { echo "missing command: ${command_name}" >&2; exit 2; }
done

temporary_dir="$(mktemp -d)"
trap 'rm -f -- "${temporary_dir}/bundle.tar" "${temporary_dir}/bundle.tar.age" "${temporary_dir}/manifest.sha256"; rmdir "${temporary_dir}" 2>/dev/null || true' EXIT
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

files=(compose.env runtime.env)
for file_name in "${files[@]}"; do
  if [[ ! -f "${CONFIG_ROOT}/${file_name}" ]]; then
    echo "backup rejected: ${CONFIG_ROOT}/${file_name} is missing" >&2
    exit 2
  fi
done
if [[ ! -f "${RELEASE_ROOT}/last-successful-release" || ! -L "${RELEASE_ROOT}/current" ]]; then
  echo "backup rejected: no successful release is recorded" >&2
  exit 2
fi

current_release="$(readlink -f "${RELEASE_ROOT}/current")"
case "${current_release}" in
  "${RELEASE_ROOT}/releases/"*) ;;
  *) echo "backup rejected: current release escapes the release root" >&2; exit 2 ;;
esac

tar -C / -cf "${temporary_dir}/bundle.tar" \
  etc/gents-saloon/compose.env \
  etc/gents-saloon/runtime.env \
  "${current_release#/}/release.env" \
  "${current_release#/}/release-manifest.json" \
  opt/gents-saloon/last-successful-release
age --encrypt --recipients-file "${recipient_file}" --output "${temporary_dir}/bundle.tar.age" "${temporary_dir}/bundle.tar"
encrypted_sha256="$(sha256sum "${temporary_dir}/bundle.tar.age" | cut -d ' ' -f 1)"
printf '%s  %s\n' "${encrypted_sha256}" "recovery-${timestamp}.tar.age" >"${temporary_dir}/manifest.sha256"

object_uri="${destination%/}/recovery-${timestamp}.tar.age"
aws_options=()
if [[ -n "${S3_ENDPOINT_URL:-}" ]]; then
  aws_options+=(--endpoint-url "${S3_ENDPOINT_URL}")
fi
aws "${aws_options[@]}" s3 cp \
  "${temporary_dir}/bundle.tar.age" "${object_uri}" \
  --no-progress --sse AES256
aws "${aws_options[@]}" s3 cp \
  "${temporary_dir}/manifest.sha256" "${object_uri}.sha256" \
  --no-progress --sse AES256
echo "uploaded encrypted recovery bundle to ${object_uri}"
