#!/usr/bin/env bash
# Install nginx + a free Let's Encrypt TLS cert for the console. Run as root on the VM
# AFTER the DNS A-record for $DOMAIN points at this VM's public IP and port 80 is open.
#   sudo DOMAIN=console.example.com EMAIL=you@example.com bash ops/deploy/setup-tls.sh
# certbot adds the 443 server + the 80->443 redirect and sets up auto-renewal.
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN (the console FQDN whose A-record points at this VM)}"
EMAIL="${EMAIL:?set EMAIL for Lets-Encrypt expiry notices}"
APP_PORT="${APP_PORT:-8503}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)"; exit 1; }

echo ">>> nginx + certbot"
command -v nginx   >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y nginx; }
command -v certbot >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y certbot python3-certbot-nginx; }

echo ">>> install the site (domain=$DOMAIN, upstream 127.0.0.1:$APP_PORT)"
sed -e "s/__SET_YOUR_ENV_DNS__/$DOMAIN/" -e "s#127.0.0.1:8503#127.0.0.1:$APP_PORT#" \
  "$SCRIPT_DIR/nginx-agentic-console.conf" > /etc/nginx/sites-available/agentic-console
ln -sfn /etc/nginx/sites-available/agentic-console /etc/nginx/sites-enabled/agentic-console
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ">>> obtain + install the cert (adds 443 + 80->443 redirect)"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
systemctl enable --now certbot.timer 2>/dev/null || true

echo ">>> done — https://$DOMAIN is live (auto-renew via certbot.timer)."
echo "    Set GITHUB_OAUTH_CALLBACK_URL=https://$DOMAIN/auth/github/callback in env.nonsecret.<env>,"
echo "    and the SAME callback in this environment GitHub OAuth app."
