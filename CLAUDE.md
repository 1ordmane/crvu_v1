# CRVU

Сайт на Python + Flask. Проект на начальной стадии: есть только минимальная структура.

## Стек

- **Backend:** Python 3, Flask (фабрика приложения `create_app` в `app/__init__.py`, маршруты в Blueprint `app/routes.py`).
- **Шаблоны:** Jinja (`app/templates/`), все публичные страницы наследуют `base.html`.
- **Frontend:** HTML, CSS (`app/static/css/`), обычный JavaScript без фреймворков и сборщиков (`app/static/js/`).
- **Контент:** JSON в `content/` (тексты, блоки главной, разделы, контакты, портфолио) и фото в `content/media/`; инструкция — `content/README.md`, проверка — `flask --app app check-content`. Новые тексты и данные класть в контент, а не в шаблоны.
- **Админ-панель:** Blueprint `app/admin.py` (`/admin`), вход и CSRF — `app/auth.py`, загрузка фото (Pillow → WebP 640/1280/1920/2560, исходный файл — в `content/originals/`, вне `/media`) — `app/uploads.py`, атомарная запись JSON с копиями в `content/.history/` — `app/storage.py`. Включается только при `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` в окружении или в локальном `.env` в корне проекта (читает `create_app`, окружение приоритетнее, тесты файл не читают); хеш — `flask --app app hash-password`. Шаблоны админки наследуют `admin/base.html` и стили `admin.css`, без JS и inline-стилей (CSP). Запись в `content/` — только через `storage.write_json`. Видеоотзывы (ссылки RuTube и свои файлы MP4/WebM в `content/media/videos/files/`, обложки — `videos/covers/`) — `app/videos.py`, управление только в Редакторе → «Видеоотзывы» (`admin/_video_library.html`, `static/js/admin-videos.js`); ненужные файлы не удаляются, а `videos.sweep` переносит их в `content/.history/media/`.
- **Тесты:** pytest + тестовый клиент Flask (`tests/`).

Не добавлять React, Tailwind, npm-сборку и другие фреймворки без явного запроса.

## Команды

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

flask --app app run --debug --port 5001   # локальный запуск, http://127.0.0.1:5001
pytest                        # тесты
flask --app app check-content # проверка папки content/
python scripts/export_static.py --base-path /repo --serve  # статическая сборка для GitHub Pages (GITHUB_PAGES.md)
```

## Skills

- **frontend-design** (`frontend-design:frontend-design`) — при разработке визуального интерфейса: композиция, типографика, цвета, адаптивность.
- **systematic-debugging** (`superpowers:systematic-debugging`) — при любой ошибке, падающем тесте или странном поведении: сначала найти первопричину, потом исправлять.
- **openai-security-best-practices** (`openai-security-best-practices:openai-security-best-practices`) — при любой работе, затрагивающей безопасность: формы, пользовательский ввод, сессии, cookie, авторизация, секреты, SQL, XSS, CSRF, заголовки.
- **verification-before-completion** (`superpowers:verification-before-completion`) — перед заявлением, что задача выполнена: запустить тесты и проверки и показать результат.

## Правила работы

1. Перед изменениями изучать существующие файлы и следовать принятым в проекте соглашениям.
2. Не устанавливать зависимости (pip, npm и др.) без необходимости; новую зависимость сначала согласовать и затем добавить в `requirements.txt`.
3. Не изменять файлы за пределами папки проекта `CRVU` без разрешения пользователя.
4. Не выполнять разрушительные команды (`rm -rf`, `git reset --hard`, `git push --force`, удаление данных, перезапись файлов) без подтверждения.
5. Не изменять конфигурацию OmniRoute.

## Соглашения

- Шаблоны: autoescape не отключать; не использовать `|safe` для пользовательских данных; ссылки через `url_for`.
- Данные в JavaScript передавать через `|tojson` или `data-*`-атрибуты; не вставлять непроверенные данные через `innerHTML`.
- Стили: цвета, отступы и размеры — через CSS custom properties в `:root`; mobile first.
- Секреты (например, `SECRET_KEY`) — только из переменных окружения, без значений по умолчанию в коде; `.env` не коммитить.
- Изменения небольшие и сфокусированные; к новой логике — тесты в `tests/`.
