"""`./tb setup`: первый запуск и пошаговый помощник владельца (запускает сам владелец в своём терминале).

  ./tb setup            интерактивно, по этапам: 1) проверка среды (tb.setup_env: Python, websockets, frontend/dist, порт дашборда, запись в state/ и runtime/,
                        доступ к публичному api.bybit.com и расхождение часов); установка websockets (pip --user или .venv) только после вопроса [Y/n];
                        2) режим paper; 3) ключ подписи, токен дашборда (печатается ОДИН раз), токен агента шлюза `api` (в файл, не на экран), ключи Bybit, права;
                        4) мозг: пропустить / DeepSeek (скрытый ввод) / другой облачный (`./tb llm key set`) / локальная модель (LM Studio, Ollama);
                        5) запуск платформы (`./tb start`, paper) и дашборда в браузере. Если Python нет, этап 0 делают обёртки tb / tb.cmd (scripts/bootstrap.*).
       --yes            «да» на установку и запуск без вопросов (ключи без терминала не вводятся); --dry-run печатает команды, ничего не выполняя;
       --no-start       не запускать платформу в конце
  ./tb setup --check [--offline]
                        только таблицы «среда» и «что готово / чего нет» (без TTY, ничего не меняет, значений не читает и не печатает); код 1, если среда не готова;
                        --offline без обращения к api.bybit.com
  ./tb setup bybit [--profile read|trade|testnet]
                        только ключ биржи Bybit (скрытый ввод API key и secret, запись в ~/.config/traider-bot/bybit_*.env, 0600, без печати).
                        read: читающий ключ (баланс), trade: торговый mainnet (БЕЗ права вывода), testnet: тестовая сеть. Нужны только для live/testnet;
                        paper работает без ключа биржи. Проверено только на Bybit (docs/EXCHANGES.md).

Правила: существующее НЕ перезаписывается без явного подтверждения (слово `да` / `yes`); ключи и значения никогда не печатаются и не логируются
(в выводе только имена файлов и признаки есть/нет). Права: POSIX chmod 0600/0700, Windows icacls (иначе предупреждение).
Ключ DeepSeek: имя файла и переменная берутся из config/providers.json (deepseek.key_file / key_env), формат файла `ИМЯ=значение` (строка KEY=VALUE).
"""
import getpass
import json
import os
import re
import stat
import sys

from tb import launcher, paths, setup_env
from tb.control import auth, cli as ctl_cli
from tb.gateway import registry
from tb.llm import keys as llm_keys

AGENT = "api"
PROVIDER = "deepseek"
YES = ("да", "д", "yes", "y")
# профиль ключа Bybit -> пояснение; имена файлов и формат: launcher.PROFILES, launcher.bybit_env_text
BYBIT = {
    "read": "читающий ключ mainnet (права только чтение): баланс и сверка капитала; торговать им нельзя",
    "trade": "торговый ключ mainnet: право Contract Trade, БЕЗ права вывода средств (Withdraw), по возможности привязка к вашему IP",
    "testnet": "ключ тестовой сети testnet.bybit.com (отдельный аккаунт, деньги не настоящие)",
}
_KEY_OK = re.compile(r"^[A-Za-z0-9]{8,128}$")


def _is_tty():
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def keys_dir(environ=None):
    return auth.keys_dir(environ)


def provider_key_spec(root=None):
    """-> (key_file, key_env) провайдера DeepSeek из providers.json или значения по умолчанию."""
    try:
        from tb.core import localcfg
        p = (localcfg.read("providers", root).get("providers") or {}).get(PROVIDER) or {}
        return p.get("key_file") or "deepseek.env", p.get("key_env") or "DEEPSEEK_API_KEY"
    except (OSError, ValueError, AttributeError):
        return "deepseek.env", "DEEPSEEK_API_KEY"


def restrict(path, is_dir=False):
    """POSIX: 0600 (каталог 0700). Windows: icacls (cli.harden). -> True, если права ограничены."""
    if sys.platform.startswith("win"):
        return bool(ctl_cli.harden(path))
    try:
        os.chmod(path, 0o700 if is_dir else 0o600)
        return True
    except OSError:
        return False


def perms_ok(path):
    """-> True | False | None (None: не проверяемо, Windows). Только режим доступа, без чтения содержимого."""
    if os.name != "posix":
        return None
    try:
        return (os.stat(path).st_mode & 0o077) == 0
    except OSError:
        return None


def write_secret_file(path, text):
    """Атомарная запись файла с секретом, права 0600 с самого создания."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    restrict(path)


def has_provider_key(root=None, environ=None):
    """Есть ли непустой ключ DeepSeek в файле (значение не возвращается)."""
    kf, ke = provider_key_spec(root)
    try:
        return bool(llm_keys.present(kf, ke, environ)[0])
    except Exception:
        return False


def agent_state(root=None, environ=None):
    """-> {"agent": bool (есть в agents.json), "hash": bool, "file": bool}: только наличие."""
    try:
        with open(paths.config("agents.json", root=root), encoding="utf-8") as f:
            ag = (json.load(f).get("agents") or {}).get(AGENT)
    except (OSError, ValueError):
        ag = None
    tf = os.path.join(registry.token_dir(environ), AGENT + ".token")
    return {"agent": isinstance(ag, dict), "hash": bool(isinstance(ag, dict) and ag.get("token_hash")), "file": os.path.isfile(tf)}


def _secret_files(kd, kf, env):
    """Файлы с секретами для проверки прав: ключ подписи, ключ провайдера, токен агента, ключи Bybit."""
    out = [auth.control_file(env), os.path.join(kd, kf), os.path.join(registry.token_dir(env), AGENT + ".token")]
    out += [os.path.join(kd, name) for name in launcher.PROFILES.values()]
    return out


def collect(root=None, environ=None):
    """Состояние для итоговой таблицы: [(пункт, ok|None, пояснение)]. Ничего не меняет, значений не читает."""
    env = os.environ if environ is None else environ
    kd = keys_dir(env)
    rows = []
    rows.append(("Каталог ключей", os.path.isdir(kd), kd if os.path.isdir(kd) else "нет каталога %s (создаст помощник)" % kd))
    st = auth.status(env)
    rows.append(("Ключ подписи (control.json)", st["key"], "есть" if st["key"] else "нет: шаг 1"))
    rows.append(("Токен дашборда", st["token"], "есть (в файле только хеш)" if st["token"] else "нет: шаг 2"))
    a = agent_state(root, env)
    ok = a["agent"] and a["hash"] and a["file"]
    rows.append(("Токен агента %s (шлюз)" % AGENT, ok, "готов" if ok else "нет агента в config/agents.json" if not a["agent"] else "нет хеша или файла токена: шаг 3"))
    kf, ke = provider_key_spec(root)
    pk = has_provider_key(root, env)
    rows.append(("Ключ DeepSeek (%s)" % kf, pk, "есть" if pk else "нет: шаг 4 (нужен только для живого мозга)"))
    for prof in ("read", "trade", "testnet"):          # ключи биржи нужны только для live/testnet: отсутствие не ошибка (None)
        ks = launcher.key_status(prof, env)
        rows.append(("Ключ Bybit %s" % prof, True if ks["ok"] else None, "есть" if ks["ok"] else "нет (нужен только для live/testnet): ./tb setup bybit --profile %s" % prof))
    bad = []
    for p in _secret_files(kd, kf, env):
        if os.path.isfile(p) and perms_ok(p) is False:
            bad.append(os.path.basename(p))
    rows.append(("Права файлов ключей", (not bad) if os.name == "posix" else None, "ok (0600)" if not bad and os.name == "posix" else "слишком открыты: %s (помощник исправит)" % ", ".join(bad) if bad else "Windows: проверьте вручную (icacls)"))
    return rows


def render(rows, out=print):
    out("")
    out("%-34s %-8s %s" % ("Что", "Статус", "Примечание"))
    for name, ok, note in rows:
        out("%-34s %-8s %s" % (name, {True: "готово", False: "НЕТ", None: "?"}[ok], note))
    out("")


class Helper(object):
    """Шаги помощника. ask/secret/out подменяются в тестах."""

    def __init__(self, root=None, environ=None, ask=input, secret=getpass.getpass, out=print):
        self.root = root
        self.env = os.environ if environ is None else environ
        self.ask, self.secret, self.out = ask, secret, out

    def yes(self, question, default=False):
        try:
            a = (self.ask("%s [%s]: " % (question, "Да/нет" if default else "да/Нет")) or "").strip().lower()
        except EOFError:
            return False
        return default if not a else a in YES

    def confirm_overwrite(self, what):
        """Перезапись существующего только по явному слову `да` (Enter = нет)."""
        return self.yes("%s уже есть. Перезаписать? Старое перестанет действовать" % what, default=False)

    def step_dir(self):
        kd = keys_dir(self.env)
        if not os.path.isdir(kd):
            os.makedirs(kd, exist_ok=True)
            self.out("создан каталог ключей: %s" % kd)
        restrict(kd, is_dir=True)

    def step_sign_key(self):
        self.out("Шаг 1. Ключ подписи (control.json): им подписываются команды управления, уровень риска и назначения моделей. Он общий для control и risk.")
        if auth.status(self.env)["key"]:
            self.out("  ключ подписи уже есть, не изменяется.")
            return False
        if not self.yes("  создать ключ подписи?", default=True):
            return False
        auth.init_key(self.env)
        restrict(auth.control_file(self.env))
        self.out("  ключ подписи создан (значение не показывается).")
        return True

    def step_dash_token(self):
        self.out("Шаг 2. Токен входа в дашборд (вкладка «Управление»). Печатается ОДИН раз, в файле только хеш.")
        st = auth.status(self.env)
        if not st["key"]:
            self.out("  нет ключа подписи: шаг пропущен.")
            return False
        if st["token"] and not self.confirm_overwrite("  токен дашборда"):
            self.out("  токен не изменён.")
            return False
        if not st["token"] and not self.yes("  создать токен?", default=True):
            return False
        tok = auth.new_token(self.env)
        restrict(auth.control_file(self.env))
        self.out("  ТОКЕН (сохраните в менеджере паролей, второй раз он не покажется):")
        self.out("  " + tok)
        return True

    def step_agent_token(self):
        self.out("Шаг 3. Токен агента шлюза `%s` (его использует API-мозг). Пишется в файл agents/%s.token (0600), на экран не выводится." % (AGENT, AGENT))
        a = agent_state(self.root, self.env)
        if not a["agent"]:
            self.out("  агента %s нет в config/agents.json: шаг пропущен." % AGENT)
            return False
        if a["hash"] and a["file"]:
            if not self.confirm_overwrite("  токен агента %s" % AGENT):
                self.out("  токен не изменён.")
                return False
        elif not self.yes("  создать токен агента?", default=True):
            return False
        tok = registry.new_token()
        registry.set_token_hash(AGENT, registry.hash_token(tok), self.root)
        path = registry.write_token_file(AGENT, tok, self.env)
        restrict(path)
        restrict(os.path.dirname(path), is_dir=True)
        self.out("  токен создан, файл: %s" % os.path.basename(path))
        return True

    def step_provider_key(self):
        kf, ke = provider_key_spec(self.root)
        path = os.path.join(keys_dir(self.env), kf)
        self.out("Шаг 4. Ключ DeepSeek. Нужен только для живого мозга; без него платформа и paper работают с заглушкой/без мозга.")
        self.out("  файл: %s, формат: одна строка %s=<ваш ключ> (без кавычек). Ключ берётся в кабинете DeepSeek." % (path, ke))
        self.out("  Включение мозга: подписчик api в личном слое config/local/subscriptions.json ({\"subscribers\": [{\"id\": \"api\", \"enabled\": true}]}), см. docs/QUICKSTART.md п. 4.")
        if has_provider_key(self.root, self.env):
            if not self.confirm_overwrite("  ключ DeepSeek"):
                self.out("  ключ не изменён.")
                return False
        else:
            a = (self.ask("  [1] ввести ключ сейчас (скрытый ввод), [2] создать пустой шаблон файла, Enter = пропустить: ") or "").strip()
            if a == "2":
                if os.path.exists(path):
                    self.out("  файл уже есть, шаблон не создаётся.")
                    return False
                write_secret_file(path, "# ключ DeepSeek: впишите значение после знака равенства, без кавычек\n%s=\n" % ke)
                self.out("  шаблон создан: %s; впишите ключ вручную (редактор), затем ./tb llm check." % path)
                return True
            if a != "1":
                return False
        val = (self.secret("  ключ (ввод скрыт): ") or "").strip()
        if not val:
            self.out("  пусто: ничего не записано.")
            return False
        write_secret_file(path, "%s=%s\n" % (ke, val))
        val = None
        self.out("  ключ записан в %s (значение не показывается). Проверка: ./tb llm check" % os.path.basename(path))
        return True

    def step_bybit_key(self, profile):
        """Ключ Bybit профиля read|trade|testnet: скрытый ввод key и secret -> bybit_*.env (0600). Значения не печатаются. -> True, если файл записан."""
        hint = BYBIT[profile]
        fname = launcher.PROFILES[profile]
        path = os.path.join(keys_dir(self.env), fname)
        self.out("  %s: %s" % (profile, hint))
        self.out("  файл: %s (формат KEY=VALUE по строке; создаёт помощник)." % path)
        if os.path.isfile(path) and not self.confirm_overwrite("  файл %s" % fname):
            self.out("  не изменён.")
            return False
        key = (self.secret("  API key (ввод скрыт): ") or "").strip()
        sec = (self.secret("  API secret (ввод скрыт): ") or "").strip()
        if not (_KEY_OK.match(key) and _KEY_OK.match(sec)):
            self.out("  пусто или недопустимые символы (ожидаются латинские буквы и цифры, без пробелов и кавычек): ничего не записано.")
            return False
        write_secret_file(path, launcher.bybit_env_text(profile, key, sec))
        key = sec = None
        st = launcher.key_status(profile, self.env)
        self.out("  записано: %s (значения не показываются); файл %s." % (fname, "в порядке" if st["ok"] else "неполный: нет " + ", ".join(st["missing"])))
        self.out("  Проверка связи с биржей: ./tb bybit %s balance" % profile)
        return True

    def step_exchange(self):
        self.out("Шаг 5. Ключи биржи Bybit. Нужны только для live и testnet; paper работает без них (по публичным ценам). Проверено только на Bybit (docs/EXCHANGES.md).")
        done = False
        for prof in ("read", "trade", "testnet"):
            if self.yes("  настроить ключ профиля %s?" % prof, default=False):
                done = self.step_bybit_key(prof) or done
        return done

    def step_perms(self):
        self.out("Шаг 6. Права файлов ключей.")
        kd = keys_dir(self.env)
        kf, _ke = provider_key_spec(self.root)
        fixed = 0
        for p in _secret_files(kd, kf, self.env):
            if os.path.isfile(p) and perms_ok(p) is False:
                if restrict(p):
                    fixed += 1
                    self.out("  исправлены права: %s" % os.path.basename(p))
        if os.name != "posix":
            self.out("  Windows: права выставлены через icacls там, где это удалось; проверьте вручную, что файлы читает только ваш пользователь.")
        elif not fixed:
            self.out("  права в порядке.")

    def run(self):
        self.out("Помощник настройки traider-bot. Ключи и токены не печатаются (кроме токена дашборда, один раз). Enter = значение по умолчанию.")
        self.step_dir()
        self.step_sign_key()
        self.step_dash_token()
        self.step_agent_token()
        self.step_provider_key()
        self.step_exchange()
        self.step_perms()
        rows = collect(self.root, self.env)
        render(rows, self.out)
        self.out("Дальше: ./tb start   (затем открыть дашборд (для paper токен не нужен), нажать «Старт»; подробности docs/QUICKSTART.md)")
        return 0


def decide(present, yes=False, tty=False):
    """Ставить ли недостающее: -> 'skip' (уже есть), 'run' (--yes), 'ask' (вопрос в терминале), 'refuse' (нет терминала и нет --yes: ничего не ставить молча)."""
    if present:
        return "skip"
    if yes:
        return "run"
    return "ask" if tty else "refuse"


def run_cmd(cmd, dry_run=False, out=print, runner=None):
    """Выполняет команду (список), печатая её; dry_run только печатает. -> код возврата (0 для dry_run)."""
    out("  $ %s" % setup_env.shown(cmd))
    if dry_run:
        return 0
    import subprocess
    try:
        return (runner or subprocess.call)(cmd)
    except OSError as e:
        out("  не удалось запустить: %s" % type(e).__name__)
        return 127


class FirstRun(object):
    """Первый запуск `./tb setup`: проверка среды, установка зависимости дашборда, выбор режима, ключи (Helper), LLM-провайдер, запуск платформы и дашборда.
    Каждое действие только после явного вопроса с показом точной команды; --yes отвечает «да» на установку и запуск, --dry-run печатает команды без выполнения.
    Ключи вводит только пользователь (скрытый ввод в существующих механизмах: Helper.step_provider_key, `./tb llm key set`); мастер значений не видит."""

    def __init__(self, root=None, environ=None, ask=input, secret=getpass.getpass, out=print, yes=False, dry_run=False, tty=True,
                 runner=None, checks=None, opener=None, http_get=None):
        self.root = root or paths.root()
        self.env = os.environ if environ is None else environ
        self.ask, self.secret, self.out = ask, secret, out
        self.yes_all, self.dry_run, self.tty = yes, dry_run, tty
        self.runner = runner
        self.checks = checks or (lambda: setup_env.run_checks(self.root))
        self.opener = opener
        self.http_get = http_get

    def yes(self, question, default=True):
        if self.yes_all:
            self.out("%s [Y/n]: y (--yes)" % question)
            return True
        if not self.tty:
            return False
        try:
            a = (self.ask("%s [%s]: " % (question, "Y/n" if default else "y/N")) or "").strip().lower()
        except EOFError:
            return False
        return default if not a else a in YES

    def tb(self, *args):
        """Команда `./tb ...` тем же интерпретатором (без обёртки, чтобы не зависеть от ОС)."""
        return [sys.executable, "-m", "tb.launcher"] + list(args)

    def run(self, cmd):
        return run_cmd(cmd, self.dry_run, self.out, self.runner)

    # 1. среда
    def stage_env(self):
        self.out("Этап 1. Проверка среды")
        rows = self.checks()
        setup_env.render(rows, self.out)
        return rows

    def stage_deps(self, rows):
        """Пакет websockets (дашборд). pip --user; системный Python с запретом pip (PEP 668) -> виртуальное окружение .venv в корне проекта (обёртки tb его предпочитают)."""
        ws = next((r for r in rows if r["id"] == "websockets"), None)
        how = decide(ws is None or ws["status"] == "ok", self.yes_all, self.tty)
        if how == "skip":
            return True
        if how == "refuse":
            self.out("нет пакета websockets: без терминала ничего не ставлю. Выполните: %s" % setup_env.shown(setup_env.pip_install_cmd()))
            return False
        if setup_env.externally_managed():
            vpy = setup_env.venv_python(self.root)
            self.out("Системный Python запрещает pip (PEP 668). Предлагаю виртуальное окружение .venv в папке проекта (обёртки ./tb подхватят его сами).")
            cmds = [[sys.executable, "-m", "venv", os.path.join(self.root, ".venv")], [vpy, "-m", "pip", "install", "websockets>=13"]]
        else:
            cmds = [setup_env.pip_install_cmd()]
        for c in cmds:
            self.out("  будет выполнено: %s" % setup_env.shown(c))
        if not self.yes("Поставить зависимость дашборда (websockets)?"):
            self.out("  пропущено: дашборд не запустится, пока пакета нет.")
            return False
        for c in cmds:
            if self.run(c) != 0:
                self.out("  команда завершилась с ошибкой. На Debian/Ubuntu без pip: sudo apt install python3-pip python3-venv; затем ./tb setup ещё раз.")
                return False
        if os.path.isfile(setup_env.venv_python(self.root)) and not setup_env.in_venv():
            self.out("  готово. Создано .venv: перезапустите ./tb setup, чтобы дальше работать уже из него.")
        return True

    # 2. режим
    def stage_mode(self):
        self.out("Этап 2. Режим. По умолчанию paper: бумажная торговля по реальным публичным ценам Bybit, ключ биржи не нужен, деньги не двигаются.")
        self.out("  testnet и live включаются только владельцем отдельно (ключи: ./tb setup bybit; порядок: docs/RUNBOOK.md, docs/CONTROL.md). Здесь live не включается никогда.")
        return "paper"

    # 3. ключи и токены (прежний помощник)
    def stage_keys(self):
        self.out("Этап 3. Ключ подписи, токены, ключи (прежний помощник; Enter = значение по умолчанию).")
        if not self.tty:
            self.out("  без терминала пропущено (скрытый ввод невозможен); ключ подписи и токен создаст ./tb start сам.")
            return False
        h = Helper(self.root, self.env, ask=self.ask, secret=self.secret, out=self.out)
        h.step_dir()
        h.step_sign_key()
        h.step_dash_token()
        h.step_agent_token()
        h.step_exchange()
        h.step_perms()
        return True

    # 4. LLM
    def probe_local(self, base):
        """Отвечает ли локальный OpenAI-совместимый сервер: -> список имён моделей или None."""
        try:
            data = (self.http_get or setup_env._http_json)(base.rstrip("/") + "/models")
            return [m.get("id") for m in (data or {}).get("data") or [] if isinstance(m, dict) and m.get("id")]
        except Exception:
            return None

    def stage_llm(self):
        self.out("Этап 4. Мозг (LLM). Без него платформа, дашборд и paper работают; день стартует с консервативным планом.")
        self.out("  [1] пропустить   [2] DeepSeek (облако, ключ)   [3] другой облачный (OpenAI, Anthropic, Gemini, OpenRouter...)   [4] локальная модель (LM Studio, Ollama)")
        if not self.tty:
            self.out("  без терминала пропущено: ключ вводится только вручную. Позже: ./tb setup в терминале или docs/PROVIDERS.ru.md")
            return None
        try:
            a = (self.ask("  выбор [1]: ") or "1").strip()
        except EOFError:
            a = "1"
        if a == "2":
            if Helper(self.root, self.env, ask=self.ask, secret=self.secret, out=self.out).step_provider_key() or has_provider_key(self.root, self.env):
                self.offer_brain_enable("deepseek")
            return "deepseek"
        if a == "3":
            pid = (self.ask("  id шаблона (openai, anthropic, gemini, groq, mistral, openrouter; список: ./tb llm presets): ") or "").strip().lower()
            if not re.match(r"^[a-z0-9_-]{2,32}$", pid):
                self.out("  пусто или недопустимое имя: пропущено.")
                return None
            if pid not in ("deepseek", "anthropic", "local"):
                self.run(self.tb("llm", "provider", "add", pid, "--preset", pid))
            self.out("  ключ вводится скрытым вводом самой командой (значение мастер не видит):")
            if self.run(self.tb("llm", "key", "set", pid)) == 0:
                self.run(self.tb("llm", "provider", "enable", pid))
                self.offer_brain_enable(pid)
            self.out("  дальше: профиль и роли, docs/PROVIDERS.ru.md шаг 4 (./tb brain profile set ... ; ./tb brain use all ...)")
            return pid
        if a == "4":
            return self.stage_local()
        self.out("  пропущено. Позже: ./tb llm key set deepseek или docs/PROVIDERS.ru.md")
        return None

    def stage_local(self):
        try:
            k = (self.ask("  [1] LM Studio (http://127.0.0.1:1234/v1)   [2] Ollama (http://127.0.0.1:11434/v1)   выбор [1]: ") or "1").strip()
        except EOFError:
            k = "1"
        pid, base = ("local", "http://127.0.0.1:11434/v1") if k == "2" else ("lmstudio", "http://127.0.0.1:1234/v1")
        models = self.probe_local(base)
        if models is None:
            self.out("  %s не отвечает. Запустите сервер (LM Studio: Developer -> Start Server; Ollama: ollama serve) и повторите ./tb setup." % base)
            return None
        if not models:
            self.out("  сервер отвечает, но моделей нет: загрузите модель (Ollama: ollama pull qwen2.5:14b) и повторите.")
            return None
        self.out("  модели на сервере: %s" % ", ".join(models[:8]))
        name = (self.ask("  какую использовать [%s]: " % models[0]) or models[0]).strip()
        alias = "main" if pid == "lmstudio" else "qwen"
        cmds = []
        if pid == "lmstudio":
            cmds.append(self.tb("llm", "provider", "add", "lmstudio", "--preset", "lmstudio"))
        cmds += [self.tb("llm", "model", "set", pid, alias, "--name", name, "--tools", "--json-mode"), self.tb("llm", "provider", "enable", pid),
                 self.tb("brain", "profile", "set", "loc-main", "--provider", pid, "--model", alias)]
        for c in cmds:
            self.out("  будет выполнено: %s" % setup_env.shown(c))
        if not self.yes("  Подключить локальную модель так?"):
            return None
        for c in cmds:
            self.run(c)
        self.out("  локальные модели слабее: начните с ролей entry_scout/news_reader: ./tb brain role chain entry_scout --primary loc-main (docs/PROVIDERS.ru.md, раздел 8)")
        self.offer_brain_enable(pid)
        return pid

    def offer_brain_enable(self, pid):
        self.out("  включить подписчика мозга api (личный слой config/local/subscriptions.json; выключить: ./tb brain disable)")
        if self.yes("  Включить мозг?", default=True):
            self.run(self.tb("brain", "enable"))
            self.run(self.tb("llm", "check", "--provider", pid))

    # 5. запуск
    def stage_start(self):
        from tb.platform import services as S
        try:
            url = S.link_of("dash", S.load_cfg(self.root)) or "http://127.0.0.1:%d/" % setup_env.DASH_PORT
        except Exception:
            url = "http://127.0.0.1:%d/" % setup_env.dash_port(self.root)
        self.out("Этап 5. Запуск платформы (paper, в фоне) и дашборда %s" % url)
        if not self.yes("Запустить платформу сейчас?"):
            self.out("  позже: ./tb start, затем откройте %s" % url)
            return False
        if self.run(self.tb("start")) != 0:
            self.out("  платформа не запустилась: ./tb status, ./tb logs")
            return False
        if self.dry_run:
            return True
        if self.yes("Открыть дашборд в браузере?"):
            import webbrowser
            (self.opener or webbrowser.open)(url)
        self.out("  В дашборде: кнопка «Начать торговлю» вверху слева, режим paper.")
        return True

    def main(self, start=True):
        self.out("Stoa: первый запуск. Ключи и токены не печатаются (кроме токена дашборда, один раз). Ничего не ставится без вашего ответа «да».%s" % (" Режим --dry-run: команды только печатаются." if self.dry_run else ""))
        rows = self.stage_env()
        deps = self.stage_deps(rows)
        if any(r["id"] == "python" and r["status"] == "fail" for r in rows):
            self.out("Python старше 3.9: обновите его (обёртка ./tb setup предложит команду для вашей ОС) и повторите.")
            return 3
        self.stage_mode()
        self.stage_keys()
        self.stage_llm()
        render(collect(self.root, self.env), self.out)
        # повторная проверка после установки; в --dry-run ничего не ставилось, поэтому нехватка websockets там не блокирует показ следующих шагов
        blocking = [r for r in setup_env.failed(self.checks()) if not (self.dry_run and r["id"] == "websockets")]
        if blocking:
            self.out("Не готово к запуску: %s. Исправьте и повторите ./tb setup." % ", ".join(r["name"] for r in blocking))
            return 1
        if start:
            self.stage_start()
        else:
            self.out("Дальше: ./tb start, затем откройте дашборд.")
        return 0


USAGE = "использование: ./tb setup [--yes] [--dry-run] [--no-start] | ./tb setup --check [--offline] | ./tb setup bybit [--profile read|trade|testnet]"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv and argv[0] in ("--check", "check"):
        rest = argv[1:]
        if any(a not in ("--offline",) for a in rest):
            print(USAGE, file=sys.stderr)
            return 2
        rows = setup_env.run_checks(paths.root(), offline="--offline" in rest)
        setup_env.render(rows)
        render(collect())
        return 1 if setup_env.failed(rows) else 0
    if argv and argv[0] == "bybit":
        rest = argv[1:]
        prof = rest[1] if len(rest) == 2 and rest[0] == "--profile" else ("read" if not rest else None)
        if prof not in BYBIT:
            print("использование: ./tb setup bybit [--profile read|trade|testnet]", file=sys.stderr)
            return 2
        if not _is_tty():
            print("./tb setup bybit нужен интерактивный терминал (ключи вводятся скрытым вводом)", file=sys.stderr)
            return 2
        try:
            h = Helper()
            h.step_dir()
            return 0 if h.step_bybit_key(prof) else 1
        except (KeyboardInterrupt, EOFError):
            print("\nпрервано", file=sys.stderr)
            return 130
    flags = {"--yes", "-y", "--dry-run", "--no-start"}
    if any(a not in flags for a in argv):
        print(USAGE, file=sys.stderr)
        return 2
    yes, dry, start = ("--yes" in argv or "-y" in argv), "--dry-run" in argv, "--no-start" not in argv
    tty = _is_tty()
    if not tty and not yes and not dry:
        print("./tb setup нужен интерактивный терминал (вопросы и скрытый ввод ключей). Только таблица: ./tb setup --check; без вопросов: ./tb setup --yes", file=sys.stderr)
        return 2
    try:
        return FirstRun(yes=yes, dry_run=dry, tty=tty).main(start=start)
    except (KeyboardInterrupt, EOFError):
        print("\nпрервано, сделанное сохранено", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
