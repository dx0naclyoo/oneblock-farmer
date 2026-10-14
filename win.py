"""WinAPI через ctypes: поиск окна, захват картинки, ввод (фоновый и обычный)."""
import ctypes
import ctypes.wintypes as wt
import struct
import time
import zlib

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# Координаты должны быть в физических пикселях, иначе при масштабе Windows != 100% всё съедет.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _proto(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


EnumWindows = _proto(user32, "EnumWindows", wt.BOOL, WNDENUMPROC, wt.LPARAM)
GetWindowTextW = _proto(user32, "GetWindowTextW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
GetClassNameW = _proto(user32, "GetClassNameW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
IsWindow = _proto(user32, "IsWindow", wt.BOOL, wt.HWND)
IsWindowVisible = _proto(user32, "IsWindowVisible", wt.BOOL, wt.HWND)
IsIconic = _proto(user32, "IsIconic", wt.BOOL, wt.HWND)
GetClientRect = _proto(user32, "GetClientRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
ClientToScreen = _proto(user32, "ClientToScreen", wt.BOOL, wt.HWND, ctypes.POINTER(wt.POINT))
ScreenToClient = _proto(user32, "ScreenToClient", wt.BOOL, wt.HWND, ctypes.POINTER(wt.POINT))
GetForegroundWindow = _proto(user32, "GetForegroundWindow", wt.HWND)
GetDC = _proto(user32, "GetDC", wt.HDC, wt.HWND)
ReleaseDC = _proto(user32, "ReleaseDC", ctypes.c_int, wt.HWND, wt.HDC)
PrintWindow = _proto(user32, "PrintWindow", wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
GetCursorPos = _proto(user32, "GetCursorPos", wt.BOOL, ctypes.POINTER(wt.POINT))
SetCursorPos = _proto(user32, "SetCursorPos", wt.BOOL, ctypes.c_int, ctypes.c_int)
GetAsyncKeyState = _proto(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
PostMessageW = _proto(user32, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
SetWindowPos = _proto(user32, "SetWindowPos", wt.BOOL, wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, wt.UINT)
HWND_TOPMOST, HWND_NOTOPMOST = wt.HWND(-1), wt.HWND(-2)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

CreateCompatibleDC = _proto(gdi32, "CreateCompatibleDC", wt.HDC, wt.HDC)
CreateDIBSection = _proto(gdi32, "CreateDIBSection", wt.HBITMAP, wt.HDC, ctypes.c_void_p, wt.UINT,
                          ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
SelectObject = _proto(gdi32, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
DeleteObject = _proto(gdi32, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
DeleteDC = _proto(gdi32, "DeleteDC", wt.BOOL, wt.HDC)
BitBlt = _proto(gdi32, "BitBlt", wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD)

WM_SETFOCUS, WM_KILLFOCUS = 0x0007, 0x0008
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002
VK_ESCAPE, SCAN_ESCAPE = 0x1B, 0x01
VK_F8, VK_F9, VK_F10 = 0x77, 0x78, 0x79
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
SRCCOPY = 0x00CC0020


# ---------------------------------------------------------------- окно

def find_window(title_part, window_class="LWJGL"):
    """Видимое окно игры: в заголовке есть title_part, класс окна — window_class (у Minecraft 1.7.10 это LWJGL).

    Класс проверяется, чтобы не схватить браузер, проводник или оверлей с похожим заголовком.
    """
    found = []

    def cb(hwnd, _):
        if IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            GetWindowTextW(hwnd, title, 256)
            if title_part.lower() in title.value.lower():
                cls = ctypes.create_unicode_buffer(256)
                GetClassNameW(hwnd, cls, 256)
                if not window_class or cls.value == window_class:
                    found.append((hwnd, title.value, cls.value))
        return True

    EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        raise RuntimeError(f"Окно игры ('{title_part}' в заголовке) не найдено. Игра запущена?")
    return found[0]


def client_size(hwnd):
    r = wt.RECT()
    GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom


def client_to_screen(hwnd, x, y):
    p = wt.POINT(int(x), int(y))
    ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y


def screen_to_client(hwnd, x, y):
    p = wt.POINT(int(x), int(y))
    ScreenToClient(hwnd, ctypes.byref(p))
    return p.x, p.y


def cursor_pos():
    p = wt.POINT()
    GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def key_down(vk):
    return bool(GetAsyncKeyState(vk) & 0x8000)


# ---------------------------------------------------------------- захват

class Image:
    """Картинка клиентской области окна: BGRA, строки сверху вниз."""

    def __init__(self, w, h, data):
        self.w, self.h, self.data = w, h, data

    def px(self, x, y):
        x = min(max(int(x), 0), self.w - 1)
        y = min(max(int(y), 0), self.h - 1)
        i = (y * self.w + x) * 4
        d = self.data
        return d[i + 2], d[i + 1], d[i]

    def is_blank(self):
        step = max(1, (self.w * self.h) // 4000) * 4
        d = self.data
        return not any(d[i] or d[i + 1] or d[i + 2] for i in range(0, len(d) - 4, step))

    def mark(self, x, y, rgb=(255, 0, 0), size=10):
        """Рисует крестик — чтобы на сохранённом скрине было видно, куда целится бот."""
        d = self.data if isinstance(self.data, bytearray) else bytearray(self.data)
        self.data = d
        bgr = bytes((rgb[2], rgb[1], rgb[0], 255))
        for k in range(-size, size + 1):
            for t in (-1, 0, 1):
                for px, py in ((x + k, y + t), (x + t, y + k)):
                    if 0 <= px < self.w and 0 <= py < self.h:
                        i = (int(py) * self.w + int(px)) * 4
                        d[i:i + 4] = bgr

    def save_png(self, path):
        w, h, d = self.w, self.h, self.data
        rgb = bytearray(w * h * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = d[2::4], d[1::4], d[0::4]
        stride = w * 3
        raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(h))

        def chunk(tag, body):
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
            f.write(chunk(b"IDAT", zlib.compress(raw, 3)))
            f.write(chunk(b"IEND", b""))


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def capture(hwnd, method="printwindow"):
    """Снимок клиентской области.

    printwindow — берёт картинку у DWM, работает и когда окно перекрыто другими (но не свёрнуто).
    screen      — копирует с экрана, годится только когда окно сверху.
    """
    if IsIconic(hwnd):
        raise RuntimeError("Окно игры свёрнуто — свёрнутое окно не рисуется, захват невозможен. "
                           "Разверни его и просто перекрой другими окнами.")
    w, h = client_size(hwnd)
    if w <= 0 or h <= 0:
        raise RuntimeError("У окна игры нулевой размер.")
    src_hwnd = hwnd if method == "printwindow" else None
    hdc_src = GetDC(src_hwnd)
    hdc_mem = CreateCompatibleDC(hdc_src)
    hdr = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = CreateDIBSection(hdc_mem, ctypes.byref(hdr), 0, ctypes.byref(bits), None, 0)
    old = SelectObject(hdc_mem, hbmp)
    try:
        if method == "printwindow":
            PrintWindow(hwnd, hdc_mem, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
        else:
            sx, sy = client_to_screen(hwnd, 0, 0)
            BitBlt(hdc_mem, 0, 0, w, h, hdc_src, sx, sy, SRCCOPY)
        data = ctypes.string_at(bits, w * h * 4)
    finally:
        SelectObject(hdc_mem, old)
        DeleteObject(hbmp)
        DeleteDC(hdc_mem)
        ReleaseDC(src_hwnd, hdc_src)
    return Image(w, h, data)


# ---------------------------------------------------------------- ввод

def _lparam(x, y):
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


class PostInput:
    """Фоновый ввод: сообщения мыши/клавиатуры кладутся прямо в очередь окна игры.

    Настоящие курсор и клавиатура не занимаются, окну не нужно быть активным.
    """
    name = "bg"

    def __init__(self, hwnd):
        self.hwnd = hwnd

    def _post(self, msg, wparam, lparam):
        if not PostMessageW(self.hwnd, msg, wparam, lparam):
            err = ctypes.get_last_error()
            hint = " (игра запущена от администратора? тогда и бота запускай от администратора)" if err == 5 else ""
            raise RuntimeError(f"PostMessage не прошёл, ошибка {err}{hint}")

    def _center(self):
        w, h = client_size(self.hwnd)
        return _lparam(w // 2, h // 2)

    def left_down(self):
        self._post(WM_LBUTTONDOWN, MK_LBUTTON, self._center())

    def left_up(self):
        self._post(WM_LBUTTONUP, 0, self._center())

    def right_click(self):
        lp = self._center()
        self._post(WM_RBUTTONDOWN, MK_RBUTTON, lp)
        time.sleep(0.07)
        self._post(WM_RBUTTONUP, 0, lp)

    def click(self, x, y):
        lp = _lparam(x, y)
        self._post(WM_MOUSEMOVE, 0, _lparam(x + 1, y))
        self._post(WM_MOUSEMOVE, 0, lp)
        time.sleep(0.12)
        self._post(WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.07)
        self._post(WM_LBUTTONUP, 0, lp)

    def esc(self):
        self._post(WM_KEYDOWN, VK_ESCAPE, (SCAN_ESCAPE << 16) | 1)
        time.sleep(0.07)
        self._post(WM_KEYUP, VK_ESCAPE, (SCAN_ESCAPE << 16) | 1 | 0xC0000000)

    def fake_focus(self, on):
        """Говорит игре, что её окно получило/потеряло фокус (на самом деле фокус не меняется).

        Minecraft 1.7.10 возвращает управление игроку после закрытия GUI только если считает
        окно активным, поэтому в фоне без этого копание после Esc не возобновится.
        """
        self._post(WM_SETFOCUS if on else WM_KILLFOCUS, 0, 0)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


SendInput = _proto(user32, "SendInput", wt.UINT, wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_LEFTDOWN, _LEFTUP, _RIGHTDOWN, _RIGHTUP = 0x0002, 0x0004, 0x0008, 0x0010
_KEYUP, _SCANCODE = 0x0002, 0x0008


class SendInput_:
    """Обычный ввод через SendInput: надёжно, но окно игры должно быть активным, мышь занята."""
    name = "fg"

    def __init__(self, hwnd):
        self.hwnd = hwnd

    @staticmethod
    def _mouse(flags):
        inp = _INPUT(0, _INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, flags, 0, 0)))
        SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    @staticmethod
    def _key(scan, up):
        flags = _SCANCODE | (_KEYUP if up else 0)
        inp = _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(0, scan, flags, 0, 0)))
        SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def left_down(self):
        self._mouse(_LEFTDOWN)

    def left_up(self):
        self._mouse(_LEFTUP)

    def right_click(self):
        self._mouse(_RIGHTDOWN)
        time.sleep(0.07)
        self._mouse(_RIGHTUP)

    def click(self, x, y):
        sx, sy = client_to_screen(self.hwnd, x, y)
        SetCursorPos(sx + 1, sy)
        time.sleep(0.03)
        SetCursorPos(sx, sy)
        time.sleep(0.12)
        self._mouse(_LEFTDOWN)
        time.sleep(0.07)
        self._mouse(_LEFTUP)

    def esc(self):
        self._key(SCAN_ESCAPE, False)
        time.sleep(0.07)
        self._key(SCAN_ESCAPE, True)

    def fake_focus(self, on):
        pass
