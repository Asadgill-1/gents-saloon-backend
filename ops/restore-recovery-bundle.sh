#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

bundle="${1:-}"
identity_file="${AGE_IDENTITY_FILE:-}"
apply_confirmation="${2:-}"
checksum_file="${BUNDLE_SHA256_FILE:-}"

if [[ ! -f "${bundle}" || -z "${identity_file}" || ! -f "${identity_file}" ]]; then
  echo "restore rejected: encrypted bundle and AGE_IDENTITY_FILE are required" >&2
  exit 2
fi

temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "${temporary_dir}"' EXIT
if [[ -n "${checksum_file}" ]]; then
  if [[ ! -f "${checksum_file}" ]]; then
    echo "restore rejected: BUNDLE_SHA256_FILE does not exist" >&2
    exit 2
  fi
  expected_checksum="$(cut -d ' ' -f 1 "${checksum_file}")"
  actual_checksum="$(sha256sum "${bundle}" | cut -d ' ' -f 1)"
  if [[ ! "${expected_checksum}" =~ ^[a-f0-9]{64}$ || "${actual_checksum}" != "${expected_checksum}" ]]; then
    echo "restore rejected: encrypted bundle checksum mismatch" >&2
    exit 2
  fi
fi
age --decrypt --identity "${identity_file}" --output "${temporary_dir}/bundle.tar" "${bundle}"
tar -tf "${temporary_dir}/bundle.tar" >"${temporary_dir}/contents.txt"

while IFS= read -r member; do
  if [[ "${member}" == /* || "${member}" == *".."* ]]; then
    echo "restore rejected: unsafe archive member ${member}" >&2
    exit 2
  fi
  if [[ "${member}" != "etc/gents-saloon/compose.env" \
    && "${member}" != "etc/gents-saloon/runtime.env" \
    && ! "${member}" =~ ^opt/gents-saloon/releases/[a-f0-9]{40}/(release\.env|release-manifest\.json)$ \
    && "${member}" != "opt/gents-saloon/last-successful-release" ]]; then
    echo "restore rejected: unexpected archive member ${member}" >&2
    exit 2
  fi
done <"${temporary_dir}/contents.txt"

tar -C "${temporary_dir}" -xf "${temporary_dir}/bundle.tar"
echo "recovery bundle decrypted and validated in a temporary directory"

if [[ "${apply_confirmation}" != "CONFIRM_RESTORE" ]]; then
  echo "dry run only; pass CONFIRM_RESTORE after the restore checklist is approved"
  exit 0
fi

install -d -m 0750 /etc/gents-saloon /opt/gents-saloon/releases
install -m 0600 "${temporary_dir}/etc/gents-saloon/compose.env" /etc/gents-saloon/compose.env
install -m 0600 "${temporary_dir}/etc/gents-saloon/runtime.env" /etc/gents-saloon/runtime.env
cp -a "${temporary_dir}/opt/gents-saloon/releases/." /opt/gents-saloon/releases/
install -m 0644 "${temporary_dir}/opt/gents-saloon/last-successful-release" /opt/gents-saloon/last-successful-release
echo "recovery configuration applied; application and Supabase restore are separate approved steps"
