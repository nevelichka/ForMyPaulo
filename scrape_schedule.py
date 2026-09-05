#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт скачивает страницу расписания с uspu.ru, парсит её и:
  1) сохраняет данные в data/schedule.json
  2) генерирует красивую статическую страницу index.html — сеткой
     по неделям (Пн-Сб), с переключателем RU/EN (бразильское время +8ч
     назад), рубрикой "Us" со случайным фото из 20 и всплывающим
     уведомлением со случайной фразой.

Запускается вручную или по расписанию (cron / Планировщик заданий Windows)
на ВАШЕМ компьютере. Результат затем пушится в GitHub -> отображается
через GitHub Pages и доступен из любой страны.

Использование:
    python scrape_schedule.py
    python scrape_schedule.py --from-file "Расписание_занятий.html"   # офлайн-тест
"""

import argparse
import base64
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://uspu.ru/education/eios/schedule/?group_name=%D0%9E%D0%9B%D0%98-2431"
GROUP_NAME = "ОЛИ-2431"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

OUT_DIR = Path(__file__).resolve().parent
DATA_DIR = OUT_DIR / "data"

# Разница во времени: Екатеринбург -> Бразилиа (и большинство других
# городов восточной Бразилии) — минус 8 часов. Если у друга другой
# город/часовой пояс в Бразилии — поменяйте только эту цифру.
BRAZIL_HOURS_OFFSET = -8


# ---------------------------------------------------------------------------
# Фото ("Us")
# ---------------------------------------------------------------------------
# Фото рядом со скриптом автоматически встраиваются в страницу (base64,
# без отдельной папки с картинками — так проще пушить на GitHub, всё в
# одном index.html). Поддерживаются два варианта имён:
#   - photo.jpg / photo.jpeg / photo.png            — одно фото
#   - photo1.jpg, photo2.jpg, ... photoN.jpg/.png    — сколько угодно фото
# При каждом открытии сайта случайно показывается одно из них. По
# умолчанию у всех фото одинаковый шанс выпасть, но вес любого фото
# можно поменять здесь — чем больше число, тем чаще оно будет
# попадаться. Веса не обязаны давать в сумме 100, это просто пропорции.
PHOTO_EXTENSIONS = ("jpg", "jpeg", "png")
PHOTO_WEIGHTS = {
    # "photo1.jpg": 2,
}


def _photo_to_data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def find_photos() -> list[tuple[str, float]]:
    """Ищет все фото рядом со скриптом и возвращает список
    (data-uri, вес) для каждого найденного файла."""
    photos: list[tuple[str, float]] = []

    for name in ("photo.jpg", "photo.jpeg", "photo.png"):
        path = OUT_DIR / name
        if path.exists():
            photos.append((_photo_to_data_uri(path), PHOTO_WEIGHTS.get(name, 1)))
            break

    n = 1
    while True:
        found_this_n = False
        for ext in PHOTO_EXTENSIONS:
            name = f"photo{n}.{ext}"
            path = OUT_DIR / name
            if path.exists():
                photos.append((_photo_to_data_uri(path), PHOTO_WEIGHTS.get(name, 1)))
                found_this_n = True
                break
        if not found_this_n:
            break
        n += 1

    return photos


# Случайное уведомление при заходе на сайт — все фразы с одинаковой
# вероятностью. Хотите поменять/добавить фразу — просто отредактируйте
# список (кавычки и запятые обязательны).
NOTIF_PHRASES = [
    "I love you.",
    "Hey there, handsome.",
    "Have a great day, my love.",
    "You are my treasure.",
    "I'm thinking about you right now.",
    "I miss you.",
    "Muah!",
    "I want to eat you up.",
    "This is for you \U0001F339",
    "Thank you for being in my life.",
]


# ---------------------------------------------------------------------------
# Переводы
# ---------------------------------------------------------------------------
# Тип занятия (последняя скобка в названии) -> перевод
LESSON_TYPE_EN = {
    "Лекции": "Lecture",
    "Практ.зан-я": "Practical class",
    "Лаб.зан-я": "Lab class",
}

# Название предмета (без типа занятия) -> перевод на английский.
# Список закрытый и собран вручную под конкретную группу — для нового
# предмета, которого нет в словаре, просто добавьте строку сюда.
SUBJECT_EN = {
    "История олигофренопедагогики":
        "History of Special Education for Intellectual Disabilities",
    "Коррекционная работа с обучающимися с умственной отсталостью "
    "(интеллектуальными нарушениями) и расстройствами аутистического спектра":
        "Correctional Work with Students with Intellectual Disabilities "
        "and Autism Spectrum Disorders",
    "Методы количественного и качественного анализа данных":
        "Methods of Quantitative and Qualitative Data Analysis",
    "Научные основы воспитания обучающихся с умственной отсталостью "
    "(интеллектуальными нарушениями)":
        "Scientific Foundations of Upbringing Students with Intellectual Disabilities",
    "Научные основы образования дошкольников с умственной отсталостью "
    "(интеллектуальными нарушениями)":
        "Scientific Foundations of Preschool Education for Children "
        "with Intellectual Disabilities",
    "Научные основы обучения обучающихся с умственной отсталостью "
    "(интеллектуальными нарушениями)":
        "Scientific Foundations of Teaching Students with Intellectual Disabilities",
    "Общая физическая подготовка":
        "General Physical Training",
    "Основы государственной политики в сфере межэтнических и "
    "межконфессиональных отношений":
        "Fundamentals of State Policy on Interethnic and Interfaith Relations",
    "Педагогика":
        "Pedagogy",
    "Психология обучающихся с умственной отсталостью "
    "(интеллектуальными нарушениями)":
        "Psychology of Students with Intellectual Disabilities",
    "Технология и организация воспитательных практик (классное руководство)":
        "Technology and Organization of Educational Practices "
        "(Classroom Management)",
}


def translate_subject(full_subject: str) -> str | None:
    """Пробует перевести 'Название (Тип занятия)' на английский.
    Возвращает None, если названия нет в словаре."""
    m = re.match(r"^(.*)\s\(([^()]+)\)$", full_subject)
    if not m:
        return None
    base, lesson_type = m.group(1).strip(), m.group(2).strip()
    base_en = SUBJECT_EN.get(base)
    if not base_en:
        return None
    type_en = LESSON_TYPE_EN.get(lesson_type, lesson_type)
    return f"{base_en} ({type_en})"


# ---------------------------------------------------------------------------
# Даты: разбор "01 сентября" -> объект date, форматирование на RU/EN
# ---------------------------------------------------------------------------
RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
RU_MONTH_BY_NUM = {v: k for k, v in RU_MONTHS.items()}
EN_MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}
WEEKDAY_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
WEEKDAY_RU_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]
WEEKDAY_EN_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def parse_site_date(date_str: str, ref_dt: datetime) -> date | None:
    """Разбирает дату вида '01 сентября' (или запасной вариант 'дд.мм')
    в объект date, подбирая год так, чтобы дата была недалеко от
    ref_dt (дата запуска скрипта) — это корректно обрабатывает переход
    через новый год."""
    date_str = date_str.strip()

    m = re.match(r"^(\d{1,2})\s+([а-яёА-ЯЁ]+)$", date_str)
    if m:
        day = int(m.group(1))
        month = RU_MONTHS.get(m.group(2).lower())
    else:
        m2 = re.match(r"^(\d{1,2})[.\-](\d{1,2})", date_str)
        if not m2:
            return None
        day, month = int(m2.group(1)), int(m2.group(2))

    if not month:
        return None

    year = ref_dt.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    # если получилась дата больше чем на ~200 дней в прошлом — скорее
    # всего, это дата уже из следующего года (переход через январь)
    if (ref_dt.date() - d).days > 200:
        try:
            d = date(year + 1, month, day)
        except ValueError:
            pass
    return d


def format_date_ru(d: date) -> str:
    return f"{d.day:02d} {RU_MONTH_BY_NUM[d.month]}"


def format_date_en(d: date) -> str:
    return f"{EN_MONTH_ABBR[d.month]} {d.day}"


def shift_time_range(time_str: str, hours_delta: int) -> tuple[str, bool]:
    """Сдвигает строку времени вида '14:00 - 15:35' на hours_delta часов.
    Возвращает (новая_строка, ушло_ли_на_предыдущий_день).
    Если формат не распознан — возвращает исходную строку без сдвига."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", time_str)
    if not m:
        return time_str, False
    h1, mi1, h2, mi2 = (int(x) for x in m.groups())

    def shift(h: int, mi: int) -> tuple[int, int, bool]:
        total = h * 60 + mi + hours_delta * 60
        crossed = total < 0 or total >= 24 * 60
        total %= 24 * 60
        nh, nm = divmod(total, 60)
        return nh, nm, crossed

    nh1, nm1, c1 = shift(h1, mi1)
    nh2, nm2, c2 = shift(h2, mi2)
    return f"{nh1}:{nm1:02d} - {nh2}:{nm2:02d}", (c1 or c2)


# ---------------------------------------------------------------------------
# Скачивание и разбор расписания
# ---------------------------------------------------------------------------
def fetch_html(from_file: str | None) -> str:
    if from_file:
        return Path(from_file).read_text(encoding="utf-8")
    resp = requests.get(URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_schedule(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    ref_dt = datetime.now()

    updated_el = soup.select_one(".rasp-update")
    updated = updated_el.get_text(strip=True) if updated_el else ""

    title_el = soup.select_one(".rasp-zag-group")
    period = title_el.get_text(strip=True) if title_el else ""

    days = []
    for item in soup.select(".rasp-item"):
        day_span = item.select_one(".rasp-day")
        week_div = item.select_one(".rasp-week")
        if not day_span:
            continue
        date_str = day_span.get_text(strip=True)
        weekday = week_div.get_text(strip=True) if week_div else ""
        date_obj = parse_site_date(date_str, ref_dt)

        lessons = []
        for para in item.select(".rasp-para"):
            time_el = para.select_one(".para-time")
            time_str = time_el.get_text(strip=True) if time_el else ""

            desc_p = para.select_one(".rasp-desc p")
            if not desc_p:
                continue

            raw_html = desc_p.decode_contents()
            raw_html = re.sub(r"<!--.*?-->", "", raw_html, flags=re.S)
            lines = [
                re.sub(r"<[^>]+>", "", chunk).strip()
                for chunk in raw_html.split("<br")
            ]
            lines = [re.sub(r"^/?>", "", l).strip() for l in lines]
            lines = [l for l in lines if l]

            subject = lines[0] if lines else ""
            room = teacher = group = ""
            for l in lines[1:]:
                low = l.lower()
                if low.startswith("учебная аудитория") or low.startswith("физкультур"):
                    room = l
                elif low.startswith("преподаватель"):
                    teacher = l.split(":", 1)[-1].strip()
                elif low.startswith("группа"):
                    group = l.split(":", 1)[-1].strip()
                elif not room:
                    room = l

            lessons.append(
                {
                    "time": time_str,
                    "subject": subject,
                    "subject_en": translate_subject(subject),
                    "room": room,
                    "teacher": teacher,
                    "group": group,
                }
            )

        days.append({
            "date": date_str,
            "weekday": weekday,
            "date_iso": date_obj.isoformat() if date_obj else None,
            "lessons": lessons,
        })

    return {
        "group": GROUP_NAME,
        "period": period,
        "updated_on_site": updated,
        "generated_at": ref_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
    }


# ---------------------------------------------------------------------------
# HTML: сетка недель (Пн-Сб)
# ---------------------------------------------------------------------------
GRID_HEAD_CELL = '<div class="g-head">{label}</div>'

GRID_LESSON = """<div class="g-lesson">
  <div class="g-time">{time}{note}</div>
  <div class="g-subject">{subject}</div>
  <div class="g-info">{room}{teacher_line}</div>
</div>"""

GRID_CELL = """<div class="g-cell">
  <div class="g-date">{weekday} · {date_label}</div>
  {body}
</div>"""

WEEK_TABLE = """<div class="week-table-wrap">
  <div class="week-table">
    {cells}
  </div>
</div>"""


def build_week_tables(days_by_date, weekday_labels, weekday_short, date_fmt,
                       no_class_text, lesson_fields):
    """days_by_date: {date: [lesson,...]} (списки уже в нужном для
    отображения виде — переведённые/сдвинутые, если нужно).
    lesson_fields(lesson) -> (time, note_html, subject, room, teacher_line)
    Возвращает готовый HTML со всеми week-table блоками."""
    if not days_by_date:
        return '<p style="text-align:center;color:var(--muted)">Расписание не найдено.</p>'

    start = min(days_by_date.keys())
    end = max(days_by_date.keys())
    monday0 = start - timedelta(days=start.weekday())

    tables = []
    cur_monday = monday0
    while cur_monday <= end:
        cells = [GRID_HEAD_CELL.format(label=lbl) for lbl in weekday_labels]
        for i in range(6):
            cell_date = cur_monday + timedelta(days=i)
            lessons = days_by_date.get(cell_date, [])
            if lessons:
                body = "\n".join(
                    GRID_LESSON.format(
                        time=t, note=note, subject=subj, room=room,
                        teacher_line=teacher_line,
                    )
                    for (t, note, subj, room, teacher_line) in
                    (lesson_fields(l) for l in lessons)
                )
            else:
                body = f'<div class="g-empty">{no_class_text}</div>'
            cells.append(GRID_CELL.format(
                weekday=weekday_short[i],
                date_label=date_fmt(cell_date),
                body=body,
            ))
        tables.append(WEEK_TABLE.format(cells="\n".join(cells)))
        cur_monday += timedelta(days=7)

    return "\n".join(tables)


def build_ru_en_grids(data):
    """Возвращает (ru_html, en_html, предупреждения)."""
    warnings = []

    ru_by_date = {}
    for day in data["days"]:
        if not day["date_iso"]:
            warnings.append(f"Не удалось разобрать дату '{day['date']}' — день пропущен из сетки.")
            continue
        d = date.fromisoformat(day["date_iso"])
        if d.weekday() == 6:  # воскресенье — сетка только Пн-Сб
            if day["lessons"]:
                warnings.append(f"На {day['date']} (воскресенье) есть занятия — они не попали в сетку.")
            continue
        ru_by_date.setdefault(d, []).extend(day["lessons"])

    def ru_fields(l):
        teacher_line = f"<br>{l['teacher']}" if l["teacher"] else ""
        return (l["time"], "", l["subject"], l["room"], teacher_line)

    ru_html = build_week_tables(
        ru_by_date, WEEKDAY_RU, WEEKDAY_RU_SHORT, format_date_ru,
        "Занятий нет", ru_fields,
    )

    # EN: время сдвинуто на BRAZIL_HOURS_OFFSET часов, предмет — перевод
    # (если нет перевода — берём оригинал, чтобы не оставлять пусто).
    # День/колонка остаются как в RU-сетке — сдвигается только время
    # внутри суток (если время "перескакивает" через полночь назад,
    # рядом со временем ставится пометка).
    def en_fields(l):
        shifted, crossed = shift_time_range(l["time"], BRAZIL_HOURS_OFFSET)
        note = ' <span class="g-daynote">(prev. day)</span>' if crossed else ""
        subject = l.get("subject_en") or l["subject"]
        teacher_line = f"<br>{l['teacher']}" if l["teacher"] else ""
        return (shifted, note, subject, l["room"], teacher_line)

    en_html = build_week_tables(
        ru_by_date, WEEKDAY_EN, WEEKDAY_EN_SHORT, format_date_en,
        "No classes", en_fields,
    )

    return ru_html, en_html, warnings


# ---------------------------------------------------------------------------
# Полная страница
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Schedule for my sweet boyfriend</title>
<link rel="icon" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzMiAzMiI+CjxwYXRoIGQ9Ik0xNiAyOC41IEMxNiAyOC41IDIgMTkuOCAyIDEwLjkgQzIgNi4zIDUuNiAzIDkuOCAzIEMxMi42IDMgMTUgNC42IDE2IDcgQzE3IDQuNiAxOS40IDMgMjIuMiAzIEMyNi40IDMgMzAgNi4zIDMwIDEwLjkgQzMwIDE5LjggMTYgMjguNSAxNiAyOC41IFoiIGZpbGw9IiNlMDUwN2UiLz4KPC9zdmc+">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Dancing+Script:wght@600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --primary: #c65f92;
    --primary-dark: #92406f;
    --accent: #ef8fab;
    --bg-start: #fff2f6;
    --bg-end: #f2e8fa;
    --card: #ffffff;
    --text: #4a3f4d;
    --muted: #8f7f95;
    --en-accent: #a4487f;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    background: linear-gradient(160deg, var(--bg-start), var(--bg-end));
    min-height: 100vh;
    margin: 0;
    padding: 24px 16px 60px;
    color: var(--text);
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}

  .header-row {{ position: relative; text-align: center; margin-bottom: 6px; }}
  h1 {{
    font-family: "Dancing Script", cursive;
    text-align: center;
    font-size: 42px;
    margin: 8px 0 10px;
    background: linear-gradient(100deg, var(--primary), var(--primary-dark));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    font-weight: 700;
  }}
  .lang-toggle {{
    position: absolute;
    right: 0;
    top: 6px;
    border: none;
    background: var(--card);
    color: var(--primary-dark);
    font-weight: 600;
    font-size: 13px;
    padding: 9px 16px;
    border-radius: 999px;
    box-shadow: 0 4px 12px rgba(109, 36, 86, 0.18);
    cursor: pointer;
  }}
  .lang-toggle:active {{ transform: scale(0.96); }}

  .meta {{ color: var(--muted); font-size: 13px; margin-bottom: 24px; text-align: center; }}

  /* --- сетка недель --- */
  .week-table-wrap {{
    overflow-x: auto;
    margin-bottom: 20px;
    border-radius: 14px;
    box-shadow: 0 4px 14px rgba(109, 36, 86, 0.09);
  }}
  .week-table {{
    display: grid;
    grid-template-columns: repeat(6, minmax(140px, 1fr));
    background: var(--card);
    min-width: 840px;
    border-radius: 14px;
    overflow: hidden;
  }}
  .g-head {{
    background: linear-gradient(165deg, var(--primary), var(--primary-dark));
    color: #fff;
    text-align: center;
    font-weight: 600;
    font-size: 13px;
    padding: 10px 6px;
  }}
  .g-cell {{
    border-top: 1px solid #f1e6ee;
    border-left: 1px solid #f1e6ee;
    padding: 10px 10px 14px;
    min-height: 60px;
  }}
  .week-table > :nth-child(6n+1) {{ border-left: none; }}
  .g-date {{ font-size: 12px; color: var(--muted); font-weight: 700; margin-bottom: 8px; }}
  .g-lesson {{ margin-bottom: 10px; }}
  .g-lesson:last-child {{ margin-bottom: 0; }}
  .g-time {{ font-size: 12px; color: var(--primary); font-weight: 600; }}
  .g-daynote {{ font-size: 10px; color: var(--muted); font-style: italic; }}
  .g-subject {{ font-size: 13px; font-weight: 600; margin: 2px 0 3px; }}
  .g-info {{ font-size: 12px; color: #6b5f6e; line-height: 1.45; }}
  .g-empty {{ font-size: 12px; color: var(--muted); font-style: italic; }}

  /* --- рубрика Us --- */
  .us-section {{ text-align: center; margin: 36px 0 8px; }}
  .us-photo img {{
    max-width: 100%;
    width: 360px;
    border-radius: 18px;
    box-shadow: 0 8px 24px rgba(146, 64, 111, 0.25);
    border: 4px solid #ffffff;
    cursor: pointer;
  }}
  .us-caption {{
    font-family: "Dancing Script", cursive;
    font-size: 30px;
    color: var(--primary-dark);
    margin-top: 10px;
  }}
  .us-message {{
    font-family: "Dancing Script", cursive;
    font-size: 21px;
    color: var(--primary-dark);
    margin: 16px auto 0;
    max-width: 480px;
    line-height: 1.4;
  }}

  /* --- всплывающее уведомление --- */
  .notif-toast {{
    position: fixed;
    top: 18px;
    left: 50%;
    transform: translate(-50%, -140%);
    background: linear-gradient(100deg, var(--primary), var(--primary-dark));
    color: #fff;
    padding: 13px 24px;
    border-radius: 999px;
    font-size: 15px;
    font-weight: 600;
    box-shadow: 0 8px 22px rgba(109, 36, 86, 0.32);
    z-index: 50;
    opacity: 0;
    transition: transform 0.4s ease, opacity 0.4s ease;
    cursor: pointer;
    max-width: 90%;
    text-align: center;
  }}
  .notif-toast.show {{ transform: translate(-50%, 0); opacity: 1; }}

  @keyframes float-heart {{
    0%   {{ transform: translateY(0) rotate(0deg); opacity: 0.9; }}
    100% {{ transform: translateY(-120vh) rotate(20deg); opacity: 0; }}
  }}
  .floating-hearts {{
    position: fixed;
    inset: 0;
    pointer-events: none;
    overflow: hidden;
    z-index: 0;
  }}
  .floating-hearts span {{
    position: absolute;
    bottom: -10%;
    color: var(--accent);
    animation: float-heart linear infinite;
    opacity: 0.75;
  }}
  .wrap {{ position: relative; z-index: 1; }}

  @media (max-width: 480px) {{
    h1 {{ font-size: 32px; }}
    .lang-toggle {{ position: static; display: inline-block; margin-top: 10px; }}
  }}
</style>
</head>
<body>
<div id="notifToast" class="notif-toast"></div>
<div class="floating-hearts">
  <span style="left:6%;  font-size:52px; animation-duration:14s; animation-delay:0s;">♥</span>
  <span style="left:14%; font-size:38px; animation-duration:17s; animation-delay:7s;">♥</span>
  <span style="left:18%; font-size:68px; animation-duration:18s; animation-delay:2s;">♥</span>
  <span style="left:25%; font-size:44px; animation-duration:15s; animation-delay:9s;">♥</span>
  <span style="left:32%; font-size:46px; animation-duration:12s; animation-delay:4s;">♥</span>
  <span style="left:40%; font-size:36px; animation-duration:19s; animation-delay:6s;">♥</span>
  <span style="left:48%; font-size:62px; animation-duration:16s; animation-delay:1s;">♥</span>
  <span style="left:56%; font-size:42px; animation-duration:13s; animation-delay:8s;">♥</span>
  <span style="left:63%; font-size:48px; animation-duration:13s; animation-delay:5s;">♥</span>
  <span style="left:70%; font-size:40px; animation-duration:17s; animation-delay:3s;">♥</span>
  <span style="left:77%; font-size:72px; animation-duration:20s; animation-delay:3s;">♥</span>
  <span style="left:84%; font-size:38px; animation-duration:14s; animation-delay:10s;">♥</span>
  <span style="left:90%; font-size:54px; animation-duration:15s; animation-delay:6s;">♥</span>
  <span style="left:96%; font-size:44px; animation-duration:16s; animation-delay:2s;">♥</span>
</div>
<div class="wrap">
  <div class="header-row">
    <h1>Schedule for my sweet boyfriend ❤️</h1>
    <button id="langToggle" class="lang-toggle" type="button">EN 🇧🇷</button>
  </div>
  <div class="meta">{period}<br>Обновлено на сайте: {updated_on_site}<br>Синхронизировано: {generated_at}</div>

  <div id="scheduleRu">
    {ru_grid}
  </div>
  <div id="scheduleEn" style="display:none">
    {en_grid}
  </div>

  <div class="us-section">
    {photo_html}
    <div class="us-caption">us</div>
    <div class="us-message">Spend my whole life in love with Paulo ❤️</div>
  </div>
</div>
{photo_script}
<script>
document.getElementById('langToggle').addEventListener('click', function() {{
  var ru = document.getElementById('scheduleRu');
  var en = document.getElementById('scheduleEn');
  if (en.style.display === 'none') {{
    ru.style.display = 'none';
    en.style.display = '';
    this.textContent = 'RU 🇷🇺';
  }} else {{
    en.style.display = 'none';
    ru.style.display = '';
    this.textContent = 'EN 🇧🇷';
  }}
}});
</script>
<script>
(function() {{
  var phrases = {phrases_json};
  var phrase = phrases[Math.floor(Math.random() * phrases.length)];
  var toast = document.getElementById('notifToast');
  toast.textContent = phrase;
  requestAnimationFrame(function() {{
    setTimeout(function() {{ toast.classList.add('show'); }}, 300);
  }});
  setTimeout(function() {{ toast.classList.remove('show'); }}, 5000);
  toast.addEventListener('click', function() {{ toast.classList.remove('show'); }});
}})();
</script>
</body>
</html>
"""


def render_html(data):
    ru_grid, en_grid, warnings = build_ru_en_grids(data)
    for w in warnings:
        print(f"Предупреждение: {w}", file=sys.stderr)

    photos = find_photos()
    if photos:
        photo_html = '<div class="us-photo"><img id="usPhoto" alt="us"></div>'
        photos_json = json.dumps(
            [{"src": src, "weight": weight} for src, weight in photos],
            ensure_ascii=False,
        )
        photo_script = f"""<script>
(function() {{
  var photos = {photos_json};
  var total = photos.reduce(function(sum, p) {{ return sum + p.weight; }}, 0);
  var r = Math.random() * total;
  var chosen = photos[0];
  for (var i = 0; i < photos.length; i++) {{
    if (r < photos[i].weight) {{ chosen = photos[i]; break; }}
    r -= photos[i].weight;
  }}
  document.getElementById('usPhoto').src = chosen.src;
}})();
</script>"""
    else:
        photo_html = ""
        photo_script = ""

    return HTML_TEMPLATE.format(
        period=data["period"],
        updated_on_site=data["updated_on_site"],
        generated_at=data["generated_at"],
        ru_grid=ru_grid,
        en_grid=en_grid,
        photo_html=photo_html,
        photo_script=photo_script,
        phrases_json=json.dumps(NOTIF_PHRASES, ensure_ascii=False),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", help="разобрать локальный HTML-файл вместо запроса к сайту")
    args = ap.parse_args()

    html = fetch_html(args.from_file)
    data = parse_schedule(html)

    if not data["days"]:
        print("Не удалось найти расписание в HTML — сайт мог поменять вёрстку.", file=sys.stderr)
        sys.exit(1)

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "schedule.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    page = render_html(data)
    (OUT_DIR / "index.html").write_text(page, encoding="utf-8")

    total_lessons = sum(len(d["lessons"]) for d in data["days"])
    print(f"Готово: {len(data['days'])} дней, {total_lessons} пар. "
          f"Файлы: index.html, data/schedule.json")


if __name__ == "__main__":
    main()
