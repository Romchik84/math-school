#!/usr/bin/env bash
set -euo pipefail
umask 027
base=https://raw.githubusercontent.com/Romchik84/math-school/494ee4dd2eb18de91460760968d149c6b0de78c/vps
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
test -f /opt/math-school/server.py
test -f /etc/math-school/credentials.json
curl -fsSL "$base/server.py" -o "$tmp/server.py"
curl -fsSL "$base/index.html" -o "$tmp/index.html"
python3 -m py_compile "$tmp/server.py"
MATH_ASSIGNMENTS="$tmp/test-assignments.json" PYTHONPATH="$tmp" python3 - <<'PY'
import server
a=server.new_assignment({"title":"Test","tasks":[{"text":"2+2","answer":"4"}]})
assert a["seed"]==4000000001
server.save_assignments([a])
assert server.json.loads(server.ASSIGN_FILE.read_text())[0]["tasks"][0]["answer"]=="4"
server.ASSIGNMENTS.append(a)
assert "photo" not in server.public_assignments()[0]
try:
 server.new_assignment({"title":"Test","tasks":[{"text":""}]})
 raise AssertionError("empty task accepted")
except ValueError:
 pass
print("ASSIGNMENT STORAGE TEST OK")
PY
backup="/root/math-school-v32-$(date +%s)"
install -d -m 0700 "$backup"
cp -a /opt/math-school/server.py /opt/math-school/index.html "$backup/"
drop=/etc/systemd/system/math-school.service.d/32-storage.conf
install -d -m 0755 /etc/systemd/system/math-school.service.d
if [ -f "$drop" ]; then cp -a "$drop" "$backup/storage.conf"; fi
printf '[Service]\nStateDirectory=math-school\nStateDirectoryMode=0750\n' >"$drop"
install -m 0644 "$tmp/server.py" /opt/math-school/server.py
install -m 0644 "$tmp/index.html" /opt/math-school/index.html
systemctl daemon-reload
rollback(){
 cp -a "$backup/server.py" "$backup/index.html" /opt/math-school/
 if [ -f "$backup/storage.conf" ]; then cp -a "$backup/storage.conf" "$drop"; else rm -f "$drop"; fi
 systemctl daemon-reload
 systemctl restart math-school || true
 echo "UPDATE FAILED. Previous version restored." >&2
 exit 1
}
systemctl restart math-school || rollback
ok=0
for i in $(seq 1 15); do
 if curl -fsS http://127.0.0.1:3178/health 2>/dev/null | grep -q '"version":"3.2"'; then ok=1; break; fi
 sleep 1
done
[ "$ok" = 1 ] || rollback
python3 - <<'PY' || rollback
import urllib.request,urllib.error,json
c=json.load(open('/etc/math-school/credentials.json'))
base='http://127.0.0.1:3178'
for role in ('student','parent'):
 req=urllib.request.Request(base+'/api/assignments',headers={'Cookie':'math_'+role+'='+c[role]['token']})
 with urllib.request.urlopen(req,timeout=5) as f: assert isinstance(json.load(f)['assignments'],list)
try:
 urllib.request.urlopen(base+'/api/assignments',timeout=5)
 raise AssertionError("unauthorized access")
except urllib.error.HTTPError as e: assert e.code==401
req=urllib.request.Request(base+'/api/assignments',data=b'{}',headers={'Cookie':'math_student='+c['student']['token'],'Content-Type':'application/json'})
try:
 urllib.request.urlopen(req,timeout=5)
 raise AssertionError("student can create tasks")
except urllib.error.HTTPError as e: assert e.code==403
print("ASSIGNMENT API TEST OK")
PY
echo "MATH SCHOOL 3.2 UPDATED"
echo "Backup: $backup"
curl -fsS http://127.0.0.1:3178/health
echo
