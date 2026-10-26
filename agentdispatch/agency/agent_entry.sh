#!/bin/sh
# Fail-closed network lock for an agent container: it may talk to ONE address (the agency gateway) and nothing else - not Ollama, not the broker, not RDP/SMB/other host ports, not the internet.
# Runs as root only long enough to set the firewall, then drops every privilege and starts Claude Code as the unprivileged user.
GW_HOST="${AD_GATEWAY_HOST:-host.docker.internal}"
GW_PORT="${AD_GATEWAY_PORT:-8871}"
GW_IP=$(getent ahostsv4 "$GW_HOST" | awk 'NR==1{print $1}')
if [ -z "$GW_IP" ]; then echo "network lock: cannot resolve $GW_HOST" >&2; exit 97; fi
for T in iptables ip6tables; do
  $T -F OUTPUT && $T -F INPUT || { echo "network lock: $T failed" >&2; exit 98; }
  $T -A OUTPUT -o lo -j ACCEPT
  $T -A INPUT -i lo -j ACCEPT
  $T -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  [ "$T" = iptables ] && $T -A OUTPUT -d "$GW_IP" -p tcp --dport "$GW_PORT" -j ACCEPT
  $T -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  $T -P OUTPUT DROP && $T -P INPUT DROP || { echo "network lock: could not set policy" >&2; exit 98; }
done
export AD_GATEWAY_IP="$GW_IP"
# everything the agent may reach is under its own run token on the gateway
export ANTHROPIC_BASE_URL="http://$GW_IP:$GW_PORT/g/$AD_RUN_TOKEN/ollama"
printf '{"mcpServers":{"agentdispatch":{"type":"http","url":"http://%s:%s/g/%s/mcp"}}}' "$GW_IP" "$GW_PORT" "$AD_RUN_TOKEN" > /tmp/mcp.json && chmod 644 /tmp/mcp.json
mkdir -p /tmp/cfg && chmod 777 /tmp/cfg
if [ "$1" = "--selftest" ]; then
  exec setpriv --reuid=1000 --regid=1000 --init-groups --bounding-set=-all --inh-caps=-all --no-new-privs python3 - <<PY
import socket
ok = []
for host, port in (("$GW_IP", $GW_PORT), ("$GW_IP", 11434), ("$GW_IP", 8870), ("$GW_IP", 3389), ("1.1.1.1", 443)):
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); ok.append("%s:%d OPEN" % (host, port))
    except Exception:
        ok.append("%s:%d blocked" % (host, port))
print("; ".join(ok))
PY
fi
exec setpriv --reuid=1000 --regid=1000 --init-groups --bounding-set=-all --inh-caps=-all --no-new-privs claude "$@"
