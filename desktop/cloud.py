"""مزامنة التذكيرات مع Supabase، عشان تكون نفسها على الكمبيوتر والماك والجوال.

الملف المحلي يظل نسخة على الجهاز: لو انقطع النت البرنامج يشتغل عادي،
والتعديلات تنتظر في الطابور لين يرجع النت.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

URL = "https://cdbegnazbfqzkpvyhvzf.supabase.co"
KEY = "sb_publishable_d5FCnmtNNXrRDb6NRD4yow__DUo-ICt"
PULL_EVERY = 4  # ثواني


class AuthError(Exception):
    pass


def _request(method, path, body=None, token=None, headers=None):
    h = {"apikey": KEY, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            info = json.loads(e.read())
        except ValueError:
            info = {}
        msg = info.get("msg") or info.get("error_description") or info.get("message") or str(e)
        if e.code in (400, 401, 403, 422):
            raise AuthError(msg) from None
        raise OSError(msg) from None


def auth_message(err):
    """رسالة الخطأ من Supabase بالعربي."""
    m = str(err).lower()
    if "invalid login" in m:
        return "الإيميل أو كلمة المرور غلط"
    if "not confirmed" in m:
        return "لازم تأكد إيميلك أول: افتح الرسالة واضغط الرابط"
    if "already registered" in m:
        return "الإيميل مسجّل من قبل، اضغط دخول"
    if "password" in m and ("6" in m or "short" in m or "weak" in m):
        return "كلمة المرور قصيرة، خلها 6 حروف أو أكثر"
    if "rate limit" in m:
        return "محاولات كثيرة، انتظر شوي وجرب"
    return str(err)


class Cloud:
    def __init__(self, app_dir):
        self.session_file = os.path.join(app_dir, "session.json")
        self.state_file = os.path.join(app_dir, "sync_state.json")
        self.lock = threading.Lock()
        self.session = self._read(self.session_file, None)
        st = self._read(self.state_file, {})
        self.since = st.get("since")          # آخر وقت سحبنا فيه من السيرفر
        self.queue = st.get("queue", {})      # id -> البيانات، أو None لو انحذف
        self.known = st.get("known")          # آخر نسخة معروفة من كل عنصر، عشان نعرف وش تغيّر
        self.on_remote = None                 # تنادى بالعناصر الجاية من السيرفر
        self.on_logout = None
        self.online = False
        self._wake = threading.Event()
        self._thread = None

    # ---------- ملفات ----------
    @staticmethod
    def _read(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write(path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _save_state(self):
        self._write(self.state_file, {"since": self.since, "queue": self.queue, "known": self.known})

    # ---------- الدخول ----------
    @property
    def logged_in(self):
        return bool(self.session)

    @property
    def email(self):
        return (self.session or {}).get("email", "")

    def _store_session(self, res):
        self.session = {
            "access_token": res["access_token"],
            "refresh_token": res["refresh_token"],
            "expires_at": time.time() + res.get("expires_in", 3600),
            "email": res.get("user", {}).get("email", ""),
        }
        self._write(self.session_file, self.session)

    def login(self, email, password):
        res = _request("POST", "/auth/v1/token?grant_type=password",
                       {"email": email, "password": password})
        self._store_session(res)

    def signup(self, email, password):
        """يرجّع True لو دخل على طول، أو False لو لازم يأكد الإيميل أول."""
        res = _request("POST", "/auth/v1/signup", {"email": email, "password": password})
        if res and res.get("access_token"):
            self._store_session(res)
            return True
        return False

    def change_password(self, password):
        _request("PUT", "/auth/v1/user", {"password": password}, self._token())

    def logout(self):
        with self.lock:
            self.session = None
            self.since = None
            self.known = None
            self.queue = {}
            for p in (self.session_file, self.state_file):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _token(self):
        s = self.session
        if not s:
            raise AuthError("not logged in")
        if s["expires_at"] - time.time() < 120:
            try:
                res = _request("POST", "/auth/v1/token?grant_type=refresh_token",
                               {"refresh_token": s["refresh_token"]})
            except AuthError:
                self.session = None
                try:
                    os.remove(self.session_file)
                except OSError:
                    pass
                raise
            self._store_session(res)
        return self.session["access_token"]

    # ---------- تتبع التعديلات (من الواجهة) ----------
    def track(self, items):
        """تنادى بعد كل حفظ: تقارن بآخر نسخة وتحط اللي تغيّر في الطابور."""
        with self.lock:
            now = {i["id"]: json.dumps(i, ensure_ascii=False, sort_keys=True) for i in items}
            if self.known is None:
                # أول مزامنة على هذا الجهاز: نرفع كل اللي عندنا
                changed, removed = list(now), []
            else:
                changed = [k for k, v in now.items() if self.known.get(k) != v]
                removed = [k for k in self.known if k not in now]
            for k in changed:
                self.queue[k] = json.loads(now[k])
            for k in removed:
                self.queue[k] = None
            self.known = now
            self._save_state()
        if changed or removed:
            self._wake.set()

    def accept_remote(self, items):
        """بعد ما الواجهة تطبّق اللي جا من السيرفر، نعتبره معروف عشان ما نرجّعه."""
        with self.lock:
            self.known = {i["id"]: json.dumps(i, ensure_ascii=False, sort_keys=True) for i in items}
            self._save_state()

    # ---------- الخيط اللي يزامن ----------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        self._wake.set()

    def _loop(self):
        while True:
            self._wake.wait(PULL_EVERY)
            self._wake.clear()
            if not self.session:
                continue
            try:
                self._push()
                self._pull()
                self.online = True
            except AuthError:
                if not self.session and self.on_logout:
                    self.on_logout()
            except (OSError, ValueError):
                self.online = False

    def _push(self):
        with self.lock:
            batch = dict(self.queue)
        if not batch:
            return
        rows = [{"id": k, "data": v or {}, "deleted": v is None} for k, v in batch.items()]
        _request("POST", "/rest/v1/items?on_conflict=id", rows, self._token(),
                 {"Prefer": "resolution=merge-duplicates,return=minimal"})
        with self.lock:
            for k, v in batch.items():
                if self.queue.get(k, "missing") == v:  # ما تغيّر وإحنا نرفع
                    del self.queue[k]
            self._save_state()

    def _pull(self):
        q = "select=id,data,deleted,updated_at&order=updated_at.asc"
        if self.since:
            q += "&updated_at=gt." + urllib.parse.quote(self.since)
        rows = _request("GET", "/rest/v1/items?" + q, token=self._token()) or []
        if not rows:
            return
        with self.lock:
            self.since = max(r["updated_at"] for r in rows)
            # اللي عندنا تعديل محلي عليه لسا ما انرفع، نخليه (المحلي أحدث)
            rows = [r for r in rows if r["id"] not in self.queue]
            self._save_state()
        if rows and self.on_remote:
            self.on_remote([(r["id"], None if r["deleted"] else r["data"]) for r in rows])
