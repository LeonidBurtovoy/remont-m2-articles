#!/usr/bin/env python3
"""Ежедневная публикация вертикального ролика в клипы сообщества ВКонтакте.

Ключи берутся из переменных окружения VK_TOKEN и VK_GROUP_ID — те же, что у post_vk.py.
Ролик дня выбирается той же формулой, что статья дня, поэтому пост и клип совпадают по теме.

Сначала пробуем shortVideo.create (это и есть клипы). Если метод недоступен токену,
откатываемся на video.save — ролик ляжет в видео сообщества, вертикальный формат
ВКонтакте подхватывает и там. Что именно сработало, видно в выводе.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://api.vk.com/method/"
API_VERSION = "5.199"
START = datetime.date(2026, 9, 10)
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SITE = "https://remont-m2.ru"
FOOTER = (
    "Ремонт М2 · Калининград\n"
    "Бесплатный замер: +7 (4012) 52-32-78\n"
    f"{SITE}\n\n"
    "#ремонт #ремонтквартир #Калининград #ремонтподключ"
)
MAX_DESC = 1000


def total_articles():
    text = open(os.path.join(ROOT, "articles.md"), encoding="utf-8").read()
    return len(re.findall(r"(?m)^##\s+\d+\.", text))


def article_number(today=None):
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    days = max((today - START).days, 0)
    return days % total_articles() + 1


def article_title(number):
    text = open(os.path.join(ROOT, "articles.md"), encoding="utf-8").read()
    m = re.search(rf"(?m)^##\s+{number}\.\s+(.+?)\s*$", text)
    if not m:
        raise SystemExit(f"Статья №{number} не найдена в articles.md")
    return m.group(1)


def article_link(number):
    """Адрес статьи на сайте — ради него клип и публикуется."""
    path = os.path.join(ROOT, "links.json")
    if not os.path.exists(path):
        return SITE
    return json.load(open(path, encoding="utf-8")).get(str(number), SITE)


def clip_path(number):
    return os.path.join(ROOT, "videos", f"{number:03d}.mp4")


def build_description(title, link):
    body = f"{title}\n\nРазбор целиком: {link}\n\n{FOOTER}"
    return body[:MAX_DESC]


def vk(method, token, **params):
    params.update({"v": API_VERSION, "access_token": token})
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API + method, data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode())
    if "error" in res:
        e = res["error"]
        raise RuntimeError(f"VK error {e.get('error_code')}: {e.get('error_msg')} (метод {method})")
    return res["response"]


def upload(url, field, path):
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
        f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: video/mp4\r\n\r\n"
    ).encode()
    body = head + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read().decode("utf-8", "ignore")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:300]}


def post_as_clip(token, group_id, path, description):
    size_kb = max(1, os.path.getsize(path) // 1024)
    created = vk("shortVideo.create", token, group_id=group_id,
                 file_size=size_kb, description=description)
    res = upload(created["upload_url"], "file", path)
    return "клип", created.get("owner_id", -group_id), created.get("video_id"), res


def post_as_video(token, group_id, path, title, description):
    saved = vk("video.save", token, group_id=group_id, name=title[:128],
               description=description, wallpost=0)
    res = upload(saved["upload_url"], "video_file", path)
    return "видео сообщества", saved.get("owner_id", -group_id), saved.get("video_id"), res


def already_posted(token, group_id, title):
    try:
        items = vk("video.get", token, owner_id=-group_id, count=30)["items"]
    except Exception as e:  # noqa: BLE001
        print(f"Проверка дублей пропущена: {e}")
        return False
    head = title[:60].lower()
    return any(head in (v.get("title", "") + " " + v.get("description", "")).lower() for v in items)


def publish(token, group_id, number, force=False, video_only=False):
    """Загрузка одного ролика. Вызывается и из post_vk.py, чтобы пост и клип выходили вместе."""
    title = article_title(number)
    path = clip_path(number)
    if not os.path.exists(path):
        raise SystemExit(f"Нет файла ролика: {path}")
    if not force and already_posted(token, group_id, title):
        print(f"RESULT: пропуск — ролик №{number} «{title}» уже загружен")
        return

    description = build_description(title, article_link(number))
    attempts = [
        ("клип", lambda: post_as_clip(token, group_id, path, description)),
        ("видео", lambda: post_as_video(token, group_id, path, title, description)),
    ]
    if video_only:
        attempts = attempts[1:]
    last_error = ""
    for label, send in attempts:
        try:
            kind, owner, vid, res = send()
            print(f"RESULT: ролик №{number} «{title}» загружен как {kind}")
            if vid:
                print(f"Ссылка: https://vk.com/video{owner}_{vid}")
            if isinstance(res, dict) and res.get("error"):
                print(f"Ответ загрузчика: {res}")
            return
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            print(f"Через {label} не вышло: {e}")
    raise SystemExit(f"Не удалось загрузить ролик: {last_error}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="показать, что будет опубликовано")
    ap.add_argument("--check", action="store_true", help="проверить ключ и права, ничего не публиковать")
    ap.add_argument("--number", type=int, help="номер ролика вместо ролика дня")
    ap.add_argument("--force", action="store_true", help="публиковать, даже если ролик уже загружен")
    ap.add_argument("--video-only", action="store_true", help="не пробовать клипы, сразу video.save")
    args = ap.parse_args()

    number = args.number or article_number()
    title = article_title(number)
    path = clip_path(number)
    description = build_description(title, article_link(number))

    if args.dry_run:
        print(f"Ролик №{number}: {title}")
        print(f"Файл: {path} ({'есть' if os.path.exists(path) else 'НЕТ ФАЙЛА'})")
        print(f"Описание ({len(description)} символов):\n{'-' * 60}\n{description}")
        return

    token = os.environ.get("VK_TOKEN", "")
    gid_raw = os.environ.get("VK_GROUP_ID", "")
    if not token or not gid_raw:
        raise SystemExit("Не заданы переменные окружения VK_TOKEN и VK_GROUP_ID")
    group_id = int(gid_raw.lstrip("-"))

    if args.check:
        g = vk("groups.getById", token, group_id=group_id)
        g = g["groups"][0] if isinstance(g, dict) and "groups" in g else g[0]
        print(f"Сообщество: {g.get('name')} (id {group_id})")
        for method, params, label in [
            ("video.get", {"owner_id": -group_id, "count": 1}, "Чтение видео"),
            ("shortVideo.create", {"group_id": group_id, "file_size": 1,
                                   "description": "проверка прав"}, "Клипы (shortVideo.create)"),
        ]:
            try:
                vk(method, token, **params)
                print(f"{label}: OK")
            except Exception as e:  # noqa: BLE001
                print(f"{label}: недоступно ({e})")
        print("Если клипы недоступны — ролики пойдут через video.save, это рабочий запасной путь.")
        return

    publish(token, group_id, number, force=args.force, video_only=args.video_only)


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"ОШИБКА: {exc}")
        sys.exit(1)
