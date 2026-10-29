import json, re, sys, glob, os, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

JST = timezone(timedelta(hours=9))
# 大手メディア: (表示名, 運営会社, RSSのURL) 同じ運営会社は媒体数で1つと数える
FEEDS = [
    ("NHK 主要", "NHK", "https://www.nhk.or.jp/rss/news/cat0.xml"),
    ("NHK 社会", "NHK", "https://www.nhk.or.jp/rss/news/cat1.xml"),
    ("朝日新聞", "朝日新聞", "https://www.asahi.com/rss/asahi/newsheadlines.rdf"),
    ("毎日新聞", "毎日新聞", "https://mainichi.jp/rss/etc/mainichi-flash.rss"),
    ("Yahoo!ニュース 国内", "Yahoo!ニュース", "https://news.yahoo.co.jp/rss/topics/domestic.xml"),
    ("Yahoo!ニュース IT", "Yahoo!ニュース", "https://news.yahoo.co.jp/rss/topics/it.xml"),
    ("ITmedia", "ITmedia", "https://rss.itmedia.co.jp/rss/2.0/itmedia_all.xml"),
    ("INTERNET Watch", "Impress", "https://internet.watch.impress.co.jp/data/rss/1.0/iw/feed.rdf"),
]
# YouTube: (チャンネル名, @ハンドル または UCで始まるチャンネルID)
YOUTUBE = [
    ("ANNnewsCH", "UCGCZAYq5Xxojl_tSXcVJhiQ"),
    ("日テレNEWS", "UCuTAXTexrhetbOe3zgskJBQ"),
    ("TBS NEWS DIG", "UC6AG81pAkf6Lbi_1VC5NmPA"),
    ("テレ東BIZ", "UCkKVQ_GNjd8FbAuT6xDcWgg"),
    ("FNNプライムオンライン", "UCoQBJMzcwmXrRSHBFAlTsIw"),
]
HOURS = 36
SIMILAR = 0.35
LEAK = re.compile("漏洩|漏えい|流出|不正アクセス|不正に閲覧|紛失|誤送信|誤送付|盗まれ|盗難")
CATS = [  # (区分, 言葉) 複数に当てはまれば、すべての区分に表示する
    ("pii", re.compile("個人情報|氏名|住所|電話番号|メールアドレス|顧客情報|会員情報|生年月日|個人データ|名簿")),
    ("card", re.compile("クレジットカード|カード情報|カード番号|セキュリティコード|カード決済")),
    ("tokutei", re.compile("マイナンバー|個人番号|特定個人情報")),
]


def local(tag):
    return tag.split("}")[-1]


def find(e, *names):
    for c in e.iter():
        if local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def link_of(e):
    for c in e:
        if local(c.tag) == "link":
            v = (c.text or "").strip() or c.get("href", "")
            if v:
                return v
    return ""


def thumb_of(e):
    for c in e.iter():
        if local(c.tag) == "thumbnail" and c.get("url"):
            return c.get("url")
    return ""


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=JST)


def fetch(url, limit=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; leak-news-app/1.0)",
        "Accept-Language": "ja", "Cookie": "CONSENT=YES+1"})
    return urllib.request.urlopen(req, timeout=30).read(limit)


def channel_id(h):
    if h.startswith("UC"):
        return h
    page = fetch("https://www.youtube.com/" + h, 600000).decode("utf-8", "ignore")
    m = re.search(r'"channelId":"(UC[\w-]{22})"|channel_id=(UC[\w-]{22})', page)
    if not m:
        raise ValueError("チャンネルIDが見つかりません")
    return m.group(1) or m.group(2)


def og_image(url):
    try:
        h = fetch(url, 300000).decode("utf-8", "ignore")
        m = (re.search(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)', h, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image["\']', h, re.I))
        u = m.group(1).replace("&amp;", "&") if m else ""
        return u if u.startswith("http") else ""
    except Exception:
        return ""


def bigrams(t):
    t = re.sub(r"\s|[「」『』、。!?:()（）]", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similar(a, b):
    x, y = bigrams(a), bigrams(b)
    return bool(x and y) and len(x & y) / len(x | y) >= SIMILAR


def collect(url, name, company, kind, now, items, seen):
    root = ET.fromstring(fetch(url))
    n = 0
    for e in root.iter():
        if local(e.tag) not in ("item", "entry"):
            continue
        title, link = find(e, "title"), link_of(e)
        desc = re.sub(r"<[^>]+>", "", find(e, "description", "summary")).strip()[:120]
        when = parse_date(find(e, "pubDate", "date", "published"))
        if not title or not link or link in seen:
            continue
        if when and now - when > timedelta(hours=HOURS):
            continue
        text = title + " " + (find(e, "description", "summary") if kind == "video" else desc)
        cats = [k for k, r in CATS if r.search(text)] if LEAK.search(text) else []
        if not cats:
            continue
        seen.add(link)
        n += 1
        items.append({"title": title, "summary": desc, "link": link, "source": name, "company": company,
                      "kind": kind, "cats": cats, "image": thumb_of(e)})
    return n


def main():
    now = datetime.now(JST)
    items, seen, status = [], set(), []
    for name, company, url in FEEDS:
        try:
            status.append({"name": name, "ok": True, "items": collect(url, name, company, "article", now, items, seen)})
        except Exception as ex:
            status.append({"name": name, "ok": False, "error": str(ex)[:100]})
    for name, handle in YOUTUBE:
        try:
            url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id(handle)
            status.append({"name": name, "ok": True, "items": collect(url, name, name, "video", now, items, seen)})
        except Exception as ex:
            status.append({"name": name, "ok": False, "error": str(ex)[:100]})
    for s in status:
        print(s)

    groups = []
    for it in items:
        for g in groups:
            if similar(g["title"], it["title"]):
                g["members"].append(it)
                break
        else:
            groups.append({"title": it["title"], "members": [it]})
    out = []
    for g in groups:
        ms, rep = g["members"], g["members"][0]
        image = next((m["image"] for m in ms if m["image"]), "")
        for m in ms[:3]:
            if not image and m["kind"] == "article":
                image = og_image(m["link"])
        cats = [k for k, _ in CATS if any(k in m["cats"] for m in ms)]
        out.append({"title": rep["title"], "summary": rep["summary"], "link": rep["link"],
                    "source": rep["source"], "kind": rep["kind"], "categories": cats, "image": image,
                    "count": len({m["company"] for m in ms}),
                    "others": [{"source": m["source"], "link": m["link"]} for m in ms[1:]]})
    out.sort(key=lambda g: -g["count"])

    os.makedirs("data", exist_ok=True)
    today = now.strftime("%Y-%m-%d")
    with open(f"data/{today}.json", "w", encoding="utf-8") as f:
        json.dump({"date": today, "groups": out}, f, ensure_ascii=False, indent=1)
    dates = sorted((os.path.basename(p)[:-5] for p in glob.glob("data/20*.json")), reverse=True)
    with open("data/index.json", "w", encoding="utf-8") as f:
        json.dump(dates, f)
    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    if not any(s["ok"] for s in status):
        sys.exit(1)


if __name__ == "__main__":
    main()
