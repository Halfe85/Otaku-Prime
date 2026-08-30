#!/usr/bin/env bash
set -euo pipefail

PORT="9898"
RULE_COMMENT="Otaku Prime temporary WAN test"
STATE_DIR="/var/lib/otaku-prime"
STATE_FILE="${STATE_DIR}/ufw-9898-test-rule"

usage() {
  echo "Usage: sudo $0 enable|status|disable" >&2
  exit 2
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this command with sudo." >&2
    exit 1
  fi
}

require_ufw() {
  if ! command -v ufw >/dev/null 2>&1; then
    echo "UFW is not installed; no firewall changes were made." >&2
    exit 1
  fi
}

global_rule_exists() {
  ufw status | grep -Eq "^${PORT}/tcp[[:space:]]+ALLOW( IN)?[[:space:]]+Anywhere([[:space:]]|$)"
}

show_status() {
  ufw status verbose
  if [[ -e "${STATE_FILE}" ]]; then
    echo "Otaku Prime owns the temporary ${PORT}/tcp test rule."
  elif global_rule_exists; then
    echo "${PORT}/tcp already accepts IPv4 clients from Anywhere through a pre-existing rule."
  else
    echo "${PORT}/tcp does not currently accept IPv4 clients from Anywhere."
  fi
}

enable_rule() {
  if global_rule_exists; then
    echo "${PORT}/tcp already accepts clients from Anywhere; leaving that rule untouched."
    show_status
    return
  fi

  ufw allow "${PORT}/tcp" comment "${RULE_COMMENT}"
  install -d -m 0755 "${STATE_DIR}"
  printf '%s\n' "${RULE_COMMENT}" > "${STATE_FILE}"
  echo "Temporary inbound TCP ${PORT} rule enabled."
  show_status
}

disable_rule() {
  if [[ ! -e "${STATE_FILE}" ]]; then
    echo "This helper did not create the ${PORT}/tcp rule; nothing was removed."
    show_status
    return
  fi

  if global_rule_exists; then
    ufw --force delete allow "${PORT}/tcp"
  fi
  rm -f -- "${STATE_FILE}"
  echo "Temporary inbound TCP ${PORT} rule removed."
  show_status
}

require_root
require_ufw

case "${1:-}" in
  enable) enable_rule ;;
  status) show_status ;;
  disable) disable_rule ;;
  *) usage ;;
esac
