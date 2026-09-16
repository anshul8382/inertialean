# Shared Apache paths for cPanel (/etc/apache2) vs CentOS (/etc/httpd).
# shellcheck shell=bash

_apache_ssl_directive() {
  local file="$1"
  local directive="$2"
  grep -E "^[[:space:]]*${directive}[[:space:]]+" "$file" 2>/dev/null | head -1 | awk '{print $2}' || true
}

apache_find_legacy_443_conf() {
  local soak_file="${SOAK_APACHE_CONF:-}"
  local soak_base=""
  [[ -n "$soak_file" ]] && soak_base="$(basename "$soak_file")"

  local candidate
  for candidate in \
    "${APACHE_CONF_DIR}/inertiainvest.in.conf" \
    "${APACHE_CONF_DIR}/inertiainvest.in-centos.conf" \
    "/etc/httpd/conf.d/inertiainvest.in.conf"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done

  local hit
  while IFS= read -r hit; do
    [[ -z "$hit" ]] && continue
    [[ -n "$soak_base" && "$(basename "$hit")" == "$soak_base" ]] && continue
    [[ "$hit" == *5003* ]] && continue
    [[ "$hit" == *soak* ]] && continue
    echo "$hit"
    return 0
  done < <(grep -rl '127.0.0.1:5000' "${APACHE_CONF_DIR}" /etc/apache2/conf.d /etc/httpd/conf.d 2>/dev/null || true)

  return 1
}

apache_detect_layout() {
  local soak=""
  soak="$(find /etc/apache2 /etc/httpd -name 'inertia2026v1-5003-ssl.conf' 2>/dev/null | head -1 || true)"

  if [[ -n "$soak" ]]; then
    APACHE_BASE="$(cd "$(dirname "$soak")/.." && pwd)"
    APACHE_CONF_DIR="$(dirname "$soak")"
  elif [[ -d /etc/apache2/conf.d ]]; then
    APACHE_BASE="/etc/apache2"
    APACHE_CONF_DIR="/etc/apache2/conf.d"
  elif [[ -d /etc/httpd/conf.d ]]; then
    APACHE_BASE="/etc/httpd"
    APACHE_CONF_DIR="/etc/httpd/conf.d"
  else
    echo "ERROR: No Apache conf.d found under /etc/apache2 or /etc/httpd" >&2
    return 1
  fi

  APACHE_INERTIA_DIR="${APACHE_BASE}/inertia"
  BACKUP_DIR="${APACHE_INERTIA_DIR}/cutover-backups"

  if [[ -z "${SOAK_APACHE_CONF:-}" ]]; then
    SOAK_APACHE_CONF="${soak:-${APACHE_CONF_DIR}/inertia2026v1-5003-ssl.conf}"
  fi
  if [[ -z "${APACHE_CONF_DEST:-}" ]]; then
    APACHE_CONF_DEST="${APACHE_CONF_DIR}/inertiainvest-prod-v2026.conf"
  fi
  if [[ -z "${LEGACY_APACHE_CONF:-}" ]]; then
    LEGACY_APACHE_CONF="$(apache_find_legacy_443_conf || true)"
  fi
  if [[ -z "${HOOKS_CONF_DEST:-}" ]]; then
    HOOKS_CONF_DEST="${APACHE_CONF_DIR}/inertiainvest-hooks.conf"
  fi
  if [[ -z "${LOCKDOWN_CONF_DEST:-}" ]]; then
    LOCKDOWN_CONF_DEST="${APACHE_INERTIA_DIR}/inertiainvest-public-lockdown.conf"
  fi

  export APACHE_BASE APACHE_CONF_DIR APACHE_INERTIA_DIR BACKUP_DIR
  export SOAK_APACHE_CONF APACHE_CONF_DEST LEGACY_APACHE_CONF HOOKS_CONF_DEST LOCKDOWN_CONF_DEST
}

apache_is_cpanel_proxy_legacy() {
  [[ "${LEGACY_APACHE_CONF:-}" == *proxy_flask.conf* ]] || [[ "${LEGACY_APACHE_CONF:-}" == *userdata* ]]
}

apache_cpanel_rebuild_httpd() {
  if [[ -x /usr/local/cpanel/scripts/rebuildhttpdconf ]]; then
    /usr/local/cpanel/scripts/rebuildhttpdconf
  fi
}

_apache_patch_proxy_port_in_file() {
  local f="$1"
  local port="$2"
  [[ -f "$f" ]] || return 0
  sed -i \
    -e "s|127.0.0.1:5000|127.0.0.1:${port}|g" \
    -e "s|localhost:5000|127.0.0.1:${port}|g" \
    -e "s|http://127.0.0.1:5000|http://127.0.0.1:${port}|g" \
    -e "s|http://localhost:5000|http://127.0.0.1:${port}|g" \
    -e "s|:5000/|:${port}/|g" \
    -e "s|:5000\"|:${port}\"|g" \
    -e "s|:5000 |:${port} |g" \
    "$f"
}

apache_cpanel_userdata_dirs() {
  local base="/etc/apache2/conf.d/userdata"
  local sub
  for sub in ssl/2_4/inertia/inertiainvest.in std/2_4/inertia/inertiainvest.in; do
    [[ -d "${base}/${sub}" ]] && echo "${base}/${sub}"
  done
}

apache_cpanel_update_proxy() {
  local primary="${1:-}"
  local new_port="${2:-5004}"
  local app_dir="${3:-/opt/Inertia2026v1}"
  local proxy_tpl="${app_dir}/config/cpanel-proxy_flask-v2026.conf"
  local zzz_src="${app_dir}/config/cpanel-inertiainvest-v2026-userdata.conf"
  local dir f

  # Prefer full replace — cPanel proxy_flask often uses RewriteRule, not sed-friendly ProxyPass.
  if [[ -f "$proxy_tpl" ]]; then
    while IFS= read -r dir; do
      if [[ -f "${dir}/proxy_flask.conf" ]]; then
        cp -a "${dir}/proxy_flask.conf" "${BACKUP_DIR}/proxy_flask-$(echo "$dir" | tr '/' '_').bak"
      fi
      cp "$proxy_tpl" "${dir}/proxy_flask.conf"
      sed -i "s|127.0.0.1:5004|127.0.0.1:${new_port}|g" "${dir}/proxy_flask.conf"
      echo "Replaced: ${dir}/proxy_flask.conf"
    done < <(apache_cpanel_userdata_dirs)
  else
    while IFS= read -r f; do
      [[ -f "$f" ]] || continue
      _apache_patch_proxy_port_in_file "$f" "$new_port"
      echo "Patched: $f"
    done < <(grep -rl '5000' /etc/apache2/conf.d/userdata 2>/dev/null | grep -i 'inertiainvest\.in' || true)
    if [[ -n "$primary" && -f "$primary" ]]; then
      _apache_patch_proxy_port_in_file "$primary" "$new_port"
    fi
  fi

  if [[ -f "$zzz_src" ]]; then
    while IFS= read -r dir; do
      cp "$zzz_src" "${dir}/zzz_v2026_gunicorn.conf"
      sed -i "s|127.0.0.1:5004|127.0.0.1:${new_port}|g" "${dir}/zzz_v2026_gunicorn.conf"
      echo "Installed: ${dir}/zzz_v2026_gunicorn.conf"
    done < <(apache_cpanel_userdata_dirs)
  fi

  apache_cpanel_rebuild_httpd
}

apache_cpanel_bind_v2026_on_port() {
  local app_dir="$1"
  local port="$2"
  local env_file="${app_dir}/.env"
  if [[ ! -f "$env_file" ]]; then
    echo "ERROR: missing ${env_file}" >&2
    return 1
  fi
  if grep -q '^GUNICORN_BIND=' "$env_file"; then
    sed -i "s|^GUNICORN_BIND=.*|GUNICORN_BIND=127.0.0.1:${port}|" "$env_file"
  else
    echo "GUNICORN_BIND=127.0.0.1:${port}" >> "$env_file"
  fi
  echo "Set GUNICORN_BIND=127.0.0.1:${port} in ${env_file}"
}

apache_cpanel_verify_proxy_port() {
  local want="${1:-5004}"
  if grep -q "127.0.0.1:5000" /etc/apache2/conf/httpd.conf 2>/dev/null; then
    echo "WARN: compiled httpd.conf still contains 127.0.0.1:5000" >&2
    grep -n '127.0.0.1:5000' /etc/apache2/conf/httpd.conf 2>/dev/null | head -5 >&2 || true
    return 1
  fi
  if ! grep -q "127.0.0.1:${want}" /etc/apache2/conf/httpd.conf 2>/dev/null; then
    echo "WARN: compiled httpd.conf missing 127.0.0.1:${want}" >&2
    return 1
  fi
  return 0
}

apache_install_vhost_from_template() {
  local src="$1"
  local dest="$2"
  local ssl_source=""

  if [[ -f "${SOAK_APACHE_CONF:-}" ]]; then
    ssl_source="${SOAK_APACHE_CONF}"
  elif [[ -n "${LEGACY_APACHE_CONF:-}" && -f "${LEGACY_APACHE_CONF}" ]]; then
    ssl_source="${LEGACY_APACHE_CONF}"
  fi

  mkdir -p "$(dirname "$dest")"
  sed "s|/etc/httpd/inertia|${APACHE_INERTIA_DIR}|g" "$src" > "$dest"

  if [[ -z "$ssl_source" ]]; then
    echo "WARN: No SSL source vhost found; edit ${dest} certificate paths manually" >&2
    return 0
  fi

  local cert key chain
  cert="$(_apache_ssl_directive "$ssl_source" SSLCertificateFile)"
  key="$(_apache_ssl_directive "$ssl_source" SSLCertificateKeyFile)"
  chain="$(_apache_ssl_directive "$ssl_source" SSLCertificateChainFile)"

  if [[ -n "$cert" ]]; then
    sed -i "s|^[[:space:]]*SSLCertificateFile.*|    SSLCertificateFile ${cert}|" "$dest"
  fi
  if [[ -n "$key" ]]; then
    sed -i "s|^[[:space:]]*SSLCertificateKeyFile.*|    SSLCertificateKeyFile ${key}|" "$dest"
  fi
  if [[ -n "$chain" && -f "$chain" ]]; then
    sed -i "s|^[[:space:]]*SSLCertificateChainFile.*|    SSLCertificateChainFile ${chain}|" "$dest"
  else
    sed -i '/^[[:space:]]*SSLCertificateChainFile/d' "$dest"
  fi

  sed -i '/^[[:space:]]*ServerTokens/d' "$dest"
  sed -i '/^[[:space:]]*ServerSignature/d' "$dest"

  echo "SSL paths copied from: ${ssl_source}"
}

apache_reload() {
  local svc="${HTTPD_SERVICE:-httpd}"
  apachectl configtest
  systemctl reload "$svc" 2>/dev/null || systemctl reload apache2 2>/dev/null
}
