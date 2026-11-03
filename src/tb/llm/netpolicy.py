"""Сетевая политика адресов провайдеров LLM (защита от SSRF и утечки ключа на внутренние сервисы). Чистый модуль: без файлов и сети (резолвер подставляется).

Два уровня:
- статический `check_url(url, allow_private)` при сохранении записи реестра (`providers._check_provider`): схема, userinfo/query/fragment, литералы IP, имена;
- динамический `check_ips(host, scheme, ips, allow_private)` в транспорте ПОСЛЕ резолва имени, на каждое соединение (против DNS rebinding).

Классы адреса: loopback (127.0.0.0/8, ::1, localhost) допустим всегда, в том числе по http; private (RFC1918, ULA fc00::/7, внутренние имена) только при `allow_private: true`;
blocked никогда: 0.0.0.0, ::, link-local 169.254.0.0/16 и fe80::/10 (метаданные облака), 100.64.0.0/10 (CGNAT, метаданные Alibaba), multicast, reserved, метаданные AWS по IPv6.
Публичные адреса допустимы только по https. Редиректы транспорт не выполняет.
"""
import ipaddress
import re
from urllib.parse import urlsplit

LOOPBACK, PRIVATE, BLOCKED, PUBLIC = "loopback", "private", "blocked", "public"
_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in ("169.254.0.0/16", "100.64.0.0/10", "fe80::/10", "fd00:ec2::/32", "192.0.0.0/24", "198.18.0.0/15"))
_BLOCKED_NAMES = ("metadata.google.internal", "metadata", "instance-data", "instance-data.ec2.internal", "metadata.azure.com")
_INTERNAL_SUFFIX = (".local", ".internal", ".lan", ".localdomain", ".home.arpa", ".intranet", ".corp", ".private", ".home")
_RE_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def classify_ip(text):
    """-> loopback | private | blocked | public (IPv4-mapped IPv6 разворачивается; zone id отбрасывается). Не IP -> blocked."""
    try:
        ip = ipaddress.ip_address(str(text).split("%", 1)[0])
    except ValueError:
        return BLOCKED
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified:
        return BLOCKED
    if ip.is_loopback:
        return LOOPBACK
    if ip.is_multicast or ip.is_reserved or ip.is_link_local or any(ip in n for n in _BLOCKED_NETS):
        return BLOCKED
    if ip.is_private:
        return PRIVATE
    return PUBLIC if ip.is_global else BLOCKED


def classify_host(host):
    """Имя или литерал IP -> класс по виду имени (без резолва): loopback | private | blocked | public."""
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if not h:
        return BLOCKED
    try:
        ipaddress.ip_address(h.split("%", 1)[0])
        return classify_ip(h)
    except ValueError:
        pass
    if h == "localhost" or h.endswith(".localhost"):
        return LOOPBACK
    if h in _BLOCKED_NAMES:
        return BLOCKED
    if "." not in h or h.endswith(_INTERNAL_SUFFIX):
        return PRIVATE                       # внутреннее имя (однокомпонентное, .local, .internal ...)
    if not _RE_HOST.match(h):
        return BLOCKED
    return PUBLIC


def check_url(url, allow_private=False):
    """Статическая проверка адреса провайдера. -> текст ошибки или None."""
    if not isinstance(url, str) or len(url) > 300 or any(c.isspace() or ord(c) < 32 for c in url):
        return "base_url должен начинаться с http:// или https://"
    try:
        u = urlsplit(url)
        port = u.port
        host = u.hostname
    except ValueError:
        return "base_url некорректен"
    if u.scheme not in ("http", "https") or not host:
        return "base_url должен начинаться с http:// или https://"
    if u.username is not None or u.password is not None or "@" in u.netloc:
        return "base_url: учётные данные в адресе (user:pass@) запрещены"
    if u.query or u.fragment or "?" in url or "#" in url:
        return "base_url: query и fragment запрещены"
    if port is not None and not (0 < port < 65536):
        return "base_url: порт"
    kind = classify_host(host)
    if kind == BLOCKED:
        return "base_url: адрес запрещён (link-local, метаданные облака, 0.0.0.0, ::, служебные диапазоны)"
    if kind == PRIVATE and not allow_private:
        return "base_url: приватный или внутренний адрес разрешён только при allow_private"
    if u.scheme == "http" and kind == PUBLIC:
        return "base_url: http допустим только для loopback и явно разрешённых приватных адресов, для остальных нужен https"
    return None


def check_ips(host, scheme, ips, allow_private=False):
    """Проверка адресов, полученных резолвом имени, перед соединением. -> текст ошибки или None. Все адреса должны проходить (имя не должно вести и в запрещённое)."""
    if not ips:
        return "имя не резолвится"
    kinds = [classify_ip(i) for i in ips]
    if BLOCKED in kinds:
        return "адрес назначения запрещён политикой"
    by_name = classify_host(host)
    if by_name == LOOPBACK:
        if any(k != LOOPBACK for k in kinds):
            return "имя loopback ведёт не на loopback"
    elif any(k in (LOOPBACK, PRIVATE) for k in kinds) and not allow_private:
        return "имя ведёт на приватный адрес (нужен allow_private)"
    if scheme == "http" and any(k == PUBLIC for k in kinds):
        return "http допустим только для loopback и разрешённых приватных адресов"
    return None
