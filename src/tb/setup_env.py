"""Проверка среды для `./tb setup` (первый этап помощника) и `./tb setup --check`. Только чтение: ничего не ставит и не меняет.

Что проверяется (требования взяты из кода, а не из памяти):
  - Python >= 3.9 (весь пакет на стандартной библиотеке);
  - пакет websockets >= 13: единственная обязательная внешняя зависимость, на ней работает дашборд (src/tb/dash2/server.py, requirements.txt);
    ccxt необязателен (кросс-биржевые данные intel), его отсутствие не ошибка;
  - собранный фронт frontend/dist/index.html (лежит в репозитории, Node.js пользователю не нужен);
  - свободный порт дашборда (по умолчанию 8765, `config/local/platform.json` -> svc.dash.port);
  - права на запись в state/ и runtime/ (или в корень, если каталогов ещё нет);
  - доступ к публичному REST Bybit (api.bybit.com, /v5/market/time, без ключей) и расхождение системных часов с часами Bybit.
    Подпись приватных запросов к Bybit поправку часов учитывает сама (venues/bybit.py: sync_time), но метки времени платформы
    (свежесть данных, TTL планов, закрытие свечей) берутся с локальных часов, поэтому расхождение больше 2 с отмечается предупреждением.

Строка результата: {"id", "name", "status": ok|warn|fail|skip, "note", "fix"}. fail значит «без этого платформа не запустится».
Сетевые вызовы и проверка порта подменяются в тестах (параметры fetch, bind).
"""
import importlib
import json
import os
import re
import socket
import sys
import tempfile
import time

MIN_PY = (3, 9)
MIN_WS = (13,)
DASH_PORT = 8765
CLOCK_WARN_S = 2.0
BYBIT_TIME_URL = "https://api.bybit.com/v5/market/time"
_VER_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(text):
    """'Python 3.12.1' | '3.9' | '13.1' | '17.3rc1' -> (3, 12, 1) | (3, 9) | (13, 1) | (17, 3); нет чисел -> None."""
    m = _VER_RE.search(str(text or ""))
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def version_ok(ver, minimum):
    """Сравнение кортежей версий без учёта длины: (3, 9) >= (3, 9, 0)."""
    if not ver:
        return False
    n = max(len(ver), len(minimum))
    pad = lambda v: tuple(v) + (0,) * (n - len(v))
    return pad(ver) >= pad(minimum)


def row(cid, name, status, note="", fix=""):
    return {"id": cid, "name": name, "status": status, "note": note, "fix": fix}


def check_python(info=None):
    v = tuple(info or sys.version_info[:3])
    ok = version_ok(v, MIN_PY)
    note = "%s (%s)" % (".".join(map(str, v)), os.path.basename(sys.executable or "python"))
    return row("python", "Python", "ok" if ok else "fail", note, "" if ok else "нужен Python 3.9+: ./tb setup (поставит или подскажет команду для вашей ОС)")


def externally_managed():
    """PEP 668: системный Python, в который pip ставить нельзя (Debian/Ubuntu 23+, Homebrew). В venv не действует."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return False
    try:
        import sysconfig
        return os.path.isfile(os.path.join(sysconfig.get_paths()["stdlib"], "EXTERNALLY-MANAGED"))
    except (KeyError, OSError):
        return False


def in_venv():
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def module_version(name, importer=importlib.import_module):
    """-> версия установленного модуля как строка, '' (есть, версия неизвестна) или None (нет)."""
    try:
        mod = importer(name)
    except Exception:
        return None
    return str(getattr(mod, "__version__", "") or "")


def shown(cmd):
    """Команда для показа: вместо полного пути интерпретатора только его имя (в выводе нет путей домашнего каталога)."""
    cmd = list(cmd)
    if cmd and os.path.isabs(cmd[0]):
        cmd[0] = os.path.basename(cmd[0])
    return " ".join('"%s"' % c if (" " in c or ">" in c) else c for c in cmd)


def websockets_ok(importer=importlib.import_module):
    v = module_version("websockets", importer)
    pv = parse_version(v) if v else None
    return v is not None and (not pv or version_ok(pv, MIN_WS))


def check_websockets(importer=importlib.import_module):
    v = module_version("websockets", importer)
    if v is None:
        return row("websockets", "Пакет websockets (дашборд)", "fail", "не установлен", "./tb setup предложит поставить; вручную: %s" % shown(pip_install_cmd()))
    pv = parse_version(v)
    if pv and not version_ok(pv, MIN_WS):
        return row("websockets", "Пакет websockets (дашборд)", "fail", "версия %s, нужна 13+" % v, "обновить: %s" % shown(pip_install_cmd()))
    return row("websockets", "Пакет websockets (дашборд)", "ok", v or "есть")


def pip_install_cmd(python=None, venv=None):
    """Команда установки обязательной зависимости. В venv без --user; иначе --user (без прав администратора)."""
    py = python or sys.executable
    inside = in_venv() if venv is None else venv
    return [py, "-m", "pip", "install"] + ([] if inside else ["--user"]) + ["websockets>=13"]


def venv_python(root, windows=None):
    win = sys.platform.startswith("win") if windows is None else windows
    return os.path.join(root, ".venv", "Scripts", "python.exe") if win else os.path.join(root, ".venv", "bin", "python")


def check_ccxt(importer=importlib.import_module):
    v = module_version("ccxt", importer)
    return row("ccxt", "Пакет ccxt (необязательно)", "ok" if v is not None else "skip", v or ("есть" if v == "" else "нет: источник xex в intel молчит, остальное работает"))


def check_dist(root):
    p = os.path.join(root, "frontend", "dist", "index.html")
    ok = os.path.isfile(p)
    return row("dist", "Собранный дашборд (frontend/dist)", "ok" if ok else "fail", "есть" if ok else "нет frontend/dist/index.html",
               "" if ok else "файлы фронта входят в репозиторий: обновите копию (git pull / заново скачайте архив); сборка из исходников: npm --prefix frontend ci && npm --prefix frontend run build")


def dash_port(root):
    """Порт дашборда из config/local/platform.json или config/platform.json (svc.dash.port), иначе 8765. Без импорта платформы."""
    port = DASH_PORT
    for rel in (("config", "platform.json"), ("config", "local", "platform.json")):
        try:
            with open(os.path.join(root, *rel), encoding="utf-8") as f:
                p = (((json.load(f) or {}).get("svc") or {}).get("dash") or {}).get("port")
            if p:
                port = int(p)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return port


def port_free(port, host="127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not sys.platform.startswith("win"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def check_port(root, bind=port_free, running=None):
    """running: True, если платформа уже запущена (тогда порт занят ею самой, это не ошибка)."""
    port = dash_port(root)
    if bind(port):
        return row("port", "Порт дашборда %d" % port, "ok", "свободен")
    if running:
        return row("port", "Порт дашборда %d" % port, "ok", "занят запущенной платформой")
    return row("port", "Порт дашборда %d" % port, "warn", "занят другой программой",
               "закройте её или выберите другой порт: ./tb start --port 8790 (запомнится в config/local/platform.json)")


def writable(path):
    """Можно ли писать в каталог path (если его нет: в ближайший существующий родитель). Пробный файл удаляется."""
    d = path
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tb_wcheck_", dir=d)
        os.close(fd)
        os.remove(tmp)
        return True
    except OSError:
        return False


def check_writable(root):
    bad = [n for n in ("state", "runtime") if not writable(os.path.join(root, n))]
    return row("write", "Запись в state/ и runtime/", "ok" if not bad else "fail", "можно" if not bad else "нельзя писать: %s" % ", ".join(bad),
               "" if not bad else "запускайте из своей копии репозитория, где у пользователя есть права на запись")


def _http_json(url, timeout=6.0):
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # как у сервисов биржи: без прокси
    req = urllib.request.Request(url, headers={"User-Agent": "stoa-setup/1"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def bybit_server_ms(resp):
    """Ответ /v5/market/time -> мс сервера или None."""
    res = (resp or {}).get("result") or {}
    try:
        if res.get("timeNano"):
            return int(res["timeNano"]) // 1000000
        if res.get("timeSecond"):
            return int(res["timeSecond"]) * 1000
        if (resp or {}).get("time"):
            return int(resp["time"])
    except (TypeError, ValueError):
        return None
    return None


def clock_offset_s(fetch=_http_json, clock=time.time):
    """-> (смещение «Bybit минус локальные часы», с; задержка запроса, с). Локальное время берётся серединой запроса."""
    t0 = clock()
    resp = fetch(BYBIT_TIME_URL)
    t1 = clock()
    ms = bybit_server_ms(resp)
    if ms is None:
        raise ValueError("непонятный ответ /v5/market/time")
    return ms / 1000.0 - (t0 + t1) / 2.0, t1 - t0


def time_sync_hint(platform=None, wsl=None):
    p = platform or sys.platform
    if p.startswith("win"):
        return "Windows: Параметры -> Время и язык -> Дата и время -> «Синхронизировать»; или в PowerShell от администратора: w32tm /resync /force"
    if p == "darwin":
        return "macOS: Системные настройки -> Основные -> Дата и время -> «Устанавливать автоматически»; или sudo sntp -sS time.apple.com"
    if wsl if wsl is not None else is_wsl():
        return "WSL: часы отстают после сна Windows: sudo hwclock -s (или wsl --shutdown в Windows и открыть заново); на самой Windows: w32tm /resync"
    return "Linux: sudo timedatectl set-ntp true (с chrony: sudo chronyc makestep)"


def is_wsl():
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def check_network_and_clock(fetch=_http_json, clock=time.time, platform=None):
    """Две строки: доступ к публичному REST Bybit и расхождение часов. Без сети: предупреждение (paper без цен Bybit не торгует)."""
    try:
        off, lat = clock_offset_s(fetch, clock)
    except Exception as e:  # сеть, DNS, TLS, формат
        net = row("bybit", "Доступ к api.bybit.com (публичный)", "warn", "нет ответа: %s" % type(e).__name__,
                  "paper берёт цены с публичного Bybit: проверьте интернет, VPN, фаервол; в песочнице агента сеть закрыта, запустите в обычном терминале")
        return [net, row("clock", "Системные часы", "skip", "не проверены (нет ответа Bybit)")]
    net = row("bybit", "Доступ к api.bybit.com (публичный)", "ok", "отвечает за %d мс" % int(lat * 1000))
    word = "отстают" if off > 0 else "спешат"
    if abs(off) > CLOCK_WARN_S:
        ck = row("clock", "Системные часы", "warn", "%s от Bybit на %.1f с" % (word, abs(off)), time_sync_hint(platform))
    else:
        ck = row("clock", "Системные часы", "ok", "расхождение с Bybit %.1f с" % abs(off))
    return [net, ck]


def platform_running(root):
    try:
        from tb.platform import status as ST
        return bool(ST.supervisor_info(root).get("running"))
    except Exception:
        return False


def run_checks(root, offline=False, fetch=_http_json, bind=port_free, importer=importlib.import_module, clock=time.time, running=None):
    rows = [check_python(), check_websockets(importer), check_ccxt(importer), check_dist(root), check_writable(root),
            check_port(root, bind, platform_running(root) if running is None else running)]
    if offline:
        rows += [row("bybit", "Доступ к api.bybit.com (публичный)", "skip", "пропущено (--offline)"), row("clock", "Системные часы", "skip", "пропущено (--offline)")]
    else:
        rows += check_network_and_clock(fetch, clock)
    return rows


def failed(rows):
    return [r for r in rows if r["status"] == "fail"]


MARK = {"ok": "готово", "warn": "ВНИМАНИЕ", "fail": "НЕТ", "skip": "-"}


def render(rows, out=print):
    out("")
    out("%-36s %-9s %s" % ("Среда", "Статус", "Примечание"))
    for r in rows:
        out("%-36s %-9s %s" % (r["name"], MARK.get(r["status"], r["status"]), r["note"]))
        if r["fix"] and r["status"] in ("warn", "fail"):
            out("%-36s %-9s -> %s" % ("", "", r["fix"]))
    out("")
