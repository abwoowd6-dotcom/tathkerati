import calendar
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from datetime import datetime, timedelta

import customtkinter as ctk
from PIL import Image, ImageTk

IS_MAC = sys.platform == "darwin"
if IS_MAC:
    import fcntl
else:
    import ctypes
    import winreg
    import winsound
    import pystray

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)
import cloud  # noqa: E402

CLOUD = cloud.Cloud(APP_DIR)
DATA_FILE = os.path.join(APP_DIR, "reminders.json")
ICON_FILE = os.path.join(APP_DIR, "icon.ico")
ICON_PNG = os.path.join(APP_DIR, "icon.png")
SHOW_FLAG = os.path.join(APP_DIR, ".show")
WIDGET_FILE = os.path.join(APP_DIR, "widget.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Reminders"
FONT = "SF Arabic" if IS_MAC else "Segoe UI"
OS_NAME = "الماك" if IS_MAC else "الويندوز"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.abwoo.tathkerati.plist")

if not IS_MAC:
    # عشان تطلع أيقونة البرنامج في شريط المهام بدل أيقونة بايثون
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("abwoo.reminders")

BG = "#0f1117"
CARD = "#181b24"
CARD_HOVER = "#1f2330"
FIELD = "#232736"
FIELD_HOVER = "#2d3246"
TEXT = "#f3f4f8"
MUTED = "#7d8398"
ACCENT = "#6c8cff"
ACCENT_HOVER = "#5a78ee"
GREEN = "#34c98b"
RED = "#ff6b7a"
URGENT = "#ff8a4c"
KEY = "systemTransparent" if IS_MAC else "#010101"  # لون شفاف لزوايا الإشعار

PROJECT_COLORS = ["#8b9dff", "#5bb8f5", "#4cc9a0", "#f5a25d", "#f28bb0", "#b18cff"]
PROJECT_COLOR_NAMES = ["بنفسجي فاتح", "أزرق", "أخضر", "برتقالي", "وردي", "بنفسجي"]

DAYS = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
          "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def font(size=13, weight="normal", **kw):
    return ctk.CTkFont(family=FONT, size=size, weight=weight, **kw)


def load():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(items, sync=True):
    # نكتب لملف مؤقت ثم نستبدل، عشان ما يخرب الملف لو انقطع الكهرب وقت الحفظ
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)
    if sync and CLOUD.logged_in:
        CLOUD.track(items)


def kind(it):
    return it.get("kind", "reminder")


def due_dt(item):
    return datetime.strptime(f"{item['date']} {item['time']}", "%Y-%m-%d %H:%M")


def end_dt(item):
    """نهاية فترة التذكير، أو None لو ما له نهاية. لو النهاية قبل البداية تكون باليوم اللي بعده."""
    if not item.get("end"):
        return None
    e = datetime.strptime(f"{item['date']} {item['end']}", "%Y-%m-%d %H:%M")
    return e if e > due_dt(item) else e + timedelta(days=1)


WARN_BEFORE = (30, 15, 5)  # دقائق قبل النهاية نرسل فيها تنبيه "لم تنفذ"


def settings_of(items):
    """إعدادات التنبيهات اللي تنضبط من الجوال وتتزامن هنا."""
    s = next((i for i in items if kind(i) == "settings"), {})
    return {"warn": sorted(s.get("warn", WARN_BEFORE), reverse=True), "end_alert": s.get("end_alert", True)}


def stages(item, settings=None):
    """مراحل التنبيه لتذكير له فترة: البداية، ثم قبل النهاية، ثم النهاية."""
    settings = settings or {"warn": WARN_BEFORE, "end_alert": True}
    s, e = due_dt(item), end_dt(item)
    out = [("start", s)]
    out += [(f"w{m}", e - timedelta(minutes=m)) for m in settings["warn"] if e - timedelta(minutes=m) > s]
    if settings["end_alert"]:
        out.append(("end", e))
    return out


def dur_text(td):
    mins = max(0, int(td.total_seconds() // 60) + 1)
    if mins < 60:
        return f"{mins} د"
    h, m = divmod(mins, 60)
    return f"{h} س" + (f" و {m} د" if m else "")


def fmt12(t):
    h, m = map(int, t.split(":"))
    return f"{h % 12 or 12}:{m:02d} {'ص' if h < 12 else 'م'}"


def day_label(date_str):
    today = datetime.now().date()
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    diff = (d - today).days
    if diff == 0:
        return "اليوم"
    if diff == 1:
        return "بكرا"
    if diff == -1:
        return "أمس"
    return f"{d.day} {MONTHS[d.month - 1]}"


# ---------- تشغيل مع الجهاز ----------
def startup_command():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw}" "{os.path.abspath(__file__)}" --tray'


MAC_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.abwoo.tathkerati</string>
  <key>ProgramArguments</key><array>
    <string>{python}</string><string>{script}</string><string>--tray</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
"""


def startup_enabled():
    if IS_MAC:
        return os.path.exists(LAUNCH_AGENT)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_startup(on):
    if IS_MAC:
        # LaunchAgent يشغّل البرنامج أول ما تسجّل دخول على الماك
        if on:
            os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
            with open(LAUNCH_AGENT, "w") as f:
                f.write(MAC_PLIST.format(python=sys.executable, script=os.path.abspath(__file__)))
        elif os.path.exists(LAUNCH_AGENT):
            os.remove(LAUNCH_AGENT)
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def beep():
    if IS_MAC:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)


def make_transparent(w):
    """زوايا النافذة شفافة عشان تطلع مدوّرة."""
    if IS_MAC:
        w.attributes("-transparent", True)
        w.configure(bg=KEY)
    else:
        w.configure(bg=KEY)
        w.attributes("-transparentcolor", KEY)


def work_area():
    """حدود الشاشة بدون شريط المهام."""
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0)  # SPI_GETWORKAREA
    return rc.r, rc.b


_lock = None


def already_running():
    """نسخة وحدة بس من البرنامج. لو فيه نسخة شغالة نطلب منها تظهر ونطلع."""
    global _lock
    if IS_MAC:
        _lock = open(os.path.join(APP_DIR, ".lock"), "w")
        try:
            fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except OSError:
            open(SHOW_FLAG, "w").close()
            return True
    ctypes.windll.kernel32.CreateMutexW(None, False, "abwoo.reminders.single")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        open(SHOW_FLAG, "w").close()
        return True
    return False


class Toast:
    """إشعار ينزل من فوق الشاشة."""

    W, H = 480, 142

    def __init__(self, root, item, slot, on_done, on_snooze, stage="start"):
        self.win = tk.Toplevel(root)
        if stage == "end":
            color, head_text = RED, "⛔  انتهى الوقت ولم تنفذ"
        elif stage.startswith("w"):
            color, head_text = URGENT, f"⚠️  لم تنفذ بعد  •  باقي {dur_text(end_dt(item) - datetime.now())}"
        else:
            color, head_text = ACCENT, "⏰  تذكير"
        when = f"من {fmt12(item['time'])} إلى {fmt12(item['end'])}" if item.get("end") else fmt12(item["time"])
        w = self.win
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        make_transparent(w)
        self.x = (w.winfo_screenwidth() - self.W) // 2
        # على الماك نبدأ تحت شريط القوائم
        self.target = (40 if IS_MAC else 18) + slot * (self.H + 12)
        self.y = -self.H
        w.geometry(f"{self.W}x{self.H}+{self.x}+{self.y}")

        outer = ctk.CTkFrame(w, corner_radius=22, fg_color=CARD, border_width=2,
                             border_color=color, bg_color=KEY)
        outer.pack(fill="both", expand=True)

        head = ctk.CTkFrame(outer, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkLabel(head, text=head_text, text_color=color, font=font(12, "bold")).pack(side="right")
        ctk.CTkLabel(head, text=when, text_color=MUTED, font=font(12)).pack(side="left")

        ctk.CTkLabel(outer, text=item["title"], text_color=TEXT, font=font(18, "bold"),
                     wraplength=self.W - 50, justify="right", anchor="e").pack(
            fill="x", padx=20, pady=(4, 6))

        btns = ctk.CTkFrame(outer, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkButton(btns, text="✓  تم", width=90, height=34, corner_radius=12, fg_color=GREEN,
                      hover_color="#2bb37a", text_color="#06130d", font=font(13, "bold"),
                      command=lambda: self.close(on_done)).pack(side="right")
        if item.get("end"):
            # له فترة: التنبيهات الجاية تكفي بدل التأجيل
            ctk.CTkButton(btns, text="حسناً", width=80, height=34, corner_radius=12,
                          fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT, font=font(13),
                          command=lambda: self.close(lambda: on_snooze(0))).pack(side="right", padx=8)
        else:
            ctk.CTkLabel(btns, text="أجّل:", text_color=MUTED, font=font(12)).pack(side="right", padx=(14, 6))
            for label, mins in (("5 د", 5), ("15 د", 15), ("ساعة", 60)):
                ctk.CTkButton(btns, text=label, width=58, height=34, corner_radius=12,
                              fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT, font=font(13),
                              command=lambda m=mins: self.close(lambda: on_snooze(m))).pack(side="right", padx=3)

        beep()
        self.slide()

    def slide(self):
        if self.y < self.target:
            self.y = min(self.target, self.y + 16)
            self.win.geometry(f"+{self.x}+{self.y}")
            self.win.after(10, self.slide)

    def close(self, cb):
        self.win.destroy()
        cb()


class App:
    def __init__(self, start_hidden=False):
        ctk.set_appearance_mode("dark")
        self.items = load()
        self.root = ctk.CTk(fg_color=BG)
        r = self.root
        r.title("تذكيراتي")
        r.geometry("540x900")
        r.minsize(480, 760)
        r.protocol("WM_DELETE_WINDOW", self.hide)
        if IS_MAC:
            # أيقونة الـ Dock، والضغط عليها يفتح البرنامج لو كان مخفي
            if os.path.exists(ICON_FILE):
                self.dock_icon = ImageTk.PhotoImage(Image.open(ICON_FILE).convert("RGBA").resize((256, 256)))
                r.iconphoto(True, self.dock_icon)
            r.createcommand("tk::mac::ReopenApplication", self.show)
            r.bind("<Command-n>", lambda e: self.focus_input())
            r.bind("<Command-k>", lambda e: self.toggle_quick())
            r.bind("<Command-w>", lambda e: self.hide())
        elif os.path.exists(ICON_FILE):
            r.iconbitmap(ICON_FILE)
            r.after(300, lambda: r.iconbitmap(ICON_FILE))  # customtkinter يغيّر الأيقونة بعد التشغيل
        r.bind("<Control-n>", lambda e: self.focus_input())
        r.bind("<Escape>", lambda e: self.hide())
        self.section = "المهام"
        self.view = "اليوم"
        self.urgent = False
        self.project = None  # القسم المختار في المهام (None = الكل)
        self.editing = False
        self.toasts = {}
        self.quick = None
        self.quick_closed_at = 0.0
        self.widget = None
        self.build()
        self.switch_section("المهام")
        self.start_tray()
        if start_hidden:
            r.withdraw()
        self.tick()
        self.watch_show_flag()
        self.auto_refresh()
        if self.widget_prefs().get("on"):
            self.open_widget()
            self.widget_switch.select()

        self.login_win = None
        CLOUD.on_remote = lambda changes: self.root.after(0, self.apply_remote, changes)
        CLOUD.on_logout = lambda: self.root.after(0, self.paint_cloud)
        if CLOUD.logged_in:
            CLOUD.track(self.items)
            CLOUD.start()
        elif not start_hidden:
            r.after(400, self.open_login)
        self.paint_cloud()

    # ---------- الشريط اللي جنب الساعة ----------
    def start_tray(self):
        if IS_MAC:
            # الماك: بدل أيقونة الساعة، قائمة في شريط القوائم فوق
            self.tray = None
            bar = tk.Menu(self.root)
            m = tk.Menu(bar, tearoff=0)
            m.add_command(label="ملخص سريع", accelerator="Command+K", command=self.toggle_quick)
            m.add_command(label="إضافة", accelerator="Command+N", command=lambda: (self.show(), self.focus_input()))
            m.add_command(label="ويدجت المهام", command=self.toggle_widget)
            bar.add_cascade(label="تذكيراتي", menu=m)
            self.root.configure(menu=bar)
            return
        img = Image.open(ICON_FILE) if os.path.exists(ICON_FILE) else Image.new("RGB", (64, 64), ACCENT)
        menu = pystray.Menu(
            # الضغطة الوحدة على الأيقونة تفتح الملخص السريع
            pystray.MenuItem("ملخص سريع", lambda: self.root.after(0, self.toggle_quick), default=True),
            pystray.MenuItem("فتح البرنامج", lambda: self.root.after(0, self.show)),
            pystray.MenuItem("ويدجت المهام", lambda: self.root.after(0, self.toggle_widget)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("خروج", lambda: self.root.after(0, self.quit)),
        )
        self.tray = pystray.Icon("reminders", img, "تذكيراتي", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    # ---------- ويدجت المهام على سطح المكتب ----------
    WIDGET_W = 320
    # شكل زجاجي: ألوان فاتحة شوي مع شفافية، وحافة فاتحة مثل لمعة القزاز
    W_BG, W_ROW, W_ROW_HOVER, W_LINE = "#1b1e2a", "#262a39", "#2f3446", "#4a5068"
    W_TEXT, W_MUTED, W_FAINT = "#f4f5fa", "#a3a8bb", "#7a8096"
    W_ACCENT, W_GREEN, W_ORANGE = "#9aaaff", "#5fd4a0", "#ffa56b"
    W_ALPHA = 0.82

    def widget_prefs(self):
        try:
            with open(WIDGET_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"on": False, "top": False}

    def save_widget_prefs(self, **kw):
        p = {**self.widget_prefs(), **kw}
        with open(WIDGET_FILE, "w", encoding="utf-8") as f:
            json.dump(p, f)

    def toggle_widget(self):
        if self.widget is not None:
            self.close_widget()
        else:
            self.open_widget()
        if hasattr(self, "widget_switch"):
            (self.widget_switch.select if self.widget else self.widget_switch.deselect)()

    def close_widget(self):
        if self.widget is not None:
            self.widget.destroy()
            self.widget = None
        self.save_widget_prefs(on=False)

    def open_widget(self):
        p = self.widget_prefs()
        w = self.widget = tk.Toplevel(self.root)
        w.overrideredirect(True)  # بدون إطار وبدون أيقونة في شريط المهام
        w.attributes("-topmost", bool(p.get("top")))
        w.attributes("-alpha", self.W_ALPHA)
        make_transparent(w)

        box = ctk.CTkFrame(w, corner_radius=22, fg_color=self.W_BG, border_width=1,
                           border_color=self.W_LINE, bg_color=KEY, width=self.WIDGET_W)
        box.pack(fill="both", expand=True)

        # الرأس: بس العنوان والتاريخ. ضغطة عليه تطلع الخيارات، والسحب يحرّك الويدجت
        head = ctk.CTkFrame(box, fg_color="transparent", cursor="hand2")
        head.pack(fill="x", padx=18, pady=(16, 0))
        t1 = self.widget_title = ctk.CTkLabel(head, text="مهامي", font=font(17, "bold"), text_color=self.W_TEXT,
                                              anchor="e", height=22)
        t1.pack(fill="x")
        self.widget_sub = ctk.CTkLabel(head, text="", font=font(11), text_color=self.W_MUTED, anchor="e", height=16)
        self.widget_sub.pack(fill="x")
        for wd in (head, t1, self.widget_sub):
            wd.bind("<ButtonPress-1>", self.widget_drag_start)
            wd.bind("<B1-Motion>", self.widget_drag)
            wd.bind("<ButtonRelease-1>", self.widget_drag_end)
        # الكلك اليمين في أي مكان يطلع الخيارات بعد (نربطه مرة وحدة بس)
        if not getattr(self, "_rc_bound", False):
            self._rc_bound = True
            self.root.bind_all("<Button-3>", self.widget_right_click, add="+")
            if IS_MAC:
                self.root.bind_all("<Button-2>", self.widget_right_click, add="+")

        # شريط التقدم
        self.widget_bar = ctk.CTkProgressBar(box, height=4, corner_radius=2, progress_color=self.W_GREEN,
                                             fg_color=self.W_ROW)
        self.widget_bar.pack(fill="x", padx=18, pady=(12, 4))
        self.widget_prog = ctk.CTkLabel(box, text="", font=font(10), text_color=self.W_FAINT, anchor="e", height=14)
        self.widget_prog.pack(fill="x", padx=18)

        self.widget_body = ctk.CTkFrame(box, fg_color="transparent")
        self.widget_body.pack(fill="x", padx=12, pady=(4, 14))

        self.render_widget()
        w.update_idletasks()
        sw = w.winfo_screenwidth()
        x = p.get("x", sw - self.WIDGET_W - 24)
        y = p.get("y", 80)
        # لو انشالت الشاشة اللي كان عليها، نرجّعه (نحسب كل الشاشات مو بس الأساسية)
        if IS_MAC:
            vx, vy, vw, vh = 0, 0, sw, w.winfo_screenheight()
        else:
            gm = ctypes.windll.user32.GetSystemMetrics
            vx, vy, vw, vh = gm(76), gm(77), gm(78), gm(79)  # SM_X/Y/CX/CYVIRTUALSCREEN
        x = min(max(vx, x), vx + vw - 120)
        y = min(max(vy, y), vy + vh - 120)
        w.geometry(f"+{x}+{y}")
        self.save_widget_prefs(on=True)

    def widget_menu(self, x, y):
        top = bool(self.widget_prefs().get("top"))
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="فتح البرنامج", command=self.show)
        m.add_command(label=("✓  " if top else "     ") + "فوق كل النوافذ", command=self.flip_widget_top)
        cur = self.widget_prefs().get("project")
        pm = tk.Menu(m, tearoff=0)
        pm.add_command(label=("✓  " if cur is None else "     ") + "كل المهام",
                       command=lambda: self.widget_project(None))
        for p in self.projects():
            pm.add_command(label=("✓  " if cur == p["id"] else "     ") + p["name"],
                           command=lambda pid=p["id"]: self.widget_project(pid))
        m.add_cascade(label="     القسم", menu=pm)
        m.add_separator()
        m.add_command(label="إخفاء الويدجت", command=self.toggle_widget)
        m.tk_popup(x, y)

    def widget_right_click(self, e):
        if self.widget is not None and str(e.widget).startswith(str(self.widget)):
            self.widget_menu(e.x_root, e.y_root)

    def widget_project(self, pid):
        self.save_widget_prefs(project=pid)
        self.render_widget()

    def flip_widget_top(self):
        top = not self.widget_prefs().get("top")
        self.save_widget_prefs(top=top)
        self.widget.attributes("-topmost", top)

    def widget_drag_start(self, e):
        self._drag = (e.x_root - self.widget.winfo_x(), e.y_root - self.widget.winfo_y())
        self._drag_from = (e.x_root, e.y_root)

    def widget_drag(self, e):
        dx, dy = self._drag
        self.widget.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def widget_drag_end(self, e):
        fx, fy = self._drag_from
        if abs(e.x_root - fx) < 4 and abs(e.y_root - fy) < 4:  # ضغطة بدون سحب: نطلع الخيارات
            self.widget_menu(e.x_root, e.y_root)
        else:
            self.save_widget_prefs(x=self.widget.winfo_x(), y=self.widget.winfo_y())

    def widget_done(self, it, btn):
        # علامة صح خضرا، وبعدها تختفي المهمة
        btn.configure(text="✓", fg_color=self.W_GREEN, border_color=self.W_GREEN, state="disabled")
        it["done"] = True
        save(self.items)
        self.root.after(450, self.refresh)

    def widget_row(self, it):
        urgent = it.get("urgent")
        row = ctk.CTkFrame(self.widget_body, fg_color=self.W_ROW, corner_radius=12)
        row.pack(fill="x", pady=3)
        if urgent:  # خط برتقالي على طرف المهمة المستعجلة
            ctk.CTkFrame(row, width=3, corner_radius=2, fg_color=self.W_ORANGE).pack(side="right", fill="y",
                                                                                     padx=(0, 4), pady=8)
        btn = ctk.CTkButton(row, text="", width=20, height=20, corner_radius=10, fg_color="transparent",
                            hover_color=self.W_GREEN, border_width=2, text_color="#0d0e13", font=font(11, "bold"),
                            text_color_disabled="#0d0e13", border_color=self.W_ORANGE if urgent else self.W_FAINT)
        btn.configure(command=lambda: self.widget_done(it, btn))
        btn.pack(side="right", padx=(8 if urgent else 12, 8), pady=10)
        lbl = ctk.CTkLabel(row, text=it["title"], font=font(13), text_color=self.W_TEXT, anchor="e",
                           justify="right", wraplength=self.WIDGET_W - 90)
        lbl.pack(side="right", fill="x", expand=True, padx=(10, 0), pady=6)
        for wd in (row, lbl):
            wd.bind("<Enter>", lambda e: row.configure(fg_color=self.W_ROW_HOVER))
            wd.bind("<Leave>", lambda e: row.configure(fg_color=self.W_ROW))

    def render_widget(self):
        if self.widget is None:
            return
        for c in self.widget_body.winfo_children():
            c.destroy()
        all_tasks = [i for i in self.items if kind(i) == "task"]
        p = self.projects_by_id().get(self.widget_prefs().get("project"))
        if p:
            all_tasks = [i for i in all_tasks if i.get("project") == p["id"]]
        self.widget_title.configure(text=p["name"] if p else "مهامي", text_color=p["color"] if p else self.W_TEXT)
        tasks = [i for i in all_tasks if not i["done"]]
        tasks.sort(key=lambda i: not i.get("urgent"))
        done = len(all_tasks) - len(tasks)

        now = datetime.now()
        left = f"  •  باقي {len(tasks)}" if tasks else ""
        self.widget_sub.configure(text=f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}{left}")
        self.widget_bar.set(done / len(all_tasks) if all_tasks else 0)
        self.widget_prog.configure(text=f"خلّصت {done} من {len(all_tasks)}" if all_tasks else "")

        if not tasks:
            e = ctk.CTkFrame(self.widget_body, fg_color="transparent")
            e.pack(fill="x", pady=16)
            ctk.CTkLabel(e, text="✓", font=font(26, "bold"), text_color=self.W_GREEN, height=30).pack()
            ctk.CTkLabel(e, text="خلّصت كل مهامك", font=font(13, "bold"), text_color=self.W_TEXT,
                         height=20).pack()
            ctk.CTkLabel(e, text="ما باقي شي اليوم", font=font(11), text_color=self.W_MUTED, height=16).pack()

        limit = 7
        for it in tasks[:limit]:
            self.widget_row(it)
        if len(tasks) > limit:
            more = ctk.CTkButton(self.widget_body, text=f"و {len(tasks) - limit} مهام ثانية  ‹", height=24,
                                 fg_color="transparent", hover_color=self.W_ROW, text_color=self.W_MUTED,
                                 font=font(11), anchor="e", command=self.show)
            more.pack(fill="x", pady=(2, 0))
        # الطول يتغيّر مع عدد المهام
        self.widget.update_idletasks()
        self.widget.geometry(f"{self.WIDGET_W}x{self.widget.winfo_reqheight()}")

    # ---------- الملخص السريع ----------
    def toggle_quick(self):
        if self.quick is not None:
            self.close_quick()
        elif time.monotonic() - self.quick_closed_at > 0.4:  # الضغطة نفسها اللي سكّرته
            self.open_quick()

    def close_quick(self):
        if self.quick is not None:
            self.quick.destroy()
            self.quick = None
            self.quick_closed_at = time.monotonic()

    def quick_data(self):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        tasks = [i for i in self.items if kind(i) == "task" and not i["done"]]
        urgent = [i for i in tasks if i.get("urgent")]
        rems = [i for i in self.items if kind(i) == "reminder" and not i["done"]]
        rems_today = [i for i in rems if i["date"] == today]
        upcoming = sorted((i for i in rems if due_dt(i) > now), key=due_dt)
        return tasks, urgent, rems_today, upcoming[0] if upcoming else None

    @staticmethod
    def until(dt):
        mins = int((dt - datetime.now()).total_seconds() // 60) + 1
        if mins < 60:
            return f"بعد {mins} د"
        h, m = divmod(mins, 60)
        if h < 24:
            return f"بعد {h} س" + (f" و {m} د" if m else "")
        return day_label(dt.strftime("%Y-%m-%d"))

    def open_quick(self):
        tasks, urgent, rems_today, nxt = self.quick_data()
        now = datetime.now()

        w = self.quick = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        make_transparent(w)

        box = ctk.CTkFrame(w, corner_radius=20, fg_color=CARD, border_width=1,
                           border_color=FIELD_HOVER, bg_color=KEY)
        box.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=16)

        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="ملخص سريع", font=font(17, "bold"), text_color=TEXT).pack(side="right")
        ctk.CTkLabel(head, text=f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}",
                     font=font(12), text_color=MUTED).pack(side="left")

        stats = ctk.CTkFrame(inner, fg_color="transparent")
        stats.pack(fill="x", pady=(12, 0))
        for i, (num, label, color) in enumerate((
                (len(urgent), "مستعجلة", URGENT),
                (len(tasks), "مهام باقية", ACCENT),
                (len(rems_today), "تذكيرات اليوم", GREEN))):
            s = ctk.CTkFrame(stats, corner_radius=14, fg_color=FIELD, width=96, height=72)
            s.pack(side="right", padx=(0 if i == 0 else 8, 0))
            s.pack_propagate(False)
            ctk.CTkLabel(s, text=str(num), font=font(22, "bold"), text_color=color).pack(pady=(8, 0))
            ctk.CTkLabel(s, text=label, font=font(11), text_color=MUTED).pack()

        nx = ctk.CTkFrame(inner, corner_radius=14, fg_color=FIELD)
        nx.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(nx, text="⏰  التذكير الجاي", font=font(12, "bold"), text_color=ACCENT,
                     anchor="e").pack(fill="x", padx=14, pady=(10, 0))
        if nxt:
            ctk.CTkLabel(nx, text=nxt["title"], font=font(14, "bold"), text_color=TEXT, anchor="e",
                         justify="right", wraplength=280).pack(fill="x", padx=14)
            ctk.CTkLabel(nx, text=f"{fmt12(nxt['time'])}  •  {self.until(due_dt(nxt))}", font=font(12),
                         text_color=MUTED, anchor="e").pack(fill="x", padx=14, pady=(0, 10))
        else:
            ctk.CTkLabel(nx, text="ما فيه تذكيرات جاية", font=font(13), text_color=MUTED,
                         anchor="e").pack(fill="x", padx=14, pady=(2, 10))

        if urgent:
            ctk.CTkLabel(inner, text="🔥  المستعجلة", font=font(12, "bold"), text_color=URGENT,
                         anchor="e").pack(fill="x", pady=(12, 2))
            for it in urgent[:3]:
                row = ctk.CTkFrame(inner, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkButton(row, text="", width=20, height=20, corner_radius=10, fg_color="transparent",
                              hover_color=GREEN, border_width=2, border_color=MUTED,
                              command=lambda it=it: self.quick_done(it)).pack(side="right", padx=(6, 0))
                ctk.CTkLabel(row, text=it["title"], font=font(13), text_color=TEXT, anchor="e").pack(
                    side="right", fill="x", expand=True)
            if len(urgent) > 3:
                ctk.CTkLabel(inner, text=f"و {len(urgent) - 3} غيرها", font=font(11), text_color=MUTED,
                             anchor="e").pack(fill="x")

        ctk.CTkButton(inner, text="فتح البرنامج", height=38, corner_radius=12, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=font(13, "bold"),
                      command=lambda: (self.close_quick(), self.show())).pack(fill="x", pady=(14, 0))

        # فوق الساعة في زاوية الشاشة
        w.update_idletasks()
        W, H = 340, w.winfo_reqheight()
        if IS_MAC:  # تحت شريط القوائم في الزاوية اليمين
            w.geometry(f"{W}x{H}+{w.winfo_screenwidth() - W - 12}+36")
        else:
            right, bottom = work_area()
            w.geometry(f"{W}x{H}+{right - W - 12}+{bottom - H - 12}")
        w.focus_force()
        w.bind("<FocusOut>", lambda e: w.after(150, self.quick_lost_focus))
        w.bind("<Escape>", lambda e: self.close_quick())

    def quick_lost_focus(self):
        if self.quick is None:
            return
        try:
            f = self.quick.focus_get()
        except (KeyError, tk.TclError):
            f = None
        if f is None or not str(f).startswith(str(self.quick)):
            self.close_quick()

    def quick_done(self, it):
        it["done"] = True
        save(self.items)
        self.refresh()
        self.close_quick()
        self.open_quick()

    def show(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self.refresh()

    def hide(self):
        self.root.withdraw()

    def quit(self):
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def watch_show_flag(self):
        if os.path.exists(SHOW_FLAG):
            try:
                os.remove(SHOW_FLAG)
            except OSError:
                pass
            self.show()
        self.root.after(700, self.watch_show_flag)

    def auto_refresh(self):
        # كل دقيقة: يحدّث المتأخر والتاريخ والترحيب (إلا لو تعدّل شي)
        if not self.editing:
            self.refresh()
        self.root.after(60_000, self.auto_refresh)

    # ---------- واجهة ----------
    def seg(self, parent, values, cmd, height=38):
        return ctk.CTkSegmentedButton(
            parent, values=values, command=cmd, height=height, corner_radius=12,
            font=font(14, "bold"), fg_color=CARD, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER, unselected_color=CARD,
            unselected_hover_color=CARD_HOVER, text_color=TEXT)

    def build(self):
        r = self.root

        head = ctk.CTkFrame(r, fg_color="transparent")
        head.pack(fill="x", padx=26, pady=(24, 0))
        self.hello = ctk.CTkLabel(head, text="", font=font(28, "bold"), text_color=TEXT, anchor="e")
        self.hello.pack(fill="x")
        self.date_lbl = ctk.CTkLabel(head, text="", font=font(13), text_color=MUTED, anchor="e")
        self.date_lbl.pack(fill="x")

        self.main_tabs = self.seg(r, ["التذكيرات", "المهام"], self.switch_section, height=44)
        self.main_tabs.pack(fill="x", padx=26, pady=(16, 0))

        prog = ctk.CTkFrame(r, corner_radius=18, fg_color=CARD)
        prog.pack(fill="x", padx=26, pady=(12, 0))
        row = ctk.CTkFrame(prog, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(14, 6))
        self.prog_title = ctk.CTkLabel(row, text="", font=font(14, "bold"), text_color=TEXT)
        self.prog_title.pack(side="right")
        self.prog_count = ctk.CTkLabel(row, text="", font=font(13), text_color=MUTED)
        self.prog_count.pack(side="left")
        self.bar = ctk.CTkProgressBar(prog, height=8, corner_radius=4, progress_color=GREEN, fg_color=FIELD)
        self.bar.pack(fill="x", padx=18, pady=(0, 16))

        self.form_holder = ctk.CTkFrame(r, fg_color="transparent")
        self.form_holder.pack(fill="x", padx=26, pady=(12, 0))
        self.build_task_form()
        self.build_reminder_form()

        self.sub_holder = ctk.CTkFrame(r, fg_color="transparent")
        self.sub_holder.pack(fill="x", padx=26)
        self.sub_tabs = self.seg(self.sub_holder, ["الكل", "اليوم"], self.show_view, height=34)
        self.sub_tabs.set("اليوم")
        self.build_projects_bar()

        foot = ctk.CTkFrame(r, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=26, pady=(0, 14))
        self.startup = ctk.CTkSwitch(foot, text=f"يشتغل مع {OS_NAME}", font=font(12), text_color=MUTED,
                                     progress_color=ACCENT, fg_color=FIELD, command=self.toggle_startup)
        if startup_enabled():
            self.startup.select()
        self.startup.pack(side="right")
        self.widget_switch = ctk.CTkSwitch(foot, text="ويدجت", font=font(12), text_color=MUTED,
                                           progress_color=ACCENT, fg_color=FIELD, command=self.toggle_widget)
        self.widget_switch.pack(side="right", padx=(0, 12))
        self.cloud_btn = ctk.CTkButton(foot, text="", height=26, corner_radius=13, fg_color="transparent",
                                       hover_color=FIELD, font=font(11), command=self.cloud_clicked)
        self.cloud_btn.pack(side="left")

        self.list = ctk.CTkScrollableFrame(r, fg_color="transparent", corner_radius=0,
                                           scrollbar_button_color=FIELD,
                                           scrollbar_button_hover_color=MUTED)
        self.list.pack(fill="both", expand=True, padx=(18, 14), pady=(8, 8))

    def entry(self, parent, placeholder, cmd):
        e = ctk.CTkEntry(parent, height=44, corner_radius=12, fg_color=FIELD, border_width=0,
                         placeholder_text=placeholder, justify="right", font=font(15), text_color=TEXT)
        e.pack(fill="x", padx=16, pady=(16, 10))
        e.bind("<Return>", lambda ev: cmd())
        return e

    def build_task_form(self):
        f = self.task_form = ctk.CTkFrame(self.form_holder, corner_radius=18, fg_color=CARD)
        self.task_title = self.entry(f, "وش المهمة؟ (تنفذها في أي وقت)", self.add_task)
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(row, text="＋  إضافة", width=120, height=40, corner_radius=12, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=font(14, "bold"), command=self.add_task).pack(side="right")
        self.btn_urgent = ctk.CTkButton(row, text="", width=120, height=40, corner_radius=12,
                                        font=font(13, "bold"), command=self.flip_urgent)
        self.btn_urgent.pack(side="left")
        self.paint_urgent()

    def build_reminder_form(self):
        f = self.rem_form = ctk.CTkFrame(self.form_holder, corner_radius=18, fg_color=CARD)
        self.title = self.entry(f, "وش تبي تتذكر؟", self.add_reminder)

        now = datetime.now()
        row1 = ctk.CTkFrame(f, fg_color="transparent")
        row1.pack(fill="x", padx=16)
        ctk.CTkLabel(row1, text="متى", width=44, anchor="e", font=font(13),
                     text_color=MUTED).pack(side="right")
        self.day_btns = {}
        for label, n in (("اليوم", 0), ("بكرا", 1)):
            b = ctk.CTkButton(row1, text=label, height=34, corner_radius=17, font=font(13), width=70,
                              command=lambda n=n: self.pick_day(n))
            b.pack(side="right", padx=(12 if n == 0 else 0, 8))
            self.day_btns[n] = b
        self.date_btn = ctk.CTkButton(row1, text="📅  اختر تاريخ", height=34, corner_radius=17,
                                      font=font(13), width=110, command=self.toggle_calendar)
        self.date_btn.pack(side="right")
        self.cal = None
        self.cal_closed_at = 0.0

        self.start_time = self.time_row(f, "من", now)
        self.has_end = ctk.BooleanVar(value=False)
        self.end_time = self.time_row(f, "إلى", now + timedelta(hours=1), toggle=self.has_end)
        self.paint_end()

        row2 = ctk.CTkFrame(f, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(12, 16))
        ctk.CTkButton(row2, text="＋  إضافة", width=120, height=40, corner_radius=12, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=font(14, "bold"), command=self.add_reminder).pack(side="right")
        self.daily = ctk.CTkSwitch(row2, text="يتكرر كل يوم", font=font(13), text_color=TEXT,
                                   progress_color=ACCENT, fg_color=FIELD)
        self.daily.pack(side="left")
        self.flash_lbl = ctk.CTkLabel(row2, text="", font=font(12, "bold"), anchor="e")
        self.flash_lbl.pack(side="right", fill="x", expand=True, padx=10)
        self.flash_job = None
        self.pick_day(0)

    def time_row(self, parent, label, when, toggle=None):
        """صف وقت: ساعة : دقيقة  صباح/مساء. يرجّع (ساعة، دقيقة، ص/م)."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(10, 0))
        if toggle is None:
            ctk.CTkLabel(row, text=label, width=44, anchor="e", font=font(13),
                         text_color=MUTED).pack(side="right")
        else:
            ctk.CTkCheckBox(row, text=label, variable=toggle, width=44, checkbox_width=20,
                            checkbox_height=20, corner_radius=6, font=font(13), text_color=MUTED,
                            fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=MUTED,
                            command=self.paint_end).pack(side="right")
        box = ctk.CTkFrame(row, fg_color="transparent")
        box.pack(side="right", padx=(0, 12))
        hour = ctk.CTkEntry(box, width=52, height=36, corner_radius=10, fg_color=FIELD,
                            border_width=0, justify="center", font=font(15, "bold"))
        minute = ctk.CTkEntry(box, width=52, height=36, corner_radius=10, fg_color=FIELD,
                              border_width=0, justify="center", font=font(15, "bold"))
        hour.insert(0, when.strftime("%I").lstrip("0"))
        minute.insert(0, when.strftime("%M"))
        ampm = ctk.CTkSegmentedButton(
            box, values=["مساء", "صباح"], width=150, height=36, corner_radius=10, font=font(13, "bold"),
            fg_color=FIELD, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=FIELD, unselected_hover_color=FIELD_HOVER, text_color=TEXT)
        ampm.set("صباح" if when.hour < 12 else "مساء")
        # يُقرأ من اليمين: 3:00 مساء
        ampm.pack(side="left", padx=(0, 10))
        hour.pack(side="left")
        ctk.CTkLabel(box, text=":", font=font(16, "bold"), text_color=MUTED).pack(side="left", padx=4)
        minute.pack(side="left")
        return hour, minute, ampm

    def paint_end(self):
        state = "normal" if self.has_end.get() else "disabled"
        for w in self.end_time:
            w.configure(state=state)
        hour, minute, _ = self.end_time
        for e in (hour, minute):
            e.configure(text_color=TEXT if state == "normal" else MUTED)

    @staticmethod
    def read_time(parts):
        """يقرأ صف الوقت ويرجّع HH:MM، أو None ويلوّن الخانات بالأحمر لو غلط."""
        hour, minute, ampm = parts
        try:
            h, m = int(hour.get()), int(minute.get())
            assert 1 <= h <= 12 and 0 <= m < 60
        except (ValueError, AssertionError):
            hour.configure(border_width=1, border_color=RED)
            minute.configure(border_width=1, border_color=RED)
            return None
        hour.configure(border_width=0)
        minute.configure(border_width=0)
        h = h % 12 + (12 if ampm.get() == "مساء" else 0)
        return f"{h:02d}:{m:02d}"

    def paint_urgent(self):
        if self.urgent:
            self.btn_urgent.configure(text="🔥  مستعجلة", fg_color=URGENT, hover_color="#f07a3c",
                                      text_color="#1a0c03")
        else:
            self.btn_urgent.configure(text="عادية", fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT)

    def flip_urgent(self):
        self.urgent = not self.urgent
        self.paint_urgent()

    def pick_day(self, n):
        # نحفظ "بعد كم يوم" مو التاريخ نفسه، عشان لو البرنامج مفتوح من أمس يظل "اليوم" صحيح
        self.day_offset, self.custom_date = n, None
        self.paint_days()

    def chosen_date(self):
        if self.custom_date is not None:
            return self.custom_date.strftime("%Y-%m-%d")
        return (datetime.now() + timedelta(days=self.day_offset)).strftime("%Y-%m-%d")

    def paint_days(self):
        def paint(b, on):
            b.configure(fg_color=ACCENT if on else FIELD, hover_color=ACCENT_HOVER if on else FIELD_HOVER,
                        text_color="#fff" if on else MUTED)
        for k, b in self.day_btns.items():
            paint(b, self.custom_date is None and k == self.day_offset)
        d = self.custom_date
        paint(self.date_btn, d is not None)
        self.date_btn.configure(
            text=f"📅  {DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}" if d else "📅  اختر تاريخ",
            width=150 if d else 110)

    # ---------- التقويم ----------
    def toggle_calendar(self):
        if self.cal is not None:
            self.close_calendar()
        elif time.monotonic() - self.cal_closed_at > 0.3:
            self.open_calendar()

    def close_calendar(self):
        if self.cal is not None:
            self.cal.destroy()
            self.cal = None
            self.cal_closed_at = time.monotonic()

    def open_calendar(self):
        w = self.cal = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        make_transparent(w)
        self.cal_box = ctk.CTkFrame(w, corner_radius=18, fg_color=CARD, border_width=1,
                                    border_color=FIELD_HOVER, bg_color=KEY)
        self.cal_box.pack(fill="both", expand=True)
        start = self.custom_date or datetime.now().date()
        self.cal_month = start.replace(day=1)
        self.render_calendar()

        w.update_idletasks()
        b = self.date_btn
        x = b.winfo_rootx() + b.winfo_width() - w.winfo_reqwidth()
        w.geometry(f"+{max(0, x)}+{b.winfo_rooty() + b.winfo_height() + 6}")
        w.focus_force()
        w.bind("<FocusOut>", lambda e: w.after(150, self.calendar_lost_focus))
        w.bind("<Escape>", lambda e: self.close_calendar())

    def calendar_lost_focus(self):
        if self.cal is None:
            return
        try:
            f = self.cal.focus_get()
        except (KeyError, tk.TclError):
            f = None
        if f is None or not str(f).startswith(str(self.cal)):
            self.close_calendar()

    def shift_month(self, step):
        y, m = self.cal_month.year, self.cal_month.month + step
        y, m = (y + 1, 1) if m > 12 else (y - 1, 12) if m < 1 else (y, m)
        self.cal_month = self.cal_month.replace(year=y, month=m)
        self.render_calendar()
        self.cal.focus_force()

    def render_calendar(self):
        for c in self.cal_box.winfo_children():
            c.destroy()
        inner = ctk.CTkFrame(self.cal_box, fg_color="transparent")
        inner.pack(padx=14, pady=12)
        today = datetime.now().date()
        month = self.cal_month

        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))
        nav = dict(width=32, height=30, corner_radius=10, fg_color=FIELD, hover_color=FIELD_HOVER,
                   text_color=TEXT, font=font(14, "bold"))
        can_go_back = (month.year, month.month) > (today.year, today.month)
        ctk.CTkButton(head, text="›", command=lambda: self.shift_month(-1),
                      state="normal" if can_go_back else "disabled", **nav).pack(side="right")
        ctk.CTkButton(head, text="‹", command=lambda: self.shift_month(1), **nav).pack(side="left")
        ctk.CTkLabel(head, text=f"{MONTHS[month.month - 1]} {month.year}", font=font(14, "bold"),
                     text_color=TEXT).pack(side="top")

        grid = ctk.CTkFrame(inner, fg_color="transparent")
        grid.pack()
        # الأسبوع يبدأ الأحد، والأعمدة من اليمين لليسار
        for i, name in enumerate(("أحد", "إثنين", "ثلاثاء", "أربعاء", "خميس", "جمعة", "سبت")):
            ctk.CTkLabel(grid, text=name, width=40, font=font(10), text_color=MUTED).grid(row=0, column=6 - i)
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(month.year, month.month)
        for r, week in enumerate(weeks, start=1):
            for i, d in enumerate(week):
                if d.month != month.month:
                    continue
                selected = d == (self.custom_date or (today + timedelta(days=self.day_offset)))
                past = d < today
                ctk.CTkButton(
                    grid, text=str(d.day), width=38, height=32, corner_radius=10, font=font(13),
                    fg_color=ACCENT if selected else "transparent",
                    hover_color=ACCENT_HOVER if selected else FIELD,
                    text_color="#fff" if selected else (FIELD_HOVER if past else TEXT),
                    border_width=1 if d == today and not selected else 0, border_color=ACCENT,
                    state="disabled" if past else "normal",
                    command=lambda d=d: self.pick_date(d)).grid(row=r, column=6 - i, padx=1, pady=1)

    def pick_date(self, d):
        diff = (d - datetime.now().date()).days
        if diff in self.day_btns:
            self.pick_day(diff)
        else:
            self.day_offset, self.custom_date = None, d
            self.paint_days()
        self.close_calendar()

    def flash(self, text, color=GREEN):
        self.flash_lbl.configure(text=text, text_color=color)
        if self.flash_job:
            self.root.after_cancel(self.flash_job)
        self.flash_job = self.root.after(4000, lambda: self.flash_lbl.configure(text=""))

    def switch_section(self, section):
        self.section = section
        self.main_tabs.set(section)
        self.task_form.pack_forget()
        self.rem_form.pack_forget()
        self.sub_tabs.pack_forget()
        self.proj_bar.pack_forget()
        if section == "المهام":
            self.task_form.pack(fill="x")
            self.proj_bar.pack(fill="x", pady=(12, 0))
        else:
            self.rem_form.pack(fill="x")
            self.sub_tabs.pack(fill="x", pady=(14, 0))
        self.refresh()

    def show_view(self, view):
        self.view = view
        self.refresh()

    def focus_input(self):
        (self.task_title if self.section == "المهام" else self.title).focus()

    # ---------- كروت ----------
    def base_card(self, it, done, title_color, meta, meta_color, toggle):
        c = ctk.CTkFrame(self.list, corner_radius=16, fg_color=CARD)
        c.pack(fill="x", pady=5, padx=(0, 4))
        ctk.CTkButton(
            c, text="✓" if done else "", width=30, height=30, corner_radius=15,
            fg_color=GREEN if done else "transparent", hover_color=GREEN if done else FIELD,
            border_width=2, border_color=GREEN if done else MUTED, text_color="#06130d", border_spacing=0,
            font=font(14, "bold"), command=toggle).pack(side="right", padx=(8, 16), pady=16)
        mid = ctk.CTkFrame(c, fg_color="transparent")
        mid.pack(side="right", fill="x", expand=True, pady=12)
        lbl = ctk.CTkLabel(mid, text=it["title"], anchor="e", justify="right", wraplength=240,
                           text_color=title_color, font=font(15, "bold", overstrike=done))
        lbl.pack(fill="x")
        lbl.bind("<Double-Button-1>", lambda e: self.start_edit(it, mid, lbl))
        if meta:
            ctk.CTkLabel(mid, text=meta, anchor="e", font=font(12), text_color=meta_color).pack(fill="x")
        left = ctk.CTkFrame(c, fg_color="transparent")
        left.pack(side="left", padx=10)
        ctk.CTkButton(left, text="✕", width=30, height=30, corner_radius=15, fg_color="transparent",
                      hover_color="#3a2128", text_color=MUTED, font=font(13),
                      command=lambda: self.delete(it)).pack()
        return c, left

    def start_edit(self, it, parent, lbl):
        self.editing = True
        e = ctk.CTkEntry(parent, height=34, corner_radius=10, fg_color=FIELD, border_width=1,
                         border_color=ACCENT, justify="right", font=font(15, "bold"), text_color=TEXT)
        e.insert(0, it["title"])
        e.pack(fill="x", before=lbl)
        lbl.pack_forget()
        e.focus()
        e.select_range(0, "end")

        def finish(save_it):
            if not self.editing:
                return
            self.editing = False
            text = e.get().strip()
            if save_it and text:
                it["title"] = text
                save(self.items)
            self.refresh()

        e.bind("<Return>", lambda ev: finish(True))
        e.bind("<Escape>", lambda ev: (finish(False), "break")[1])
        e.bind("<FocusOut>", lambda ev: finish(True))

    def task_card(self, it):
        done = it["done"]
        # لو نعرض كل الأقسام، نكتب اسم قسم المهمة تحتها بلونه
        p = self.projects_by_id().get(it.get("project")) if self.project is None else None
        meta, meta_color = (f"●  {p['name']}", p["color"]) if p else ("", MUTED)
        c, left = self.base_card(it, done, MUTED if done else TEXT, meta, MUTED if done else meta_color,
                                 lambda: self.toggle_task(it))
        if not done:
            urgent = it.get("urgent", False)
            ctk.CTkButton(left, text="🔥" if urgent else "⚑", width=30, height=26, corner_radius=13,
                          fg_color="transparent", hover_color=FIELD,
                          text_color=URGENT if urgent else MUTED, font=font(13),
                          command=lambda: self.toggle_urgent(it)).pack()

    def reminder_card(self, it, show_day):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        rolled = it.get("done_at") == today and it["date"] != today
        done = it["done"] or rolled
        end = end_dt(it)
        meta = fmt12(it["time"])
        if end:
            meta = f"من {fmt12(it['time'])} إلى {fmt12(it['end'])}"
        if show_day:
            meta = f"{day_label(it['date'])}  •  {meta}"
        if it["daily"]:
            meta += "  •  🔁 يومي"
        color = MUTED
        if not done and end:
            if now >= end:
                meta += "  •  انتهى ولم تنفذ"
                color = RED
            elif now >= due_dt(it):
                left = end - now
                meta += f"  •  باقي {dur_text(left)}"
                color = URGENT if left <= timedelta(minutes=WARN_BEFORE[0]) else ACCENT
        elif not done and due_dt(it) < now:
            meta += "  •  فات وقته"
            color = RED
        self.base_card(it, done, MUTED if done else TEXT, meta, color,
                       lambda: self.toggle_reminder(it))

    def group_title(self, text, color, count, action=None):
        row = ctk.CTkFrame(self.list, fg_color="transparent")
        row.pack(fill="x", pady=(12, 2), padx=6)
        ctk.CTkLabel(row, text=text, font=font(14, "bold"), text_color=color).pack(side="right")
        ctk.CTkLabel(row, text=str(count), font=font(12), text_color=MUTED).pack(side="right", padx=8)
        if action:
            label, cmd = action
            ctk.CTkButton(row, text=label, width=70, height=26, corner_radius=13, fg_color="transparent",
                          hover_color=FIELD, text_color=MUTED, font=font(12), command=cmd).pack(side="left")

    def empty(self, msg):
        ctk.CTkLabel(self.list, text=msg, font=font(15), text_color=MUTED).pack(pady=50)

    def refresh(self):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        greet = "صباح الخير" if 4 <= now.hour < 12 else "مساء الخير" if now.hour < 18 else "هلا والله"
        self.hello.configure(text=f"{greet} 👋")
        self.date_lbl.configure(text=f"{DAYS[now.weekday()]}، {now.day} {MONTHS[now.month - 1]} {now.year}")

        self.editing = False
        for w in self.list.winfo_children():
            w.destroy()

        if self.section == "المهام":
            self.refresh_tasks()
        else:
            self.refresh_reminders(today)

        self.render_widget()
        pending = sum(1 for i in self.items if kind(i) == "task" and not i["done"])
        if getattr(self, "tray", None):
            self.tray.title = f"تذكيراتي — {pending} مهام باقية" if pending else "تذكيراتي"

    def refresh_tasks(self):
        self.paint_projects()
        tasks = [i for i in self.items if kind(i) == "task"]
        if self.project is not None:
            tasks = [i for i in tasks if i.get("project") == self.project]
        urgent = [i for i in tasks if not i["done"] and i.get("urgent")]
        normal = [i for i in tasks if not i["done"] and not i.get("urgent")]
        done = [i for i in tasks if i["done"]]
        total = len(tasks)

        self.prog_title.configure(text="تقدم المهام")
        self.prog_count.configure(text=f"{len(done)} من {total}" if total else "ما عندك مهام")
        self.bar.set(len(done) / total if total else 0)

        if not tasks:
            self.empty("ما فيه مهام في هذا القسم 🎉\nأضف مهمة من فوق" if self.project else "ما فيه مهام 🎉\nأضف مهمة من فوق")
            return
        for title, color, group, action in (
                ("🔥  مستعجلة", URGENT, urgent, None),
                ("عادية", ACCENT, normal, None),
                ("✓  منجزة", GREEN, done, ("مسح الكل", self.clear_done))):
            if group:
                self.group_title(title, color, len(group), action)
                for it in group:
                    self.task_card(it)

    def refresh_reminders(self, today):
        rems = [i for i in self.items if kind(i) == "reminder"]
        today_rows = [i for i in rems if i["date"] == today or i.get("done_at") == today]
        n_done = sum(1 for i in today_rows if i["done"] or (i.get("done_at") == today and i["date"] != today))
        total = len(today_rows)
        self.prog_title.configure(text="تذكيرات اليوم")
        self.prog_count.configure(text=f"{n_done} من {total}" if total else "ما عندك شي")
        self.bar.set(n_done / total if total else 0)

        rows = today_rows if self.view == "اليوم" else rems
        if self.view == "اليوم":
            for it in sorted(rows, key=lambda i: (i["done"], due_dt(i))):
                self.reminder_card(it, show_day=False)
        else:
            # الكل: مقسمة حسب اليوم
            by_day = {}
            for it in sorted(rows, key=lambda i: (i["date"], i["done"], due_dt(i))):
                by_day.setdefault(it["date"], []).append(it)
            for date_str, group in by_day.items():
                d = datetime.strptime(date_str, "%Y-%m-%d")
                label = day_label(date_str)
                if label in ("اليوم", "بكرا", "أمس"):
                    label += f"  •  {DAYS[d.weekday()]}"
                else:
                    label = f"{DAYS[d.weekday()]} {label}"
                self.group_title(label, ACCENT if date_str >= today else MUTED, len(group))
                for it in group:
                    self.reminder_card(it, show_day=False)
        if not rows:
            self.empty("يومك فاضي 🎉\nأضف تذكير من فوق" if self.view == "اليوم" else "ما فيه تذكيرات بعد")

    # ---------- المزامنة ----------
    def paint_cloud(self):
        if not CLOUD.logged_in:
            self.cloud_btn.configure(text="☁️  سجّل دخول للمزامنة", text_color=URGENT)
        elif CLOUD.online:
            self.cloud_btn.configure(text="☁️  متزامن", text_color=GREEN)
        else:
            self.cloud_btn.configure(text="☁️  بدون نت", text_color=MUTED)

    def cloud_clicked(self):
        if not CLOUD.logged_in:
            self.open_login()
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=CLOUD.email, state="disabled")
        menu.add_command(label="تغيير كلمة المرور", command=self.change_password)
        menu.add_command(label="تسجيل خروج", command=self.logout)
        b = self.cloud_btn
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() - 50)

    def logout(self):
        CLOUD.logout()
        self.paint_cloud()

    def change_password(self):
        w = ctk.CTkToplevel(self.root, fg_color=BG)
        w.title("تغيير كلمة المرور")
        w.geometry("380x250")
        w.resizable(False, False)
        w.transient(self.root)
        ctk.CTkLabel(w, text="كلمة المرور الجديدة", font=font(18, "bold"), text_color=TEXT,
                     anchor="e").pack(fill="x", padx=24, pady=(20, 0))
        ctk.CTkLabel(w, text="6 حروف أو أكثر. استخدمها بعدها في الجوال والماك",
                     font=font(12), text_color=MUTED, anchor="e").pack(fill="x", padx=24)
        pw = ctk.CTkEntry(w, height=42, corner_radius=12, fg_color=FIELD, border_width=0, font=font(14),
                          text_color=TEXT, placeholder_text="كلمة المرور الجديدة")
        pw.pack(fill="x", padx=24, pady=(14, 0))
        msg = ctk.CTkLabel(w, text="", font=font(12, "bold"), text_color=RED, anchor="e")
        msg.pack(fill="x", padx=24, pady=(6, 0))

        def go():
            p = pw.get().strip()
            if len(p) < 6:
                msg.configure(text="قصيرة، خلها 6 حروف أو أكثر", text_color=RED)
                return
            try:
                CLOUD.change_password(p)
            except cloud.AuthError as err:
                msg.configure(text=cloud.auth_message(err), text_color=RED)
                return
            except OSError:
                msg.configure(text="ما قدرت أتصل، تأكد من النت", text_color=RED)
                return
            msg.configure(text="✓ تغيّرت. ادخل فيها من الجوال", text_color=GREEN)
            w.after(2500, w.destroy)

        ctk.CTkButton(w, text="حفظ", height=40, corner_radius=12, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      font=font(14, "bold"), command=go).pack(fill="x", padx=24, pady=(8, 0))
        pw.bind("<Return>", lambda e: go())
        w.after(100, lambda: (w.lift(), w.focus_force(), pw.focus()))

    def open_login(self):
        if self.login_win is not None:
            self.login_win.lift()
            return
        self.show()
        w = self.login_win = ctk.CTkToplevel(self.root, fg_color=BG)
        w.title("تسجيل الدخول")
        w.geometry("400x370")
        w.resizable(False, False)
        w.transient(self.root)
        w.protocol("WM_DELETE_WINDOW", self.close_login)
        if os.path.exists(ICON_FILE) and not IS_MAC:
            w.after(250, lambda: w.iconbitmap(ICON_FILE))

        ctk.CTkLabel(w, text="☁️  المزامنة", font=font(22, "bold"), text_color=TEXT,
                     anchor="e").pack(fill="x", padx=26, pady=(22, 0))
        ctk.CTkLabel(w, text="سجّل دخول عشان تكون تذكيراتك نفسها على\nالكمبيوتر والماك والجوال",
                     font=font(12), text_color=MUTED, anchor="e", justify="right").pack(fill="x", padx=26)

        field = dict(height=42, corner_radius=12, fg_color=FIELD, border_width=0, font=font(14),
                     text_color=TEXT)
        email = ctk.CTkEntry(w, placeholder_text="الإيميل", **field)
        email.pack(fill="x", padx=26, pady=(16, 8))
        email.insert(0, CLOUD.email or "abwoowd2@gmail.com")
        pw = ctk.CTkEntry(w, placeholder_text="كلمة المرور", show="•", justify="right", **field)
        pw.pack(fill="x", padx=26)
        msg = ctk.CTkLabel(w, text="", font=font(12, "bold"), text_color=RED, wraplength=340,
                           justify="right", anchor="e")
        msg.pack(fill="x", padx=26, pady=(8, 0))

        def go(new):
            e, p = email.get().strip(), pw.get()
            if not e or not p:
                msg.configure(text="اكتب الإيميل وكلمة المرور", text_color=RED)
                return
            msg.configure(text="لحظة...", text_color=MUTED)
            w.update_idletasks()
            try:
                if new and not CLOUD.signup(e, p):
                    msg.configure(text="✓ انرسل لك إيميل تأكيد. اضغط الرابط اللي فيه، "
                                       "وبعدها ارجع هنا واضغط دخول", text_color=GREEN)
                    return
                if not new:
                    CLOUD.login(e, p)
            except cloud.AuthError as err:
                msg.configure(text=cloud.auth_message(err), text_color=RED)
                return
            except OSError:
                msg.configure(text="ما قدرت أتصل، تأكد من النت", text_color=RED)
                return
            self.close_login()
            CLOUD.track(self.items)
            CLOUD.start()
            self.paint_cloud()

        row = ctk.CTkFrame(w, fg_color="transparent")
        row.pack(fill="x", padx=26, pady=(12, 0))
        ctk.CTkButton(row, text="دخول", height=42, corner_radius=12, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=font(14, "bold"),
                      command=lambda: go(False)).pack(side="right", fill="x", expand=True)
        ctk.CTkButton(row, text="حساب جديد", height=42, corner_radius=12, fg_color=FIELD,
                      hover_color=FIELD_HOVER, text_color=TEXT, font=font(14),
                      command=lambda: go(True)).pack(side="left", fill="x", expand=True, padx=(0, 10))
        pw.bind("<Return>", lambda ev: go(False))
        w.after(100, lambda: (w.lift(), w.focus_force(), pw.focus()))

    def close_login(self):
        if self.login_win is not None:
            self.login_win.destroy()
            self.login_win = None

    def apply_remote(self, changes):
        """تعديلات جت من جهاز ثاني. نعدّل نفس العناصر عشان الإشعارات المفتوحة تظل مربوطة فيها."""
        by_id = {i["id"]: i for i in self.items}
        for id_, data in changes:
            cur = by_id.get(id_)
            was = (cur.get("date"), cur.get("time")) if cur else None
            if data is None:
                if cur is not None:
                    self.items.remove(cur)
            elif cur is not None:
                cur.clear()
                cur.update(data)
            else:
                self.items.append(data)
                by_id[id_] = data
            # انحذف أو انتهى أو تأجّل من جهاز ثاني: نسكّر إشعاره هنا
            if data is None or data.get("done") or (was and was != (data.get("date"), data.get("time"))):
                old = self.toasts.pop(id_, None)
                if old is not None:
                    old.win.destroy()
        save(self.items, sync=False)
        CLOUD.accept_remote(self.items)
        self.paint_cloud()
        if not self.editing:
            self.refresh()

    # ---------- منطق ----------
    def toggle_startup(self):
        try:
            set_startup(bool(self.startup.get()))
        except OSError:
            self.startup.toggle()

    def add_task(self):
        text = self.task_title.get().strip()
        if not text:
            self.task_title.focus()
            return
        self.items.append({"id": uuid.uuid4().hex, "kind": "task", "title": text,
                           "urgent": self.urgent, "done": False, "project": self.project})
        save(self.items)
        self.task_title.delete(0, "end")
        self.refresh()

    # ---------- الأقسام ----------
    def projects(self):
        return sorted((i for i in self.items if kind(i) == "project"), key=lambda p: p.get("order", 0))

    def projects_by_id(self):
        return {p["id"]: p for p in self.projects()}

    def build_projects_bar(self):
        self.proj_bar = ctk.CTkFrame(self.sub_holder, fg_color="transparent", height=36)

    def paint_projects(self):
        bar = self.proj_bar
        for c in bar.winfo_children():
            c.destroy()
        projects = self.projects()
        if self.project is not None and self.project not in {p["id"] for p in projects}:
            self.project = None  # انحذف القسم من جهاز ثاني
        counts = {}
        for i in self.items:
            if kind(i) == "task" and not i["done"]:
                counts[i.get("project")] = counts.get(i.get("project"), 0) + 1

        def chip(text, on, color, cmd, pid=None):
            b = ctk.CTkButton(bar, text=text, height=32, corner_radius=16, font=font(13, "bold" if on else "normal"),
                              width=10, fg_color=color if on else FIELD, hover_color=FIELD_HOVER,
                              text_color="#0d0e13" if on else TEXT, command=cmd)
            b.pack(side="right", padx=(0, 6))
            if pid:
                b.bind("<Button-3>", lambda e: self.project_menu(pid, e))
                if IS_MAC:
                    b.bind("<Button-2>", lambda e: self.project_menu(pid, e))
            return b

        total = sum(counts.values())
        chip(f"الكل  {total}" if total else "الكل", self.project is None, ACCENT, lambda: self.pick_project(None))
        for p in projects:
            n = counts.get(p["id"], 0)
            on = self.project == p["id"]
            label = f"●  {p['name']}" + (f"  {n}" if n else "")
            b = chip(label, on, p["color"], lambda pid=p["id"]: self.pick_project(pid), p["id"])
            if not on:
                b.configure(text_color=p["color"])
        ctk.CTkButton(bar, text="＋  قسم", height=32, width=10, corner_radius=16, font=font(13),
                      fg_color="transparent", border_width=1, border_color=FIELD_HOVER, hover_color=FIELD,
                      text_color=MUTED, command=self.new_project).pack(side="right")

    def pick_project(self, pid):
        self.project = pid
        p = self.projects_by_id().get(pid)
        self.task_title.configure(placeholder_text=f"مهمة جديدة في «{p['name']}»" if p else
                                  "وش المهمة؟ (تنفذها في أي وقت)")
        self.refresh()

    def ask_name(self, title, initial=""):
        d = ctk.CTkInputDialog(title=title, text="اسم القسم:", font=font(13), fg_color=BG,
                               button_fg_color=ACCENT, button_hover_color=ACCENT_HOVER, entry_fg_color=FIELD,
                               entry_border_color=FIELD_HOVER, entry_text_color=TEXT)
        if initial:
            d.after(150, lambda: d._entry.insert(0, initial))
        name = d.get_input()
        return name.strip() if name else ""

    def new_project(self):
        name = self.ask_name("قسم جديد")
        if not name:
            return
        projects = self.projects()
        color = PROJECT_COLORS[len(projects) % len(PROJECT_COLORS)]
        pid = uuid.uuid4().hex
        self.items.append({"id": pid, "kind": "project", "name": name, "color": color, "done": False,
                           "order": max((p.get("order", 0) for p in projects), default=0) + 1})
        save(self.items)
        self.pick_project(pid)

    def project_menu(self, pid, e):
        p = self.projects_by_id()[pid]
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="تغيير الاسم", command=lambda: self.rename_project(p))
        colors = tk.Menu(m, tearoff=0)
        for name, c in zip(PROJECT_COLOR_NAMES, PROJECT_COLORS):
            colors.add_command(label=("✓  " if p["color"] == c else "     ") + name,
                               command=lambda c=c: self.recolor_project(p, c))
        m.add_cascade(label="اللون", menu=colors)
        m.add_separator()
        m.add_command(label="حذف القسم", command=lambda: self.delete_project(p))
        m.tk_popup(e.x_root, e.y_root)

    def rename_project(self, p):
        name = self.ask_name("تغيير اسم القسم", p["name"])
        if name:
            p["name"] = name
            save(self.items)
            self.pick_project(self.project)

    def recolor_project(self, p, color):
        p["color"] = color
        save(self.items)
        self.refresh()

    def delete_project(self, p):
        from tkinter import messagebox
        n = sum(1 for i in self.items if kind(i) == "task" and i.get("project") == p["id"])
        msg = f"بينحذف قسم «{p['name']}»." + (f"\nمهامه ({n}) ما بتنحذف، بتصير بدون قسم." if n else "")
        if not messagebox.askyesno("حذف القسم", msg, parent=self.root):
            return
        for i in self.items:
            if kind(i) == "task" and i.get("project") == p["id"]:
                i["project"] = None
        self.items.remove(p)
        save(self.items)
        self.pick_project(None)

    def add_reminder(self):
        text = self.title.get().strip()
        if not text:
            self.title.focus()
            self.flash("اكتب التذكير أول", RED)
            return
        start = self.read_time(self.start_time)
        end = self.read_time(self.end_time) if self.has_end.get() else None
        if start is None or (self.has_end.get() and end is None):
            self.flash("الوقت غلط: الساعة من 1 إلى 12", RED)
            return
        if end == start:
            self.flash("وقت النهاية نفس البداية", RED)
            return
        date = self.chosen_date()
        if any(kind(i) == "reminder" and not i["done"] and i["title"] == text
               and i["date"] == date and i["time"] == start for i in self.items):
            self.flash("هذا التذكير موجود من قبل", URGENT)
            return
        self.items.append({
            "id": uuid.uuid4().hex, "kind": "reminder", "title": text, "date": date,
            "time": start, "end": end, "daily": bool(self.daily.get()), "done": False, "fired": False,
            "sent": [],
        })
        save(self.items)
        self.title.delete(0, "end")
        self.flash(f"✓ انضاف  •  {day_label(date)} {fmt12(start)}")
        # لو انضاف ليوم ثاني وإحنا على تبويب "اليوم"، نروح لـ "الكل" عشان تشوفه
        if self.view == "اليوم" and date != datetime.now().strftime("%Y-%m-%d"):
            self.view = "الكل"
            self.sub_tabs.set("الكل")
        self.refresh()

    def delete(self, it):
        self.items.remove(it)
        save(self.items)
        self.refresh()

    def clear_done(self):
        self.items = [i for i in self.items if not (kind(i) == "task" and i["done"])]
        save(self.items)
        self.refresh()

    def toggle_task(self, it):
        it["done"] = not it["done"]
        save(self.items)
        self.refresh()

    def toggle_urgent(self, it):
        it["urgent"] = not it.get("urgent", False)
        save(self.items)
        self.refresh()

    def toggle_reminder(self, it):
        today = datetime.now().strftime("%Y-%m-%d")
        if it["done"]:
            it["done"] = False
            it.pop("done_at", None)
            save(self.items)
            self.refresh()
        elif not (it.get("done_at") == today and it["date"] != today):
            self.finish(it)

    def finish(self, it):
        it["done_at"] = datetime.now().strftime("%Y-%m-%d")
        if it["daily"]:
            it["date"] = (due_dt(it) + timedelta(days=1)).strftime("%Y-%m-%d")
            it["fired"] = False
            it["sent"] = []
        else:
            it["done"] = True
        save(self.items)
        self.refresh()

    def tick(self):
        now = datetime.now()
        settings = settings_of(self.items)
        for it in list(self.items):
            if kind(it) != "reminder" or it["done"]:
                continue
            if end_dt(it):
                # له فترة: نرسل آخر مرحلة وصلنا لها بس (ما نغرقك لو البرنامج كان مسكّر)
                passed = [name for name, at in stages(it, settings) if at <= now]
                sent = it.setdefault("sent", [])
                if passed and passed[-1] not in sent:
                    sent.extend(p for p in passed if p not in sent)
                    self.fire(it, passed[-1])
            elif not it["fired"] and due_dt(it) <= now and it["id"] not in self.toasts:
                it["fired"] = True
                self.fire(it)
        if hasattr(self, "cloud_btn"):
            self.paint_cloud()
        self.root.after(5000, self.tick)

    def fire(self, it, stage="start"):
        save(self.items)
        old = self.toasts.pop(it["id"], None)
        if old is not None:  # التنبيه الجديد يحل محل القديم
            old.win.destroy()

        def release():
            self.toasts.pop(it["id"], None)

        def done():
            release()
            self.finish(it)

        def snooze(mins):
            release()
            if mins:
                it["date"], it["time"] = (datetime.now() + timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M").split()
                it["fired"] = False
                save(self.items)
            self.refresh()

        self.toasts[it["id"]] = Toast(self.root, it, len(self.toasts), done, snooze, stage)
        self.refresh()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if not already_running():
        App(start_hidden="--tray" in sys.argv).run()
