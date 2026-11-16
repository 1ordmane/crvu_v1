"""Вход в админ-панель, CSRF-токены и ограничение попыток входа.

Администратор один. Логин и хэш пароля берутся только из окружения
(ADMIN_USERNAME, ADMIN_PASSWORD_HASH) или локального .env; хэш создаёт `flask --app app hash-password`.
Пока они или SECRET_KEY не заданы, админ-панель выключена и отдаёт 404.
"""

import hashlib
import hmac
import secrets
import threading
import time

from flask import current_app, request, session
from werkzeug.security import check_password_hash

MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60
LOCKOUT = 15 * 60


def admin_enabled():
    cfg = current_app.config
    return bool(cfg.get("SECRET_KEY") and cfg.get("ADMIN_USERNAME") and cfg.get("ADMIN_PASSWORD_HASH"))


def _password_fingerprint():
    # Смена пароля в окружении завершает все ранее открытые сессии.
    return hashlib.sha256(current_app.config["ADMIN_PASSWORD_HASH"].encode()).hexdigest()[:16]


def is_admin():
    return admin_enabled() and session.get("admin") is True and session.get("pw") == _password_fingerprint()


def check_credentials(username, password):
    cfg = current_app.config
    # Пароль проверяется всегда, чтобы время ответа не выдавало верный логин.
    password_ok = check_password_hash(cfg["ADMIN_PASSWORD_HASH"], password)
    username_ok = hmac.compare_digest(username.encode(), cfg["ADMIN_USERNAME"].encode())
    return password_ok and username_ok


def log_in():
    session.clear()  # новая сессия при входе — защита от фиксации сессии
    session.permanent = True
    session["admin"] = True
    session["pw"] = _password_fingerprint()
    session["csrf"] = secrets.token_urlsafe(32)


def log_out():
    session.clear()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_valid():
    expected = session.get("csrf")
    sent = request.form.get("csrf_token", "")
    return bool(expected) and hmac.compare_digest(sent.encode(), expected.encode())


class LoginLimiter:
    """Блокировка входа после MAX_FAILURES неудач за FAILURE_WINDOW секунд.

    Хранится в памяти процесса: при нескольких процессах сервера у каждого свой
    счётчик. За обратным прокси без ProxyFix все клиенты имеют адрес прокси,
    и блокировка становится общей.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._failures = {}
        self._blocked_until = {}

    def blocked_for(self, key, now=None):
        now = now or time.monotonic()
        with self._lock:
            return max(0, int(self._blocked_until.get(key, 0) - now))

    def fail(self, key, now=None):
        now = now or time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW] + [now]
            self._failures[key] = recent
            if len(recent) >= MAX_FAILURES:
                self._blocked_until[key] = now + LOCKOUT
                self._failures[key] = []

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)
