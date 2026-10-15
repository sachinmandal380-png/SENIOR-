#!/usr/bin/env python3
"""Independent QR IN Attendance System - standard-library local implementation."""
import hashlib, hmac, json, math, os, secrets, sqlite3, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get("ATTENDANCE_DB", BASE / "attendance.sqlite3"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
SITE_ID = "SITE-001"
SITE_LAT = 28.467466
SITE_LON = 77.026609
RADIUS_M = 500.0
MAX_ACCURACY_M = 500.0
MAX_AGE_SECONDS = 15.0
SESSION_TTL = 8 * 60 * 60
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

class PostgresConnection:
    """Adapt psycopg to the small subset of sqlite's API used by this app."""
    def __init__(self, raw, driver):
        self.raw = raw
        self.driver = driver
    def __enter__(self):
        self.raw.__enter__()
        return self
    def __exit__(self, exc_type, exc, tb):
        return self.raw.__exit__(exc_type, exc, tb)
    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        try:
            return self.raw.execute(sql, params)
        except self.driver.IntegrityError as exc:
            # PostgreSQL aborts the current transaction after a constraint error.
            self.raw.rollback()
            # Preserve the app's cross-database integrity-error handling.
            raise sqlite3.IntegrityError(str(exc)) from exc
    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)
    def commit(self):
        self.raw.commit()
    def close(self):
        self.raw.close()


def connect():
    if DATABASE_URL:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed; install requirements.txt") from exc
        from psycopg.rows import dict_row
        raw = psycopg.connect(DATABASE_URL, connect_timeout=10, row_factory=dict_row)
        return PostgresConnection(raw, psycopg)
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c

def now_utc():
    return datetime.now(timezone.utc)

def kolkata_parts(dt=None):
    from zoneinfo import ZoneInfo
    local = (dt or now_utc()).astimezone(ZoneInfo("Asia/Kolkata"))
    return local.date().isoformat(), local.isoformat(timespec="seconds")

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return salt.hex() + "$" + digest.hex()

def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000)
        return hmac.compare_digest(actual.hex(), digest_hex)
    except Exception:
        return False

def init_db():
    with connect() as c:
        schema = """
        CREATE TABLE IF NOT EXISTS admins(
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS people(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sites(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
          radius_m REAL NOT NULL, active INTEGER NOT NULL DEFAULT 0,
          verification_status TEXT NOT NULL DEFAULT 'pending_verification', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance(
          id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id),
          site_id TEXT NOT NULL REFERENCES sites(id), business_date TEXT NOT NULL,
          marked_at_utc TEXT NOT NULL, marked_at_ist TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'IN',
          UNIQUE(person_id, business_date)
        );
        CREATE TABLE IF NOT EXISTS audit_events(
          id INTEGER PRIMARY KEY, admin_id INTEGER REFERENCES admins(id), action TEXT NOT NULL,
          entity_type TEXT NOT NULL, entity_id TEXT, reason TEXT, details TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS security_events(
          id INTEGER PRIMARY KEY, event_type TEXT NOT NULL, site_id TEXT, details TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES admins(id),
          expires_at REAL NOT NULL
        );
        """
        if DATABASE_URL:
            schema = schema.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
            schema = schema.replace("person_id INTEGER", "person_id BIGINT")
            schema = schema.replace("admin_id INTEGER", "admin_id BIGINT")
            c.executescript(schema)
        else:
            c.executescript(schema)
        c.execute("""INSERT INTO sites
          (id,name,latitude,longitude,radius_m,active,verification_status,created_at)
          VALUES(?,?,?,?,?,0,'pending_verification',?) ON CONFLICT (id) DO NOTHING""",
          (SITE_ID, "Initial attendance site", SITE_LAT, SITE_LON, RADIUS_M, now_utc().isoformat()))
        c.commit()

def audit(c, admin_id, action, entity_type, entity_id=None, reason=None, details=None):
    c.execute("INSERT INTO audit_events(admin_id,action,entity_type,entity_id,reason,details,created_at) VALUES(?,?,?,?,?,?,?)",
              (admin_id, action, entity_type, str(entity_id) if entity_id is not None else None,
               reason, json.dumps(details or {}, ensure_ascii=False), now_utc().isoformat()))

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1, math.sqrt(a)))

def json_response(handler, status, obj, extra_headers=None):
    data = json.dumps(obj, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Content-Security-Policy", "default-src 'self' https:; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self';")
    for k,v in (extra_headers or {}).items(): handler.send_header(k,v)
    handler.end_headers()
    handler.wfile.write(data)

def page_response(handler, filename):
    data = (BASE / "static" / filename).read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(data)

class Handler(BaseHTTPRequestHandler):
    server_version = "IndependentAttendance/1.0"
    def log_message(self, fmt, *args):
        # Avoid logging request bodies or coordinates.
        print("%s - %s" % (self.address_string(), fmt % args))

    def body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 100_000: raise ValueError("Request too large")
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            raise ValueError("Invalid JSON request")

    def session_admin(self):
        token = self.headers.get("Cookie", "")
        token = next((x.split("=",1)[1] for x in token.split("; ") if x.startswith("admin_session=")), "")
        if not token: return None
        with connect() as c:
            row = c.execute("SELECT a.id,a.username FROM sessions s JOIN admins a ON a.id=s.admin_id WHERE s.token_hash=? AND s.expires_at>?",
                            (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def require_admin(self):
        admin = self.session_admin()
        if not admin:
            json_response(self, 401, {"error":"Admin login required"})
            return None
        return admin

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/": return page_response(self, "index.html")
        if path == "/admin": return page_response(self, "admin.html")
        if path == "/health": return json_response(self, 200, {"status":"ok"})
        if path == "/api/setup-status":
            with connect() as c:
                n = c.execute("SELECT COUNT(*) AS n FROM admins").fetchone()["n"]
            return json_response(self, 200, {"setup_required": n == 0, "setup_key_required": bool(DATABASE_URL)})
        if path == "/api/sites/public":
            with connect() as c:
                rows = c.execute("SELECT id,name,active,verification_status FROM sites").fetchall()
            return json_response(self, 200, {"sites":[dict(x) for x in rows]})
        if path == "/api/admin/me":
            a = self.require_admin()
            if a: return json_response(self, 200, {"admin":a})
            return
        if path.startswith("/api/admin/"):
            a = self.require_admin()
            if not a: return
            with connect() as c:
                if path == "/api/admin/overview":
                    return json_response(self, 200, {
                      "people": c.execute("SELECT COUNT(*) AS n FROM people WHERE active=1").fetchone()["n"],
                      "today": c.execute("SELECT COUNT(*) AS n FROM attendance WHERE business_date=?", (kolkata_parts()[0],)).fetchone()["n"],
                      "sites": [dict(r) for r in c.execute("SELECT * FROM sites ORDER BY id")],
                      "people_list": [dict(r) for r in c.execute("SELECT id,name,active,created_at FROM people ORDER BY name")],
                      "attendance": [dict(r) for r in c.execute("""SELECT a.id,p.name,a.site_id,a.business_date,a.marked_at_ist,a.status
                        FROM attendance a JOIN people p ON p.id=a.person_id ORDER BY a.id DESC LIMIT 500""")],
                      "audit": [dict(r) for r in c.execute("SELECT id,action,entity_type,entity_id,reason,created_at FROM audit_events ORDER BY id DESC LIMIT 100")]
                    })
                if path == "/api/admin/export":
                    qs = parse_qs(urlparse(self.path).query)
                    start = qs.get("start", [""])[0]; end = qs.get("end", [""])[0]
                    rows = c.execute("""SELECT p.name,a.site_id,a.business_date,a.marked_at_ist,a.status FROM attendance a
                        JOIN people p ON p.id=a.person_id WHERE (?='' OR a.business_date>=?) AND (?='' OR a.business_date<=?)
                        ORDER BY a.business_date DESC,a.marked_at_ist DESC""",(start,start,end,end)).fetchall()
                    import csv, io
                    buf=io.StringIO(); w=csv.writer(buf); w.writerow(["Name","Site","Date","Timestamp (Asia/Kolkata)","Status"])
                    for r in rows:
                        # Prevent spreadsheet formula injection.
                        vals=[]
                        for v in (r["name"], r["site_id"], r["business_date"], r["marked_at_ist"], r["status"]):
                            s=str(v)
                            vals.append("'" + s if s.startswith(("=","+","-","@","\t","\r")) else s)
                        w.writerow(vals)
                    data=buf.getvalue().encode("utf-8-sig")
                    self.send_response(200); self.send_header("Content-Type","text/csv; charset=utf-8")
                    self.send_header("Content-Disposition",'attachment; filename="attendance.csv"')
                    self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
            return json_response(self, 404, {"error":"Not found"})
        return json_response(self, 404, {"error":"Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try: data = self.body()
        except ValueError as e: return json_response(self, 400, {"error":str(e)})
        with connect() as c:
            admin_count = c.execute("SELECT COUNT(*) AS n FROM admins").fetchone()["n"]
        if path == "/api/setup":
            if admin_count:
                return json_response(self, 409, {"error":"Admin is already configured"})
            if DATABASE_URL:
                expected_key = os.environ.get("ADMIN_SETUP_KEY", "")
                supplied_key = str(data.get("setup_key", ""))
                if len(expected_key) < 24:
                    return json_response(self, 503, {"error":"Set ADMIN_SETUP_KEY to a random secret of at least 24 characters in the hosting environment before creating the first admin"})
                if not hmac.compare_digest(supplied_key, expected_key):
                    return json_response(self, 403, {"error":"Invalid admin setup key"})
            username = str(data.get("username","")).strip()
            password = str(data.get("password",""))
            if len(username) < 3 or len(password) < 12:
                return json_response(self, 400, {"error":"Username must be at least 3 characters and password at least 12 characters"})
            with connect() as c:
                c.execute("INSERT INTO admins(username,password_hash,created_at) VALUES(?,?,?)",
                          (username,hash_password(password),now_utc().isoformat()))
                c.commit()
            return json_response(self, 201, {"message":"Admin created. Sign in to continue."})
        if path == "/api/login":
            username = str(data.get("username","")).strip()
            password = str(data.get("password",""))
            with connect() as c:
                a = c.execute("SELECT id,username,password_hash FROM admins WHERE username=?", (username,)).fetchone()
                if not a or not verify_password(password,a["password_hash"]):
                    return json_response(self, 401, {"error":"Invalid credentials"})
                token = secrets.token_urlsafe(32)
                c.execute("INSERT INTO sessions(token_hash,admin_id,expires_at) VALUES(?,?,?)",
                          (hashlib.sha256(token.encode()).hexdigest(),a["id"],time.time()+SESSION_TTL))
                c.commit()
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
            return json_response(self, 200, {"message":"Signed in"}, {"Set-Cookie":f"admin_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}{secure}"})
        if path == "/api/logout":
            raw = self.headers.get("Cookie","")
            token = next((x.split("=",1)[1] for x in raw.split("; ") if x.startswith("admin_session=")), "")
            with connect() as c:
                if token: c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
                c.commit()
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
            return json_response(self, 200, {"message":"Signed out"}, {"Set-Cookie":f"admin_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{secure}"})
        if path == "/api/attendance":
            name = " ".join(str(data.get("name","")).split())
            site_id = str(data.get("site_id","")).strip()
            try:
                lat=float(data.get("latitude")); lon=float(data.get("longitude"))
                accuracy=float(data.get("accuracy")); age=float(data.get("age_seconds"))
            except (TypeError,ValueError):
                return json_response(self,400,{"error":"A valid fresh location reading is required"})
            if not all(math.isfinite(x) for x in (lat,lon,accuracy,age)) or not (-90<=lat<=90 and -180<=lon<=180):
                return json_response(self,400,{"error":"Invalid location values"})
                           if accuracy < 0 or accuracy > MAX_ACCURACY_M:
                return json_response(self,403,{"error":f"GPS accuracy must be {MAX_ACCURACY_M:g} metres or better. Move to an area with a clearer GPS signal and retry."})
                return json_response(self,403,{"error":"Location reading is older than 15 seconds. Retry location capture."})
            with connect() as c:
                site=c.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
                if not site or not site["active"] or site["verification_status"]!="verified":
                    return json_response(self,403,{"error":"This attendance site is not active and verified"})
                person=c.execute("SELECT * FROM people WHERE normalized_name=? AND active=1",(name.casefold(),)).fetchone()
                if not person:
                    return json_response(self,403,{"error":"Name is not on the approved active list"})
                dist=haversine_m(lat,lon,site["latitude"],site["longitude"])
                if dist > site["radius_m"]:
                    c.execute("INSERT INTO security_events(event_type,site_id,details,created_at) VALUES(?,?,?,?)",
                              ("geofence_rejected",site_id,json.dumps({"distance_m":round(dist,2)}),now_utc().isoformat()))
                    c.commit()
                    return json_response(self,403,{"error":"You are outside the allowed 10-metre attendance area"})
                day, ist=kolkata_parts()
                utc=now_utc().isoformat()
                try:
                    c.execute("INSERT INTO attendance(person_id,site_id,business_date,marked_at_utc,marked_at_ist,status) VALUES(?,?,?,?,?,'IN')",
                              (person["id"],site_id,day,utc,ist))
                    c.commit()
                except sqlite3.IntegrityError:
                    return json_response(self,409,{"error":"IN attendance is already marked for this person today"})
                return json_response(self,201,{"message":"IN attendance marked successfully","name":person["name"],"site":site["name"],"date":day,"time":ist,"status":"IN"})
        admin = self.require_admin()
        if not admin: return
        with connect() as c:
            if path == "/api/admin/people":
                name=" ".join(str(data.get("name","")).split())
                if len(name)<2 or len(name)>100: return json_response(self,400,{"error":"Name must contain 2 to 100 characters"})
                norm=name.casefold()
                try:
                    created_now = now_utc().isoformat()
                    if DATABASE_URL:
                        cur=c.execute("INSERT INTO people(name,normalized_name,active,created_at,updated_at) VALUES(?,?,1,?,?) RETURNING id",
                                      (name,norm,created_now,created_now))
                        person_id = cur.fetchone()["id"]
                    else:
                        cur=c.execute("INSERT INTO people(name,normalized_name,active,created_at,updated_at) VALUES(?,?,1,?,?)",
                                      (name,norm,created_now,created_now))
                        person_id = cur.lastrowid
                    audit(c,admin["id"],"person_added","person",person_id,details={"name":name}); c.commit()
                    return json_response(self,201,{"message":"Approved person added"})
                except sqlite3.IntegrityError:
                    return json_response(self,409,{"error":"A person with that name already exists"})
            if path.startswith("/api/admin/people/") and path.endswith("/toggle"):
                pid=path.split("/")[-2]
                row=c.execute("SELECT * FROM people WHERE id=?",(pid,)).fetchone()
                if not row: return json_response(self,404,{"error":"Person not found"})
                active=0 if row["active"] else 1
                c.execute("UPDATE people SET active=?,updated_at=? WHERE id=?",(active,now_utc().isoformat(),pid))
                audit(c,admin["id"],"person_status_changed","person",pid,details={"active":active}); c.commit()
                return json_response(self,200,{"message":"Person status updated"})
            if path == "/api/admin/site/activate":
                # Explicit confirmation that the owner has verified the physical site coordinates.
                if data.get("confirm") is not True: return json_response(self,400,{"error":"Explicit site verification confirmation required"})
                site_id=str(data.get("site_id",SITE_ID))
                row=c.execute("SELECT * FROM sites WHERE id=?",(site_id,)).fetchone()
                if not row: return json_response(self,404,{"error":"Site not found"})
                c.execute("UPDATE sites SET active=1,verification_status='verified' WHERE id=?",(site_id,))
                audit(c,admin["id"],"site_activated","site",site_id,reason="Owner confirmed on-site verification")
                c.commit()
                return json_response(self,200,{"message":"Site activated. Confirm coordinates are correct before use."})
            if path == "/api/admin/site/add":
                sid=str(data.get("id","")).strip().upper()
                name=str(data.get("name","")).strip()
                try: lat=float(data["latitude"]); lon=float(data["longitude"]); radius=float(data.get("radius_m",2))
                except Exception: return json_response(self,400,{"error":"Valid coordinates are required"})
                if not sid or sid=="SITE-001" or not name or not (-90<=lat<=90) or not (-180<=lon<=180) or not (0.1<=radius<=100):
                    return json_response(self,400,{"error":"Invalid site settings"})
                try:
                    c.execute("INSERT INTO sites(id,name,latitude,longitude,radius_m,active,verification_status,created_at) VALUES(?,?,?,?,?,0,'pending_verification',?)",
                              (sid,name,lat,lon,radius,now_utc().isoformat()))
                    audit(c,admin["id"],"site_added","site",sid,details={"name":name,"radius_m":radius}); c.commit()
                    return json_response(self,201,{"message":"Site created pending verification"})
                except sqlite3.IntegrityError: return json_response(self,409,{"error":"Site ID already exists"})
            if path == "/api/admin/attendance/correct":
                try: aid=int(data["attendance_id"]); reason=str(data["reason"]).strip()
                except Exception: return json_response(self,400,{"error":"Attendance ID and reason are required"})
                if len(reason)<5: return json_response(self,400,{"error":"Correction reason must be at least 5 characters"})
                row=c.execute("SELECT * FROM attendance WHERE id=?",(aid,)).fetchone()
                if not row: return json_response(self,404,{"error":"Attendance not found"})
                c.execute("DELETE FROM attendance WHERE id=?",(aid,))
                audit(c,admin["id"],"attendance_removed","attendance",aid,reason=reason,details={"previous_date":row["business_date"],"person_id":row["person_id"]})
                c.commit()
                return json_response(self,200,{"message":"Attendance removed and audit event recorded"})
        return json_response(self,404,{"error":"Not found"})

    def do_PATCH(self):
        path=urlparse(self.path).path
        admin=self.require_admin()
        if not admin:return
        try:data=self.body()
        except ValueError as e:return json_response(self,400,{"error":str(e)})
        with connect() as c:
            if path.startswith("/api/admin/site/"):
                sid=path.rsplit("/",1)[-1]
                try: lat=float(data["latitude"]); lon=float(data["longitude"]); radius=float(data["radius_m"])
                except Exception:return json_response(self,400,{"error":"Invalid site settings"})
                if not(-90<=lat<=90 and -180<=lon<=180 and 0.1<=radius<=100):return json_response(self,400,{"error":"Coordinates or radius out of range"})
                cur=c.execute("UPDATE sites SET latitude=?,longitude=?,radius_m=?,active=0,verification_status='pending_verification' WHERE id=?",(lat,lon,radius,sid))
                if cur.rowcount==0:return json_response(self,404,{"error":"Site not found"})
                audit(c,admin["id"],"site_changed","site",sid,reason="Coordinate changes require re-verification");c.commit()
                return json_response(self,200,{"message":"Site updated and disabled pending re-verification"})
        return json_response(self,404,{"error":"Not found"})

def main():
    init_db()
    print(f"Independent QR IN Attendance System listening on http://{HOST}:{PORT}")
    print("Initial site is disabled pending on-site verification.")
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

if __name__ == "__main__": main()
