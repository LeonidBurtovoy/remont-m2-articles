#!/usr/bin/env python3
"""Ежедневная публикация статьи в сообщество ВКонтакте.

Ключи берутся из переменных окружения VK_TOKEN (ключ сообщества) и VK_GROUP_ID (число).
Статья дня выбирается по той же формуле, что и в Telegram: номер = (дни от 2026-09-10) mod 145 + 1.
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
TOTAL = 145
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FOOTER = (
    "— Ремонт М2 · Калининград\n"
    "Бесплатный замер: +7 (4012) 52-32-78\n"
    "Сайт: remont-m2.ru\n\n"
    "#ремонт #ремонтквартир #Калининград #ремонтподключ"
)
MAX_LEN = 15000


def article_number(today=None):
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    days = max((today - START).days, 0)
    return days % TOTAL + 1


def read_article(number):
    text = open(os.path.join(ROOT, "articles.md"), encoding="utf-8").read()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(\d+)\.\s+(.+?)\s*$", line)
        if m and int(m.group(1)) == number:
            start, title = i, m.group(2)
            break
    if start is None:
        raise SystemExit(f"Статья №{number} не найдена в articles.md")
    body = []
    for line in lines[start + 1:]:
        if line.startswith("## ") or re.match(r"^-{3,}\s*$", line):
            break
        body.append(line)
    return title, body


def clean_body(lines):
    out = []
    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line) and "|" in line:
            continue
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = " — ".join(c for c in cells if c)
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "— ", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", line)
        line = line.replace("`", "")
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def build_message(title, body_text):
    head = f"{title}\n\n"
    tail = f"\n\n{FOOTER}"
    room = MAX_LEN - len(head) - len(tail)
    if len(body_text) > room:
        paras = body_text.split("\n\n")
        while paras and len("\n\n".join(paras)) > room:
            paras.pop()
        body_text = "\n\n".join(paras)
    return head + body_text + tail


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


def upload_file(url, field, path):
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    name = os.path.basename(path)
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def upload_photos(token, group_id, paths):
    attachments = []
    for p in paths:
        srv = vk("photos.getWallUploadServer", token, group_id=group_id)
        up = upload_file(srv["upload_url"], "photo", p)
        saved = vk("photos.saveWallPhoto", token, group_id=group_id, photo=up["photo"], server=up["server"], hash=up["hash"])
        attachments.append(f"photo{saved[0]['owner_id']}_{saved[0]['id']}")
    return attachments


def already_posted(token, group_id, title):
    try:
        posts = vk("wall.get", token, owner_id=-group_id, count=15)["items"]
    except Exception as e:  # noqa: BLE001
        print(f"Проверка дублей пропущена: {e}")
        return False
    return any(p.get("text", "").startswith(title) for p in posts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="показать пост и ничего не публиковать")
    ap.add_argument("--check", action="store_true", help="проверить ключ и права, ничего не публиковать")
    ap.add_argument("--number", type=int, help="номер статьи вместо статьи дня")
    ap.add_argument("--no-photos", action="store_true", help="публиковать только текст")
    ap.add_argument("--force", action="store_true", help="публиковать, даже если статья уже есть на стене")
    args = ap.parse_args()

    number = args.number or article_number()
    title, body = read_article(number)
    message = build_message(title, clean_body(body))
    nnn = f"{number:03d}"
    photos = [os.path.join(ROOT, "images", f"{nnn}-{kind}.png") for kind in ("cover", "lifehack", "summary")]

    token = os.environ.get("VK_TOKEN", "")
    gid_raw = os.environ.get("VK_GROUP_ID", "")

    if args.dry_run:
        print(f"Статья №{number}: {title}")
        print(f"Длина сообщения: {len(message)} символов; картинки: {[os.path.basename(p) for p in photos]}")
        print("-" * 60)
        print(message)
        return

    if not token or not gid_raw:
        raise SystemExit("Не заданы переменные окружения VK_TOKEN и VK_GROUP_ID")
    group_id = int(gid_raw.lstrip("-"))

    if args.check:
        g = vk("groups.getById", token, group_id=group_id)
        g = g["groups"][0] if isinstance(g, dict) and "groups" in g else g[0]
        print(f"Сообщество: {g.get('name')} (id {group_id})")
        try:
            vk("wall.get", token, owner_id=-group_id, count=1)
            print("Чтение стены: OK")
        except Exception as e:  # noqa: BLE001
            print(f"Чтение стены: {e}")
        try:
            vk("photos.getWallUploadServer", token, group_id=group_id)
            print("Загрузка фото: OK — картинки будут прикрепляться к постам")
        except Exception as e:  # noqa: BLE001
            print(f"Загрузка фото: недоступна ({e}) — посты будут выходить без картинок")
        return

    if not args.force and already_posted(token, group_id, title):
        print(f"RESULT: пропуск — статья №{number} «{title}» уже опубликована на стене")
        return

    attachments, note = [], ""
    if not args.no_photos:
        missing = [p for p in photos if not os.path.exists(p)]
        if missing:
            note = f"нет файлов картинок: {missing}"
        else:
            try:
                attachments = upload_photos(token, group_id, photos)
            except Exception as e:  # noqa: BLE001
                note = str(e)

    params = {"owner_id": -group_id, "from_group": 1, "message": message}
    if attachments:
        params["attachments"] = ",".join(attachments)
    res = vk("wall.post", token, **params)
    post_id = res["post_id"]
    where = f"с {len(attachments)} картинками" if attachments else "без картинок" + (f" (причина: {note})" if note else "")
    print(f"RESULT: опубликована статья №{number} «{title}» {where}")
    print(f"Ссылка: https://vk.com/wall-{group_id}_{post_id}")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"ОШИБКА: {exc}")
        sys.exit(1)
