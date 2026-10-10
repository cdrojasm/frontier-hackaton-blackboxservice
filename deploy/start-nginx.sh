#!/bin/sh
set -eu

PORT="${PORT:-8080}"
DNS_SERVER="$(awk '/^nameserver[[:space:]]+/{print $2; exit}' /etc/resolv.conf)"
DNS_SERVER="${DNS_SERVER:-127.0.0.11}"
# Nginx requires IPv6 resolver addresses to be enclosed in brackets.
case "$DNS_SERVER" in
  *:*) DNS_SERVER="[$DNS_SERVER]" ;;
esac
sed -i "s/listen 8080;/listen ${PORT};/" /etc/nginx/conf.d/default.conf
sed -i "s/__RAILWAY_DNS_SERVER__/${DNS_SERVER}/" /etc/nginx/conf.d/default.conf
exec nginx -g "daemon off;"
