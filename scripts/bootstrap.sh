#!/bin/sh
# Этап 0 первого запуска на macOS/Linux/WSL: поставить Python 3.9+, если его нет, и передать управление `./tb setup` (дальше всё на Python).
# Вызывается из ./tb, когда подходящего Python не нашлось и команда `setup`. Можно запускать и напрямую: sh scripts/bootstrap.sh [флаги].
#   --yes       ставить без вопросов (только если есть права: root или sudo без пароля не требуется, sudo спросит пароль сам)
#   --dry-run   только напечатать команды, ничего не выполнять
#   --check     только сказать, чего не хватает, и выйти (код 3, если Python нет)
# Без терминала и без --yes ничего не ставится: печатается инструкция, код 3. Повторный запуск при уже стоящем Python сразу передаёт управление ./tb setup.
# Остальные флаги передаются в ./tb setup как есть.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
YES=0; DRY=0; CHECK=0
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --dry-run) DRY=1 ;;
    --check) CHECK=1 ;;
  esac
done
PY_VER=3.12
PKG_URL="https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"

say() { printf '%s\n' "$*"; }
tty_ok() { [ -t 0 ] && [ -t 1 ]; }

py_ok() {
  if [ "$(uname -s)" = "Darwin" ] && [ "$(command -v "$1" 2>/dev/null)" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
    return 1   # заглушка xcode-select: вызов открыл бы окно установки Command Line Tools
  fi
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

find_py() {
  for c in "${TB_PYTHON:-}" "$ROOT/.venv/bin/python" python3 python "python$PY_VER" /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           "/Library/Frameworks/Python.framework/Versions/$PY_VER/bin/python3"; do
    [ -n "$c" ] || continue
    if py_ok "$c"; then printf '%s' "$c"; return 0; fi
  done
  return 1
}

ask() {   # ask "вопрос" -> 0 = да
  if [ "$YES" = 1 ]; then say "$1 [Y/n]: y (--yes)"; return 0; fi
  tty_ok || return 1
  printf '%s [Y/n]: ' "$1"
  read -r ans || return 1
  case "$ans" in ""|y|Y|yes|Yes|да|Да|д) return 0 ;; *) return 1 ;; esac
}

run() {   # run команда... : печать и выполнение (или только печать в --dry-run)
  say "  \$ $*"
  [ "$DRY" = 1 ] && return 0
  "$@"
}

sudo_prefix() {
  if [ "$(id -u)" = 0 ]; then printf ''; elif command -v sudo >/dev/null 2>&1; then printf 'sudo'; else printf 'NOSUDO'; fi
}

is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

# команда установки для этой ОС (печатается и выполняется после вопроса)
plan_install() {
  OS="$(uname -s)"
  if [ "$OS" = "Darwin" ]; then
    if command -v brew >/dev/null 2>&1; then
      PLAN="brew install python@$PY_VER"
    else
      PLAN="pkg"
    fi
    return 0
  fi
  S="$(sudo_prefix)"
  if [ "$S" = "NOSUDO" ]; then PLAN=""; return 1; fi
  S="${S:+$S }"
  if command -v apt-get >/dev/null 2>&1; then PLAN="${S}apt-get update && ${S}apt-get install -y python3 python3-venv python3-pip"
  elif command -v dnf >/dev/null 2>&1; then PLAN="${S}dnf install -y python3 python3-pip"
  elif command -v yum >/dev/null 2>&1; then PLAN="${S}yum install -y python3 python3-pip"
  elif command -v pacman >/dev/null 2>&1; then PLAN="${S}pacman -S --needed --noconfirm python python-pip"
  elif command -v zypper >/dev/null 2>&1; then PLAN="${S}zypper --non-interactive install python3 python3-pip"
  elif command -v apk >/dev/null 2>&1; then PLAN="${S}apk add python3 py3-pip"
  else PLAN=""; return 1
  fi
  return 0
}

manual_help() {
  say "Поставьте Python 3.9+ вручную и повторите ./tb setup:"
  say "  macOS: brew install python@$PY_VER  или установщик с https://www.python.org/downloads/macos/"
  say "  Debian/Ubuntu/WSL: sudo apt-get install -y python3 python3-venv python3-pip"
  say "  Fedora/RHEL: sudo dnf install -y python3 python3-pip   Arch: sudo pacman -S python python-pip"
  say "  openSUSE: sudo zypper install python3 python3-pip   Alpine: sudo apk add python3 py3-pip"
  say "  Python стоит в другом месте: TB_PYTHON=/путь/к/python3 ./tb setup"
}

handoff() {   # handoff python [флаги]: дальше ./tb setup этим интерпретатором (TB_PYTHON, чтобы ./tb не искал заново и не вернулся сюда)
  P="$1"; shift
  say "Python найден: $("$P" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'). Дальше ./tb setup."
  TB_PYTHON="$P"; export TB_PYTHON
  if [ "$CHECK" = 1 ]; then exec "$ROOT/tb" setup --check; fi
  exec "$ROOT/tb" setup "$@"
}

if PY="$(find_py)"; then
  handoff "$PY" "$@"
fi

say "Stoa: не найден Python 3.9 или новее."
if is_wsl; then say "(WSL: ставится внутрь Linux-дистрибутива, не в Windows.)"; fi
if [ "$CHECK" = 1 ]; then manual_help; exit 3; fi

if ! plan_install; then
  say "Не удалось определить менеджер пакетов или нет sudo."
  manual_help
  exit 3
fi

if [ "$PLAN" = "pkg" ]; then
  say "Homebrew не найден. Предлагаю официальный установщик python.org (нужен пароль администратора):"
  TMP="${TMPDIR:-/tmp}/stoa-python-$PY_VER.pkg"
  say "  curl -fL -o $TMP $PKG_URL"
  say "  sudo installer -pkg $TMP -target /"
else
  say "Предлагаю команду для вашей системы:"
  say "  $PLAN"
fi

if ! tty_ok && [ "$YES" != 1 ] && [ "$DRY" != 1 ]; then
  say "Нет интерактивного терминала: молча ничего не ставлю. Выполните команду выше сами или запустите ./tb setup --yes."
  exit 3
fi
if [ "$DRY" = 1 ]; then
  say "--dry-run: команды только напечатаны, ничего не выполнено."
  exit 0
fi
if ! ask "Установить Python так?"; then
  say "Отменено."
  manual_help
  exit 3
fi

if [ "$PLAN" = "pkg" ]; then
  run curl -fL -o "$TMP" "$PKG_URL" || { say "Не удалось скачать установщик."; exit 3; }
  run sudo installer -pkg "$TMP" -target / || { say "Установка не удалась."; exit 3; }
  rm -f "$TMP"
else
  say "  \$ $PLAN"
  sh -c "$PLAN" || { say "Установка не удалась."; manual_help; exit 3; }
fi
hash -r 2>/dev/null || true

if PY="$(find_py)"; then
  handoff "$PY" "$@"
fi
say "Python поставлен, но в этом терминале ещё не виден (PATH). Откройте новый терминал и повторите ./tb setup."
say "Пакеты дистрибутива бывают старше 3.9 (например CentOS 7): тогда поставьте python3.11 из репозитория вашей ОС или через pyenv."
exit 3
