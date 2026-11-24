#!/usr/bin/env python3
import os, time, json, secrets, hmac, threading, html, base64, binascii, tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads(Path(os.environ.get("MATH_CONFIG", "/etc/math-school/credentials.json")).read_text())
PAGE = (ROOT / "index.html").read_bytes()
LOCK = threading.RLock()
LIVE = {"snapshot": None, "student": 0.0, "parent": 0.0, "paused": False, "commands": []}
TTL = 18
MAX = 6 * 1024 * 1024
ASSIGN_FILE = Path(os.environ.get("MATH_ASSIGNMENTS", "/var/lib/math-school/assignments.json"))
ASSIGNMENTS = json.loads(ASSIGN_FILE.read_text()) if ASSIGN_FILE.exists() else []
ASSIGN_BASE = 4000000000

def public_assignments():
    return [{k:v for k,v in a.items() if k != "photo"} | {"hasPhoto": bool(a.get("photo"))} for a in ASSIGNMENTS]

def save_assignments(items):
    fd, name = tempfile.mkstemp(dir=ASSIGN_FILE.parent, prefix=".assignments-")
    try:
        with os.fdopen(fd,"w") as f:
            json.dump(items,f,ensure_ascii=False,separators=(",",":"))
            f.flush(); os.fsync(f.fileno())
        os.replace(name,ASSIGN_FILE)
    finally:
        if os.path.exists(name): os.unlink(name)

def new_assignment(d):
    title=d.get("title")
    rows=d.get("tasks")
    photo=d.get("photo","")
    if not isinstance(title,str) or not 1<=len(title.strip())<=120 or not isinstance(rows,list) or not 1<=len(rows)<=12 or not isinstance(photo,str): raise ValueError("invalid assignment")
    if photo:
        if len(photo)>3000000: raise ValueError("photo too large")
        raw=base64.b64decode(photo,validate=True)
        if len(raw)>2200000 or not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"): raise ValueError("invalid JPEG")
    tasks=[]
    for row in rows:
        if not isinstance(row,dict): raise ValueError("invalid task")
        clean={}
        for key,n in (("text",2000),("answer",180),("hint",1000)):
            value=row.get(key,"")
            if not isinstance(value,str) or len(value)>n: raise ValueError("invalid "+key)
            clean[key]=value.strip()
        if not clean["text"] and not photo: raise ValueError("empty task")
        tasks.append(clean)
    if len(ASSIGNMENTS)>=50: raise ValueError("maximum 50 assignments")
    seed=max([ASSIGN_BASE]+[a["seed"] for a in ASSIGNMENTS])+1
    return {"seed":seed,"title":title.strip(),"created":int(time.time()),"tasks":tasks,"photo":photo}


def active(now):
    if now - LIVE["student"] > TTL:
        LIVE["snapshot"] = None
        LIVE["commands"].clear()
        LIVE["paused"] = False
    return LIVE["snapshot"] is not None and not LIVE["paused"]

def valid_snapshot(s):
    if not isinstance(s, dict) or s.get("kind") != "snapshot":
        return False
    if type(s.get("seed")) is not int or not 0 <= s["seed"] <= 4294967295:
        return False
    if type(s.get("index")) is not int or not 0 <= s["index"] < 12:
        return False
    r = s.get("record")
    if not isinstance(r, dict):
        return False
    if not isinstance(r.get("values"), list) or len(r["values"]) > 6:
        return False
    if not all(v is None or isinstance(v, str) and len(v) <= 180 for v in r["values"]):
        return False
    if not isinstance(r.get("checked"), list) or len(r["checked"]) > 6:
        return False
    if not isinstance(r.get("notes", ""), str) or len(r.get("notes", "")) > 10000:
        return False
    for name, limit in (("strokes", 1200), ("teacher", 300)):
        a = r.get(name, [])
        if not isinstance(a, list) or len(a) > limit:
            return False
        for stroke in a:
            if not isinstance(stroke, dict) or not isinstance(stroke.get("pts"), list) or len(stroke["pts"]) > 4000:
                return False
            if not all(isinstance(p, list) and len(p)==2 and type(p[0]) in (int,float) and type(p[1]) in (int,float) and 0<=p[0]<=900 and 0<=p[1]<=600 for p in stroke["pts"]):
                return False
    return isinstance(r.get("teacherNote", ""), str) and len(r.get("teacherNote", "")) <= 1000

def valid_command(m):
    if not isinstance(m, dict):
        return False
    if type(m.get("seed")) is not int or type(m.get("index")) is not int:
        return False
    k = m.get("kind")
    if k == "teacherUndo":
        return True
    if k == "teacherNote":
        return isinstance(m.get("note"), str) and len(m["note"]) <= 1000
    if k != "teacherStroke":
        return False
    s = m.get("stroke")
    if not isinstance(s, dict) or not isinstance(s.get("id"), str) or len(s["id"]) != 32 or any(c not in "0123456789abcdef" for c in s["id"]):
        return False
    pts = s.get("pts")
    return isinstance(pts, list) and 0 < len(pts) <= 4000 and all(isinstance(p, list) and len(p)==2 and type(p[0]) in (int,float) and type(p[1]) in (int,float) and 0<=p[0]<=900 and 0<=p[1]<=600 for p in pts)

def login_page(role, error=False):
    label = "Учебник ученика" if role == "student" else "Кабинет родителя"
    warning = '<p style="color:#a32">Неверный пароль.</p>' if error else ""
    note = "После входа текущие записи будут видны родителю, пока учебник открыт. Наблюдение можно приостановить." if role == "student" else "Здесь автоматически появляется работа ученика. Вход потребуется только один раз в этом браузере."
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Math School — {label}</title><style>body{{background:#edf2f7;font:18px/1.5 system-ui;color:#19334c;margin:0;padding:20px}}main{{max-width:460px;margin:10vh auto;background:white;border-radius:18px;padding:26px;box-shadow:0 8px 35px #19334c14}}input,button{{box-sizing:border-box;width:100%;font:inherit;padding:14px;border-radius:9px;border:1px solid #b8c6d5;margin:8px 0}}button{{background:#195990;color:white;border:0}}a{{color:#195990}}</style><main><h1>{label}</h1><p>{note}</p>{warning}<form action="/login/{role}" method="post"><label>Пароль<input type="password" name="password" required autocomplete="current-password"></label><button>Войти и запомнить устройство</button></form><p><a href="/student">Ученик</a> · <a href="/parent">Родитель</a></p></main></html>""".encode()

class Handler(BaseHTTPRequestHandler):
    server_version = "MathSchool"
    def log_message(self, *args):
        pass
    def send(self, code, data=b"", typ="application/json; charset=utf-8", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)
    def j(self, code, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode())
    def authorized(self, role):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            v = cookie.get("math_"+role)
            return bool(v and hmac.compare_digest(v.value.encode(), CONFIG[role]["token"].encode()))
        except Exception:
            return False
    def redirect(self, path, headers=()):
        self.send(303, b"", headers=[("Location",path), *headers])
    def body(self, maximum=MAX):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError()
        if not 0 < n <= maximum:
            raise ValueError()
        return self.rfile.read(n)
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            return self.redirect("/student")
        if path == "/health":
            return self.j(200, {"ok":True,"version":"3.2"})
        if path in ("/student","/parent"):
            role = path[1:]
            return self.send(200, PAGE if self.authorized(role) else login_page(role), "text/html; charset=utf-8")
        if path == "/logout":
            if self.authorized("student"):
                with LOCK:
                    LIVE.update(snapshot=None, commands=[], student=0.0, paused=False)
            return self.redirect("/student",[("Set-Cookie", "math_"+r+"=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict") for r in ("student","parent")])
        if path == "/api/assignments" or path.startswith("/api/assignment-photo/"):
            if not (self.authorized("student") or self.authorized("parent")):
                return self.j(401,{"error":"login"})
            with LOCK:
                if path == "/api/assignments": return self.j(200,{"assignments":public_assignments()})
                a=next((a for a in ASSIGNMENTS if str(a["seed"])==path.rsplit("/",1)[1]),None)
                if not a or not a.get("photo"): return self.j(404,{"error":"photo"})
                return self.send(200,base64.b64decode(a["photo"]),"image/jpeg")
        if path == "/api/parent":
            if not self.authorized("parent"):
                return self.j(401, {"error":"login"})
            with LOCK:
                now=time.monotonic()
                LIVE["parent"]=now
                online=active(now)
                return self.j(200, {"snapshot":LIVE["snapshot"] if online else None,"paused":LIVE["paused"] and now-LIVE["student"]<=TTL})
        self.j(404, {"error":"not found"})
    def do_POST(self):
        path=urlsplit(self.path).path
        # State changes from a cross-origin browser are rejected; role cookies are Strict.
        if self.headers.get("Sec-Fetch-Site") not in (None,"same-origin","none"):
            return self.j(403, {"error":"origin"})
        if path in ("/login/student","/login/parent"):
            role=path.rsplit("/",1)[1]
            try:
                pw=parse_qs(self.body(4096).decode()).get("password",[""])[0]
            except Exception:
                pw=""
            if not hmac.compare_digest(pw.encode(), CONFIG[role]["password"].encode()):
                time.sleep(0.4)
                return self.send(401,login_page(role,True),"text/html; charset=utf-8")
            return self.redirect("/"+role,[("Set-Cookie","math_"+role+"="+CONFIG[role]["token"]+"; Path=/; Max-Age=15552000; Secure; HttpOnly; SameSite=Strict")])
        if path == "/api/assignments":
            if not self.authorized("parent"): return self.j(403,{"error":"parent only"})
            if self.headers.get("Content-Type","").split(";")[0]!="application/json": return self.j(415,{"error":"content type"})
            try:
                d=json.loads(self.body())
                if not isinstance(d,dict): raise ValueError("body")
                with LOCK:
                    a=new_assignment(d)
                    items=ASSIGNMENTS+[a]
                    save_assignments(items)
                    ASSIGNMENTS.append(a)
                    return self.j(201,{"seed":a["seed"],"assignments":public_assignments()})
            except (ValueError,TypeError,binascii.Error) as e:
                return self.j(400,{"error":str(e)})
            except OSError:
                return self.j(500,{"error":"storage"})
        if path not in ("/api/student","/api/parent"):
            return self.j(404,{"error":"not found"})
        role=path.rsplit("/",1)[1]
        if not self.authorized(role):
            return self.j(401,{"error":"login"})
        if self.headers.get("Content-Type","").split(";")[0]!="application/json":
            return self.j(415,{"error":"content type"})
        try:
            d=json.loads(self.body())
            if not isinstance(d,dict):
                raise ValueError()
        except Exception:
            return self.j(400,{"error":"body"})
        with LOCK:
            now=time.monotonic()
            active(now)
            if role=="student":
                pause=d.get("paused") is True
                snap=d.get("snapshot")
                if not pause and (not valid_snapshot(snap) or (ASSIGN_BASE<snap["seed"]<=ASSIGN_BASE+50 and not any(a["seed"]==snap["seed"] and snap["index"]<len(a["tasks"]) for a in ASSIGNMENTS))):
                    return self.j(400,{"error":"snapshot"})
                a=d.get("ack",[])
                if not isinstance(a,list) or len(a)>400 or not all(isinstance(x,str) for x in a):
                    return self.j(400,{"error":"ack"})
                old=LIVE["snapshot"]
                if pause or not snap.get("canAnnotate") or (old and (old["seed"],old["index"])!=(snap["seed"],snap["index"])):
                    LIVE["commands"].clear()
                LIVE["commands"]=[x for x in LIVE["commands"] if x["id"] not in a]
                LIVE.update(snapshot=None if pause else snap,student=now,paused=pause)
                return self.j(200,{"parentOnline":now-LIVE["parent"]<=TTL,"commands":LIVE["commands"]})
            if not valid_command(d):
                return self.j(400,{"error":"command"})
            s=LIVE["snapshot"]
            if not active(now) or not s or not s.get("canAnnotate") or (s["seed"],s["index"])!=(d["seed"],d["index"]):
                return self.j(409,{"error":"not permitted"})
            if len(LIVE["commands"])>=200:
                return self.j(429,{"error":"queue"})
            LIVE["commands"].append({"id":secrets.token_hex(12),"msg":d})
            LIVE["parent"]=now
            return self.j(200,{"ok":True})

if __name__=="__main__":
    ThreadingHTTPServer(("127.0.0.1", 3178), Handler).serve_forever()
