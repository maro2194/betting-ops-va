#!/bin/bash
# Tunnel watchdog: if wg0 peer has no recent handshake, restart wstunnel + wg
LAST=$(wg show wg0 latest-handshakes 2>/dev/null | awk '{print $2}' | head -1)
NOW=$(date +%s)
if [ -z "$LAST" ]; then
  exit 0  # wg not up — let wg-quick service handle it
fi
AGE=$((NOW - LAST))
if [ "$LAST" = "0" ] || [ "$AGE" -gt 180 ]; then
  logger -t wg-watchdog "handshake age ${AGE}s -> restarting wstunnel-client + wg-quick@wg0"
  systemctl restart wstunnel-client
  sleep 5
  systemctl restart wg-quick@wg0
fi
