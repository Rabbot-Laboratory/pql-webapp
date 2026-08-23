#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "install-service.sh must run as root" >&2
  exit 1
fi

APP_ROOT=/home/rabbot/pql-single-leg/single-leg-app

install -m 0644 \
  "$APP_ROOT/deploy/single-leg-control.service" \
  /etc/systemd/system/single-leg-control.service
install -o rabbot -g rabbot -m 0600 \
  "$APP_ROOT/deploy/single-leg.env" \
  "$APP_ROOT/.env"

systemctl daemon-reload
systemctl disable --now highend-control.service
systemctl enable --now single-leg-control.service
