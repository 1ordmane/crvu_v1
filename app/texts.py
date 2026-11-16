"""Тексты сайта для редактора в админке: какие поля JSON можно править и как.

Каждая группа — один экран редактора: общие тексты (site.json), первый экран и
блоки главной (home.json), разделы-шторки (pages.json), список контактов
(contacts.json). Поля перечислены здесь явно; всё, чего нет в списке, — адреса,
id, типы, стены и площадь конфигуратора, служебные _comment, неизвестные ключи —
форма не видит и сохраняет как есть.
"""

import hashlib
import json
import re
from collections import namedtuple

LINE_MAX = 200
TEXT_MAX = 1000
PARAGRAPH_MAX = 5000
LIST_MAX = 50
NUMBER_MAX = 10 ** 9

# path — ключи и номера элементов в JSON; kind: line (строка), text (абзац без переносов),
# lines (список, по пункту в строке), paragraphs (список абзацев через пустую строку), int.
Field = namedtuple("Field", "path label kind limit hint default required bounds",
                   defaults=("line", LINE_MAX, "", "", False, None))
Group = namedtuple("Group", "key title file note sections")
# Короткая подпись соцсети в подвале («Мы в соцсетях»), если в contacts.json не задано своё поле short
SOCIAL_SHORT = {"telegram": "ТГ", "max": "МАХ", "vk": "ВК"}


class TextError(Exception):
    pass


def field_name(field):
    return ".".join(str(part) for part in field.path)


def version_of(data):
    """Отпечаток файла: если он изменился между открытием формы и сохранением, правка не применяется."""
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


# --- Описание групп ---

def site_group(site):
    f = Field
    return Group("site", "Общие тексты", "site", "Название, SEO, шапка, меню, подвал, форма заявки, галерея.", (
        ("Название и поиск", (
            f(("company",), "Название компании", hint="В подвале и подписи логотипа. Обязательно.", required=True),
            f(("title",), "Заголовок вкладки браузера (SEO title)", hint="Пусто — используется название компании."),
            f(("description",), "Описание для поисковиков (SEO description)", "text", TEXT_MAX,
              "Короткий абзац, который поисковики показывают под ссылкой."),
        )),
        ("Шапка и меню", (
            f(("nav_home",), "Пункт меню «Главная»", default="Главная"),
            f(("header_button",), "Кнопка в шапке", default="Связаться"),
            f(("floating_button",), "Плавающая кнопка связи", default="Связаться",
              hint="Кнопка в правом нижнем углу; пункты её меню — в группе «Список контактов». Пусто — «Связаться»."),
            f(("ui", "menu"), "Кнопка меню на телефоне", default="Меню"),
            f(("ui", "menu_label"), "Название меню для экранных дикторов", default="Основное меню"),
            f(("ui", "logo_label"), "Подпись ссылки-логотипа после названия компании", default="на главную"),
            f(("ui", "skip_link"), "Ссылка «к содержанию» для клавиатуры", default="Перейти к содержанию"),
        )),
        ("Подвал", (
            f(("footer", "title"), "Заголовок подвала", hint="Пусто — заголовок не показывается."),
            f(("footer", "lead"), "Подзаголовок подвала", "text", TEXT_MAX, "Пусто — не показывается."),
            f(("footer", "button"), "Кнопка в подвале", default="Оставить заявку"),
            f(("footer", "socials_title"), "Заголовок соцсетей в подвале", default="Мы в соцсетях",
              hint="Ссылки и их подписи — в группе «Список контактов». Пусто — «Мы в соцсетях»."),
            f(("footer", "copy"), "Строка копирайта", default="© {year} {company}",
              hint="{year} — текущий год, {company} — название компании."),
        )),
        ("Форма заявки", (
            f(("request", "title"), "Заголовок формы", default="Заявка"),
            f(("request", "name_label"), "Поле имени", default="Ваше имя"),
            f(("request", "contact_label"), "Поле контакта", default="Телефон или Telegram"),
            f(("request", "message_label"), "Поле сообщения", default="Выставка, даты, площадь, бюджет"),
            f(("request", "submit"), "Кнопка отправки", default="Отправить заявку"),
            f(("request", "cancel"), "Кнопка отмены", default="Отмена"),
            f(("request", "subject"), "Тема письма с заявкой", default="Заявка"),
            f(("request", "body_name"), "Строка «Имя» в письме", default="Имя"),
            f(("request", "body_contact"), "Строка «Контакт» в письме", default="Контакт"),
            f(("request", "note_email"), "Подсказка под формой, когда почта для заявок указана", "text", TEXT_MAX),
            f(("request", "note_offline"), "Подсказка под формой, когда почта для заявок не указана", "text", TEXT_MAX),
            f(("request", "offline_message"), "Сообщение после нажатия «Отправить», когда почта не указана",
              "text", TEXT_MAX, default="Онлайн-отправка заявок пока не подключена."),
        )),
        ("Окна и галерея", (
            f(("ui", "close"), "Кнопка «Закрыть» у разделов и галереи", default="Закрыть"),
            f(("ui", "photo_prev"), "Кнопка предыдущего фото", default="Предыдущее фото"),
            f(("ui", "photo_next"), "Кнопка следующего фото", default="Следующее фото"),
            f(("ui", "photo_alt"), "Описание фото в галерее", default="{title}, фото {n} из {total}",
              hint="{title} — название проекта, {n} — номер фото, {total} — сколько всего."),
            f(("ui", "photo_thumb"), "Миниатюра фото", default="Фото {n}"),
            f(("ui", "video"), "Кнопка видео в галерее (и ссылка под карточкой)", default="Смотреть видео"),
            f(("ui", "video_back"), "Кнопка возврата от видео к фото", default="К фото"),
            f(("ui", "video_title"), "Описание плеера видео для экранного диктора", default="{title} — видео",
              hint="{title} — название проекта."),
            f(("ui", "video_error"), "Сообщение, если видео не загрузилось", "text", TEXT_MAX,
              default="Не удалось загрузить видео. Вернитесь к фото или попробуйте позже."),
        )),
    ))


def hero_group(home):
    f = Field
    return Group("hero", "Первый экран", "home", "Заголовок и кнопки главной. Куда ведут кнопки, задаётся в home.json.", (
        ("Первый экран", (
            f(("hero", "title"), "Заголовок", required=True),
            f(("hero", "lead"), "Подзаголовок", "text", TEXT_MAX),
            f(("hero", "primary"), "Основная кнопка", hint="Пусто — кнопка не показывается."),
            f(("hero", "secondary"), "Вторая кнопка", hint="Пусто — кнопка не показывается."),
            f(("hero", "facts"), "Строка под кнопками", "lines", LINE_MAX,
              "По одному пункту в строке. Пустой список — строка не показывается."),
        )),
    ))


SECTION_NAMES = {"configurator": "Конфигуратор", "projects": "Проекты", "text": "Текстовый блок",
                 "faq": "Вопросы", "reviews": "Видеоотзывы"}


def section_group(home, index):
    s = home["sections"][index]
    at = ("sections", index)
    f = Field
    kind = s.get("type")
    head = (f(at + ("title",), "Заголовок блока"), f(at + ("lead",), "Подзаголовок", "text", TEXT_MAX))
    parts = [("Заголовок", head)]
    if kind == "faq":
        for j, item in enumerate(_list(s.get("items"))):
            if isinstance(item, dict):
                parts.append((f"Вопрос {j + 1}", (
                    f(at + ("items", j, "question"), "Вопрос", required=True),
                    f(at + ("items", j, "answer"), "Ответ", "text", TEXT_MAX, required=True),
                )))
    elif kind == "reviews":
        # Сами ролики (content/videos.json) и выбор трёх на главную — в этой же группе Редактора
        parts.append(("Карточки видео", (
            f(at + ("caption",), "Подпись видео без названия", default="Видеоотзыв клиента"),
            f(at + ("placeholder",), "Пустое место", default="Скоро здесь будет видео"),
            f(at + ("play_label",), "Кнопка воспроизведения", default="Смотреть видео"),
            f(at + ("video_error",), "Видео не загрузилось", default="Видео не загрузилось."),
            f(at + ("video_open",), "Ссылка на RuTube", default="Открыть на RuTube"),
        )))
        parts.append(("Другие видео-отзывы", (
            f(at + ("more_button",), "Кнопка под карточками", default="Другие видео-отзывы →"),
            f(at + ("more_title",), "Заголовок окна", default="Другие видео-отзывы"),
        )))
        parts.append(("Призыв под блоком", (
            f(at + ("cta_text",), "Текст", "text", TEXT_MAX),
            f(at + ("cta",), "Кнопка"),
        )))
    elif kind == "configurator":
        parts.append(("Поля", (
            f(at + ("size_label",), "Размер стенда", default="Размер стенда"),
            f(at + ("length_label",), "Длина", default="Длина"),
            f(at + ("width_label",), "Ширина", default="Ширина"),
            f(at + ("area_label",), "Площадь", default="Площадь"),
            f(at + ("decrease_label",), "Кнопка «−» для экранных дикторов", default="Уменьшить"),
            f(at + ("increase_label",), "Кнопка «+» для экранных дикторов", default="Увеличить"),
            f(at + ("type_label",), "Тип стенда", default="Тип стенда"),
            f(at + ("level_label",), "Уровень сложности", default="Уровень сложности"),
            f(at + ("extras_label",), "Дополнительные опции", default="Дополнительные опции"),
            f(at + ("extras_hint",), "Пометка у опций", default="по желанию"),
            f(at + ("extras_count",), "Число выбранных опций", default="выбрано: {n}", hint="{n} — число опций."),
            f(at + ("submit",), "Кнопка отправки", default="Обсудить проект"),
            f(at + ("submit_note",), "Текст под кнопкой", "text", TEXT_MAX,
              default="Обсудим вашу задачу и подготовим проект с учётом ваших пожеланий и бюджета."),
        )))
        rq = at + ("request",)
        parts.append(("Заявка на дизайн-проект", (
            f(rq + ("title",), "Заголовок окна", default="Обсудим ваш проект"),
            f(rq + ("lead",), "Текст под заголовком", "text", TEXT_MAX,
              default="Оставьте контакты, и наш менеджер свяжется с вами."),
            f(rq + ("contact_label",), "Поле контакта", default="Телефон или электронная почта"),
            f(rq + ("contact_placeholder",), "Подсказка в поле", default="Введите телефон или email"),
            f(rq + ("contact_hint",), "Пояснение под полем", default="Укажите любой удобный способ связи."),
            f(rq + ("submit",), "Кнопка отправки", default="Отправить заявку"),
            f(rq + ("cancel",), "Кнопка отмены", default="Отмена"),
            f(rq + ("success_title",), "Заголовок после отправки", default="Спасибо за заявку!"),
            f(rq + ("success_text",), "Текст после отправки", "text", TEXT_MAX,
              default="Мы получили параметры вашего стенда и скоро свяжемся с вами для обсуждения проекта."),
            f(rq + ("close",), "Кнопка закрытия", default="Закрыть"),
        )))
        # Тип с deck (второй этаж) на сайте не показывается — и в редакторе его нет
        types = tuple(f(at + ("types", j, "name"), f"Тип {j + 1}", required=True,
                        hint="Закрытые стороны задаются в home.json.")
                      for j, t in enumerate(_list(s.get("types"))) if isinstance(t, dict) and not t.get("deck"))
        parts.append(("Типы стенда", types))
        parts.append(("План и итог", (
            f(at + ("plan_label",), "Подпись плана до выбора", default="Вид сверху"),
            f(at + ("plan_caption",), "Подпись плана", default="Вид сверху: {type} стенд", hint="{type} — тип стенда."),
            f(at + ("open_sides",), "Пометка об открытых сторонах", default="открытых сторон: {n}",
              hint="{n} — число открытых сторон."),
            f(at + ("summary_text",), "Итог", default="{type} стенд, {length} × {width} м, {area} м²",
              hint="{type}, {length}, {width}, {area} — тип, длина, ширина, площадь."),
            f(at + ("request_intro",), "Первая строка заявки", default="Хочу дизайн-проект стенда:"),
        )))
        parts.append(("Ориентир бюджета", (
            f(at + ("result_title",), "Заголовок", default="Ориентировочный бюджет"),
            f(at + ("result_area",), "Подпись площади", default="Площадь"),
            f(at + ("result_area_value",), "Площадь и размер", default="{area} м² ({length} × {width} м)",
              hint="{area}, {length}, {width} — площадь и стороны."),
            f(at + ("result_config",), "Подпись конфигурации", default="Конфигурация"),
            f(at + ("result_config_value",), "Конфигурация", default="{type}, {level}",
              hint="{type} — тип стенда, {level} — уровень."),
            f(at + ("result_rate",), "Подпись цены за м²", default="За м²"),
            f(at + ("result_total",), "Подпись итога", default="Итого"),
            f(at + ("engineering_note",), "Нужен инженерный расчёт", "text", TEXT_MAX,
              default="{names}: нужен инженерный расчёт, поэтому показана только нижняя граница.",
              hint="{names} — выбранные опции."),
            f(at + ("open_ended_note",), "Уровень без верхней границы", "text", TEXT_MAX,
              default="Для этого уровня верхнюю границу определяет проект."),
            f(at + ("estimate_note",), "Пояснение под расчётом", "text", TEXT_MAX,
              default="Предварительный ориентир, не коммерческое предложение. Итоговая стоимость зависит от "
                      "утверждённого дизайна, материалов, сложности конструкции и объёма работ. Аренда площади, "
                      "сборы организатора, подключение электричества, воды и интернета, подвесные точки и другие "
                      "расходы площадки оплачиваются отдельно."),
            f(at + ("level_summary",), "Заявка: уровень", default="Уровень: {level}"),
            f(at + ("extras_summary",), "Заявка: опции", default="Опции: {extras}"),
            f(at + ("estimate_summary",), "Заявка: ориентир", default="Ориентир бюджета: {range} (предварительно)",
              hint="{range} — диапазон в рублях."),
        )))
        # Названия и описания уровней и опций; числа формулы — только в home.json → pricing
        pricing = s.get("pricing") if isinstance(s.get("pricing"), dict) else {}
        for j, lv in enumerate(_list(pricing.get("levels"))):
            if isinstance(lv, dict):
                parts.append((f"Уровень: {lv.get('name') or lv.get('id')}", (
                    f(at + ("pricing", "levels", j, "name"), "Название", required=True),
                    f(at + ("pricing", "levels", j, "text"), "Описание", "text", TEXT_MAX),
                )))
        extras = tuple(f(at + ("pricing", "extras", j, "name"), f"Опция {j + 1}", required=True,
                         hint="Надбавка и тип опции задаются в home.json → pricing.")
                       for j, e in enumerate(_list(pricing.get("extras"))) if isinstance(e, dict))
        if extras:
            parts.append(("Дополнительные опции", extras))
    elif kind == "projects":
        for j, item in enumerate(_list(s.get("items"))):
            if isinstance(item, dict):
                parts.append((f"Карточка {j + 1}", (
                    f(at + ("items", j, "title"), "Название проекта",
                      hint="Пусто — берётся из связанного проекта портфолио."),
                    f(at + ("items", j, "event"), "Выставка или мероприятие"),
                    f(at + ("items", j, "area"), "Площадь стенда", hint="Например: 54 м²."),
                    f(at + ("items", j, "place"), "Город или страна"),
                    f(at + ("items", j, "image"), "Фото",
                      hint="Путь от content/media/, например home/project-1.webp. Пусто — фото из проекта портфолио."),
                )))
        parts.append(("Подписи и кнопка", (
            f(at + ("more",), "Ссылка на карточке", default="Подробнее"),
            f(at + ("area_label",), "Подпись площади", default="Площадь"),
            f(at + ("button",), "Кнопка под карточками", hint="Пусто — кнопка не показывается."),
        )))
    elif kind == "text":
        parts.append(("Текст", (f(at + ("paragraphs",), "Абзацы", "paragraphs", PARAGRAPH_MAX,
                                  "Абзацы разделяются пустой строкой."),)))
    title = s.get("title") if isinstance(s.get("title"), str) and s.get("title") else s.get("id", "")
    # Видеоотзывы — одна группа «Видеоотзывы»: в ней же библиотека роликов и выбор трёх на главную
    name = SECTION_NAMES[kind] if kind == "reviews" else f"{SECTION_NAMES.get(kind, 'Блок')}: {title}"
    return Group(f"section-{s.get('id')}", name, "home",
                 "Блок главной. id, порядок, значки и площадь конфигуратора задаются в home.json.", tuple(parts))


def page_group(pages, index):
    p = pages["pages"][index]
    at = ("pages", index)
    f = Field
    kind = p.get("type")
    parts = [("Заголовок", (f(at + ("title",), "Заголовок раздела", required=True),
                            f(at + ("nav",), "Пункт меню", hint="Пусто — используется заголовок.")))]
    if kind == "about":
        parts.append(("О компании", (
            f(at + ("founded_year",), "Год основания", "int", bounds=(1800, 2100)),
            f(at + ("since_text",), "Строка о годе основания", default="Работаем с {year} года — уже {years}",
              hint="{year} — год основания, {years} — сколько лет."),
            f(at + ("paragraphs",), "Абзацы", "paragraphs", PARAGRAPH_MAX, "Абзацы разделяются пустой строкой."),
            f(at + ("stats_title",), "Заголовок цифр", default="Компания в цифрах"),
        )))
        for j, stat in enumerate(_list(p.get("stats"))):
            if isinstance(stat, dict):
                parts.append((f"Цифра {j + 1}", (
                    f(at + ("stats", j, "value"), "Число", "int", bounds=(0, NUMBER_MAX)),
                    f(at + ("stats", j, "suffix"), "После числа", limit=10),
                    f(at + ("stats", j, "label"), "Подпись"),
                )))
    elif kind == "portfolio":
        parts.append(("Портфолио", (
            f(at + ("tabs_label",), "Название вкладок по годам для экранных дикторов", default="Год проекта"),
            f(at + ("example_badge",), "Пометка примера", default="Пример"),
            f(at + ("photo_count",), "Число фото", default="{n} фото", hint="{n} — число фото."),
            f(at + ("empty",), "Год без проектов", default="За {year} год проекты скоро появятся.",
              hint="{year} — год."),
        )))
    elif kind == "contacts":
        parts.append(("Контакты", (
            f(at + ("button",), "Кнопка", default="Связаться с менеджером"),
            f(at + ("empty",), "Когда контактов нет", "text", TEXT_MAX, default="Контакты скоро появятся здесь."),
            f(at + ("socials_title",), "Заголовок соцсетей", default="Мы в соцсетях и мессенджерах"),
        )))
    elif kind == "text":
        parts.append(("Текст", (f(at + ("paragraphs",), "Абзацы", "paragraphs", PARAGRAPH_MAX,
                                  "Абзацы разделяются пустой строкой."),)))
    return Group(f"page-{p.get('slug')}", f"Раздел: {p.get('title') or p.get('slug')}", "pages",
                 "Адрес раздела (slug), тип и порядок задаются в pages.json.", tuple(parts))


def contacts_group(contacts):
    f = Field
    parts = []
    for j, c in enumerate(_list(contacts.get("contacts"))):
        if isinstance(c, dict):
            parts.append((f"{c.get('label') or 'Контакт'} ({c.get('type')})", (
                f(("contacts", j, "label"), "Подпись"),
                f(("contacts", j, "value"), "Значение",
                  hint="Пусто — контакт не показывается. Телефон, email и ссылка проверяются при сохранении."),
            )))
    for j, s in enumerate(_list(contacts.get("socials"))):
        if isinstance(s, dict):
            parts.append((f"{s.get('label') or 'Соцсеть'} ({s.get('network')})", (
                f(("socials", j, "label"), "Подпись", required=True),
                f(("socials", j, "short"), "Подпись в подвале", default=SOCIAL_SHORT.get(s.get("network"), ""),
                  hint="Коротко, в строке «Мы в соцсетях» внизу каждой страницы. "
                       f"Пусто — «{SOCIAL_SHORT.get(s.get('network'), '')}»."),
                f(("socials", j, "url"), "Ссылка",
                  hint="Адрес http:// или https://, открывается в новой вкладке. Пусто — ссылка не показывается."),
            )))
    quick_hints = {
        "phone": "Номер в виде tel:+79991234567.",
        "max": "Ссылка из профиля MAX: «Поделиться» → скопировать ссылку (https://max.ru/…). Номер телефона не подходит.",
    }
    for j, q in enumerate(_list(contacts.get("quick"))):
        if isinstance(q, dict):
            parts.append((f"Кнопка «Связаться»: {q.get('label') or q.get('network')}", (
                f(("quick", j, "label"), "Подпись", required=True),
                f(("quick", j, "url"), "Ссылка",
                  hint=quick_hints.get(q.get("network"), "Адрес https://, открывается в новой вкладке.") +
                  " Пусто — пункта нет в меню."),
            )))
    return Group("contacts", "Список контактов", "contacts",
                 "Тип контакта (телефон, email, ссылка) и сеть соцсети (network) задаются в contacts.json.", tuple(parts))


def groups(content):
    """Все группы редактора по текущему контенту, в порядке показа в навигации."""
    result = [site_group(content["site"]), hero_group(content["home"])]
    for i, s in enumerate(_list(content["home"].get("sections"))):
        if isinstance(s, dict) and s.get("type") in SECTION_NAMES and s.get("id"):
            result.append(section_group(content["home"], i))
    for i, p in enumerate(_list(content["pages"].get("pages"))):
        if isinstance(p, dict) and p.get("slug"):
            result.append(page_group(content["pages"], i))
    result.append(contacts_group(content["contacts"]))
    return result


def fields_of(group):
    return [field for _, fields in group.sections for field in fields]


# --- Значения ---

def _get(data, path):
    node = data
    for part in path:
        if isinstance(part, int):
            node = node[part] if isinstance(node, list) and part < len(node) else None
        else:
            node = node.get(part) if isinstance(node, dict) else None
    return node


def display(data, field):
    """Значение для поля формы; отсутствующий ключ показывается текстом, который сейчас видит посетитель."""
    value = _get(data, field.path)
    if value is None:
        return field.default
    if field.kind == "lines":
        return "\n".join(value) if isinstance(value, list) else ""
    if field.kind == "paragraphs":
        return "\n\n".join(value) if isinstance(value, list) else ""
    if field.kind == "int":
        return str(value) if isinstance(value, int) and not isinstance(value, bool) else ""
    return value if isinstance(value, str) else ""


def values_of(data, group):
    return {field_name(field): display(data, field) for field in fields_of(group)}


def _clean(text):
    return " ".join(text.split())


def parse(field, raw):
    """Текст из формы → (значение для JSON, ошибка или None)."""
    if field.kind == "int":
        text = raw.strip()
        lo, hi = field.bounds or (0, NUMBER_MAX)
        if not re.fullmatch(r"\d{1,10}", text) or not lo <= int(text) <= hi:
            return None, f"«{field.label}» — целое число от {lo} до {hi}."
        return int(text), None
    if field.kind in ("lines", "paragraphs"):
        if field.kind == "lines":
            items = [_clean(line) for line in raw.splitlines()]
        else:
            items = [_clean(block) for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n"))]
        items = [item for item in items if item]
        if len(items) > LIST_MAX:
            return None, f"«{field.label}» — не больше {LIST_MAX} пунктов."
        if any(len(item) > field.limit for item in items):
            return None, f"«{field.label}»: каждый пункт — не длиннее {field.limit} символов."
        if field.kind == "lines" and len(set(items)) != len(items):
            return None, f"«{field.label}»: пункты повторяются."
        return items, None
    # Сайт не показывает переносы строк в этих текстах, поэтому пробелы и переносы сводятся к одному пробелу.
    value = _clean(raw)
    if field.required and not value:
        return value, f"Заполните поле «{field.label}»."
    if len(value) > field.limit:
        return value, f"«{field.label}» — не длиннее {field.limit} символов."
    return value, None


def read_form(form, group):
    """Поля группы из формы → (изменения {поле: значение}, введённый текст для повторного показа, ошибки).

    Ключи формы, которых нет в группе, игнорируются; отсутствующее поле — без изменений.
    """
    changes, shown, errors = {}, {}, []
    for field in fields_of(group):
        name = field_name(field)
        if name not in form:
            continue
        shown[name] = form[name]
        value, error = parse(field, form[name])
        if error:
            errors.append(error)
        else:
            changes[field] = value
    return changes, shown, errors


def _fits(field, current):
    if field.kind == "int":
        return isinstance(current, int) and not isinstance(current, bool)
    if field.kind in ("lines", "paragraphs"):
        return isinstance(current, list) and all(isinstance(item, str) for item in current)
    return isinstance(current, str)


def apply(data, changes, file_name):
    """Записать изменения в исходный словарь, не трогая остальные ключи. Возвращает список ошибок.

    Недостающий объект-родитель создаётся только ради непустого значения; недостающий
    элемент списка значит, что файл поменяли, — тогда ничего не пишется.
    """
    errors = []
    for field, value in changes.items():
        *parents, key = field.path
        node = data
        broken = False
        for step, part in enumerate(parents):
            if isinstance(part, int):
                if not isinstance(node, list) or part >= len(node) or not isinstance(node[part], dict):
                    errors.append(f"«{field.label}»: структура {file_name}.json изменилась — обновите страницу.")
                    broken = True
                    break
                node = node[part]
                continue
            # Следующий шаг пути — номер элемента: здесь должен быть список, иначе объект.
            is_list = isinstance(field.path[step + 1], int)
            if part not in node:
                if is_list or value in ("", [], field.default):
                    if is_list:
                        errors.append(f"«{field.label}»: структура {file_name}.json изменилась — обновите страницу.")
                    broken = True
                    break
                node[part] = {}
            if not isinstance(node[part], list if is_list else dict):
                kind = "не список" if is_list else "не объект"
                errors.append(f"«{field.label}»: в {file_name}.json «{part}» {kind} — исправьте файл вручную.")
                broken = True
                break
            node = node[part]
        if broken:
            continue
        current = node.get(key)
        if current is not None and not _fits(field, current):
            errors.append(f"«{field.label}»: в {file_name}.json это поле другого типа — исправьте файл вручную.")
        elif key in node:
            node[key] = value
        elif value not in ("", []) and value != field.default:
            # Ключа не было, а введено то же, что и так показывает сайт, — файл не меняется.
            node[key] = value
    return errors


KIND_NAMES = {"line": "текст", "text": "текст", "lines": "список строк", "paragraphs": "список строк"}


def check(content):
    """Проверка структуры перед записью: известные текстовые поля — нужного типа, их родители — объекты.

    Числа (год основания, цифры) проверяет validate_data. Сообщения начинаются с имени файла.
    """
    errors = []
    for group in groups(content):
        data = content[group.file]
        for field in fields_of(group):
            if field.kind == "int":
                continue
            node, where = data, []
            for part in field.path:
                if node is None:
                    break
                if isinstance(part, int):
                    node = node[part] if isinstance(node, list) and part < len(node) else None
                elif isinstance(node, dict):
                    node = node.get(part)
                else:
                    errors.append(f"{group.file}.json: «{'.'.join(where)}» должен быть объектом {{ … }}")
                    node = None
                    break
                where.append(str(part))
            if node is not None and not _fits(field, node):
                errors.append(f"{group.file}.json: «{field_name(field)}» должен быть: {KIND_NAMES[field.kind]}")
    return sorted(set(errors))
