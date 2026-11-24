#!/usr/bin/env bash
set -euo pipefail
umask 027
if [ "$(id -u)" -ne 0 ]; then echo "Run as root"; exit 1; fi
host="math-school.147-45-228-201.sslip.io"
base="https://raw.githubusercontent.com/Romchik84/math-school/main/vps"
for cmd in python3 curl caddy systemctl; do command -v "$cmd" >/dev/null || { echo "Missing $cmd"; exit 1; }; done
test -f /etc/caddy/Caddyfile || { echo "Caddyfile missing"; exit 1; }
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$base/index.html" -o "$tmp/index.html"
curl -fsSL "$base/server.py" -o "$tmp/server.py"
python3 -m py_compile "$tmp/server.py"
if ! id mathschool >/dev/null 2>&1; then useradd --system --no-create-home --shell /usr/sbin/nologin mathschool; fi
install -d -m 0755 /opt/math-school
install -d -m 0750 -o root -g mathschool /etc/math-school
if [ ! -f /etc/math-school/credentials.json ]; then
 python3 - <<'PY'
import json,secrets,os
p='/etc/math-school/credentials.json'
with open(p,'x') as f:
 json.dump({r:{'password':secrets.token_urlsafe(15),'token':secrets.token_urlsafe(32)} for r in ('student','parent')},f)
os.chmod(p,0o640)
PY
fi
chown root:mathschool /etc/math-school/credentials.json
chmod 0640 /etc/math-school/credentials.json
install -m 0644 "$tmp/index.html" /opt/math-school/index.html
install -m 0644 "$tmp/server.py" /opt/math-school/server.py
cat >/etc/systemd/system/math-school.service <<'UNIT'
[Unit]
Description=Math School private family workbook
After=network.target
[Service]
User=mathschool
Group=mathschool
WorkingDirectory=/opt/math-school
ExecStart=/usr/bin/python3 /opt/math-school/server.py
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable math-school.service
systemctl restart math-school.service
for n in $(seq 1 10); do if curl -fsS http://127.0.0.1:3178/health; then break; fi; sleep 1; done
curl -fsS http://127.0.0.1:3178/health >/dev/null
if ! grep -Fq "$host" /etc/caddy/Caddyfile; then
 backup="/etc/caddy/Caddyfile.math-school.$(date +%s).bak"
 cp -a /etc/caddy/Caddyfile "$backup"
 printf '\n%s {\n    reverse_proxy 127.0.0.1:3178\n}\n' "$host" >> /etc/caddy/Caddyfile
 if ! caddy validate --config /etc/caddy/Caddyfile || ! systemctl reload caddy; then
  cp -a "$backup" /etc/caddy/Caddyfile
  systemctl reload caddy || true
  echo "Caddy configuration restored"; exit 1
 fi
fi
echo
echo "Math School 3.0 installed. Check HTTPS:"
echo "https://$host/health"
echo "Student: https://$host/student"
echo "Parent:  https://$host/parent"
echo "Private passwords — do NOT post these in chat:"
python3 - <<'PY'
import json
d=json.load(open('/etc/math-school/credentials.json'))
print('Student password:',d['student']['password'])
print('Parent password: ',d['parent']['password'])
PY
