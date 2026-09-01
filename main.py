#!/usr/bin/env python3
"""Poll a fixed list of Korean finance-news section pages and push new
articles to a Telegram chat. State (already-sent article ids) is kept in
state.json in this repo so re-runs don't resend the same article; that
file is committed and pushed back at the end of each run.
"""
import html as html_lib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "state.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAX_KEEP_PER_SOURCE = 300
MAX_ARTICLE_AGE = timedelta(days=3)
GLOBAL_SENT_KEY = "_global_sent_urls"
MAX_GLOBAL_SENT = 3000

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SOURCES = [
    {"label": "더벨_Finance", "type": "thebell", "url": "https://www.thebell.co.kr/front/NewsList.asp?Code=0200"},
    {"label": "더벨_채권", "type": "thebell", "url": "https://www.thebell.co.kr/front/NewsList.asp?Code=0101"},
    {"label": "더벨_산업", "type": "thebell", "url": "https://www.thebell.co.kr/front/NewsList.asp?Code=0400"},
    {"label": "IB토마토_산업", "type": "ibtomato", "url": "https://www.ibtomato.com/CateSub.aspx?cate=1100&subCate=1102&type=1"},
    {"label": "IB토마토_금융", "type": "ibtomato", "url": "https://www.ibtomato.com/CateSub.aspx?cate=1900&subCate=1902&type=1"},
    {"label": "IB토마토_Deal", "type": "ibtomato", "url": "https://www.ibtomato.com/CateSub.aspx?cate=1100&subCate=1101&type=1"},
    {"label": "연합인포_IB/기업", "type": "einfomax", "url": "https://news.einfomax.co.kr/news/articleList.html?sc_section_code=S1N7&view_type=sm", "category": "IB/기업"},
    {"label": "연합인포_증권", "type": "einfomax", "url": "https://news.einfomax.co.kr/news/articleList.html?sc_section_code=S1N2&view_type=sm", "category": "증권"},
    {"label": "이데일리_크레딧", "type": "edaily", "url": "https://marketin.edaily.co.kr/News/List?c=DM21"},
    {"label": "이데일리_투자", "type": "edaily", "url": "https://marketin.edaily.co.kr/News/List?c=DM32"},
    {"label": "이데일리_트렌드", "type": "edaily", "url": "https://marketin.edaily.co.kr/News/List?c=DM41"},
    {"label": "이데일리_포커스", "type": "edaily", "url": "https://marketin.edaily.co.kr/News/List?c=SP02"},
    {"label": "한국경제_산업", "type": "hankyung", "url": "https://www.hankyung.com/industry"},
    {"label": "딜사이트_산업", "type": "dealsite", "category_code": "068000"},
    {"label": "딜사이트_증권", "type": "dealsite", "category_code": "089000"},
    {"label": "딜사이트_인수합병", "type": "dealsite", "category_code": "080000"},
    {"label": "조선비즈_증권", "type": "chosun", "url": "https://biz.chosun.com/stock/", "section": "stock"},
    {"label": "조선비즈_금융", "type": "chosun", "url": "https://biz.chosun.com/finance/", "section": "stock/finance"},
    {"label": "조선비즈_산업", "type": "chosun", "url": "https://biz.chosun.com/industry/", "section": "industry"},
]


def fetch(url, referer=None, extra_headers=None):
    # Some sites (dealsite.co.kr) sit behind a WAF that fingerprints the TLS
    # handshake and blocks urllib/requests while allowing curl through, so
    # shell out to curl for all page fetches instead.
    cmd = ["curl", "-s", "--max-time", "20", "-A", UA]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    for h in extra_headers or []:
        cmd += ["-H", h]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"curl exit {result.returncode}: {result.stderr.decode('utf-8', 'replace')}")
    return result.stdout.decode("utf-8", errors="replace")


def clean(text):
    return html_lib.unescape(re.sub(r"\s+", " ", text)).strip()


def dedup_by_id(items):
    seen = set()
    out = []
    for aid, title, url in items:
        if aid in seen:
            continue
        seen.add(aid)
        out.append((aid, title, url))
    return out


def parse_thebell(raw):
    items = []
    for m in re.finditer(r'href="(/front/newsview\.asp\?[^"]*key=(\d+)[^"]*)"[^>]*>([^<]*)', raw):
        title = clean(m.group(3))
        if not title:
            continue
        href = html_lib.unescape(m.group(1))
        items.append((m.group(2), title, "https://www.thebell.co.kr" + href))
    return dedup_by_id(items)


def parse_ibtomato(raw):
    items = []
    pattern = re.compile(
        r'<a[^>]*?title="([^"]*)"[^>]*?href="(/View\.aspx\?no=(\d+)[^"]*)"'
        r'|<a[^>]*?href="(/View\.aspx\?no=(\d+)[^"]*)"[^>]*?title="([^"]*)"'
    )
    for m in pattern.finditer(raw):
        if m.group(1) is not None:
            title, href, aid = m.group(1), m.group(2), m.group(3)
        else:
            title, href, aid = m.group(6), m.group(4), m.group(5)
        title = clean(title)
        if not title:
            continue
        href = html_lib.unescape(href)
        items.append((aid, title, "https://www.ibtomato.com" + href))
    return dedup_by_id(items)


def parse_einfomax(raw, category):
    # einfomax's articleList.html ignores sc_section_code server-side and
    # always returns the site-wide latest feed; each <li> instead carries
    # its real section as plain text in <em class="info category">, so we
    # have to filter on that ourselves.
    items = []
    pattern = re.compile(
        r'<h4 class="titles"><a href="(/news/articleView\.html\?idxno=(\d+))"[^>]*>([^<]*)</a></h4>'
        r'.*?<em class="info category">([^<]*)</em>',
        re.S,
    )
    for m in pattern.finditer(raw):
        if clean(m.group(4)) != category:
            continue
        title = clean(m.group(3))
        if not title:
            continue
        href = html_lib.unescape(m.group(1))
        items.append((m.group(2), title, "https://news.einfomax.co.kr" + href))
    return dedup_by_id(items)


def parse_edaily(raw):
    items = []
    for m in re.finditer(r'<a href="(/News/Read\?newsId=(\d+)[^"]*)"><span class=""></span>([^<]*)</a>', raw):
        title = clean(m.group(3))
        if not title:
            continue
        href = html_lib.unescape(m.group(1))
        items.append((m.group(2), title, "https://marketin.edaily.co.kr" + href))
    return dedup_by_id(items)


def parse_hankyung(raw):
    items = []
    for m in re.finditer(r'href="(https://www\.hankyung\.com/article/([0-9A-Za-z]+))"[^>]*>([^<]{2,150})', raw):
        title = clean(m.group(3))
        if not title:
            continue
        items.append((m.group(2), title, m.group(1)))
    return dedup_by_id(items)


def parse_dealsite(category_code):
    url = (
        f"https://dealsite.co.kr/api/articles/categoryNews"
        f"?categoryCode={category_code}&page=0&size=20&pageBlockSize=10"
    )
    raw = fetch(
        url,
        referer=f"https://dealsite.co.kr/categories/{category_code}",
        extra_headers=["X-Requested-With: XMLHttpRequest"],
    )
    data = json.loads(raw)
    articles_html = data.get("articlesHtml", "")
    items = []
    for m in re.finditer(
        r'class="ss-news-top-title"\s+href="(/articles/(\d+)/\d+)">\s*<span[^>]*>([^<]*)</span>',
        articles_html,
    ):
        title = clean(m.group(3))
        if not title:
            continue
        href = html_lib.unescape(m.group(1))
        items.append((m.group(2), title, "https://dealsite.co.kr" + href))
    return dedup_by_id(items)


def json_unescape(s):
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


def parse_chosun(raw, section):
    # Arc XP (chosun's CMS) embeds the article feed as JSON inside a
    # <script> tag rather than plain <a> tags, so pull canonical_url /
    # display_date / headlines.basic straight out of that JSON blob. The
    # page also embeds sidebar widgets (e.g. "많이 본 뉴스") pulling from
    # unrelated sections and occasionally resurfaces old evergreen pieces
    # (e.g. a months-old interview) under a "new" id we've never seen, so
    # besides the section filter, drop anything whose own publish date is
    # stale rather than trusting id-novelty alone.
    items = []
    prefix = f"/{section}/"
    now = datetime.now(timezone.utc)
    pattern = re.compile(
        r'"canonical_url":"((?:[^"\\]|\\.)*)".*?'
        r'"display_date":"([^"]*)".*?'
        r'"headlines":\{"basic":"((?:[^"\\]|\\.)*)"',
        re.S,
    )
    for m in pattern.finditer(raw):
        path = json_unescape(m.group(1))
        if not path or not path.startswith(prefix):
            continue
        try:
            display_date = datetime.fromisoformat(m.group(2).replace("Z", "+00:00"))
        except ValueError:
            display_date = None
        if display_date and now - display_date > MAX_ARTICLE_AGE:
            continue
        title = clean(json_unescape(m.group(3)))
        if not title:
            continue
        aid = path.rstrip("/").split("/")[-1]
        if not aid:
            continue
        items.append((aid, title, "https://biz.chosun.com" + path))
    return dedup_by_id(items)


PARSERS = {
    "thebell": parse_thebell,
    "ibtomato": parse_ibtomato,
    "edaily": parse_edaily,
    "hankyung": parse_hankyung,
}


def collect(source):
    try:
        if source["type"] == "dealsite":
            return parse_dealsite(source["category_code"])
        raw = fetch(source["url"])
        if source["type"] == "einfomax":
            return parse_einfomax(raw, source["category"])
        if source["type"] == "chosun":
            return parse_chosun(raw, source["section"])
        return PARSERS[source["type"]](raw)
    except Exception as e:
        print(f"[WARN] {source['label']} fetch failed: {e}", file=sys.stderr)
        return []


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": False}
    ).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                retry_after = 5
                try:
                    retry_after = json.loads(body).get("parameters", {}).get("retry_after", 5)
                except Exception:
                    pass
                time.sleep(retry_after + 1)
                continue
            print(f"[ERROR] telegram send failed: {e.code} {body}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[ERROR] telegram send failed: {e}", file=sys.stderr)
            time.sleep(2)
    return False


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def git(*args):
    return subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True)


def commit_and_push(sent_count):
    git("config", "user.email", "news-bot@local")
    git("config", "user.name", "news-bot")
    git("add", "state.json")
    if git("diff", "--cached", "--quiet").returncode != 0:
        git("commit", "-m", f"update state ({sent_count} sent)")
        result = git("push")
        if result.returncode != 0:
            print(f"[WARN] git push failed: {result.stderr}", file=sys.stderr)


def main():
    config = load_json(CONFIG_PATH, {})
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or config.get("telegram_bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or config.get("telegram_chat_id")
    if not token or not chat_id:
        print("Missing telegram_bot_token / telegram_chat_id (env or config.json)", file=sys.stderr)
        sys.exit(1)

    state = load_json(STATE_PATH, {})
    global_sent = state.get(GLOBAL_SENT_KEY, [])
    global_sent_set = set(global_sent)
    sent_count = 0

    for source in SOURCES:
        label = source["label"]
        source_type = source["type"]
        items = collect(source)
        if not items:
            continue
        current_ids = [aid for aid, _, _ in items]

        if label not in state:
            # first run for this source: baseline only, don't spam existing articles.
            # Also seed the global dedup set so these don't get sent later if the
            # same article also shows up under a different, already-active source.
            state[label] = current_ids[:MAX_KEEP_PER_SOURCE]
            for aid, _, _ in items:
                canonical = f"{source_type}:{aid}"
                if canonical not in global_sent_set:
                    global_sent_set.add(canonical)
                    global_sent.append(canonical)
            print(f"[INIT] {label}: baseline {len(current_ids)} articles")
            continue

        seen_ids = set(state[label])
        new_items = [it for it in items if it[0] not in seen_ids]

        for aid, title, url in reversed(new_items):  # oldest of the new batch first
            # Dedup on (source_type, article id) rather than the full URL:
            # some sites (thebell) embed the *listing page's* category code
            # into the article link, so the same article gets a different
            # URL depending on which of our sources found it.
            canonical = f"{source_type}:{aid}"
            if canonical not in global_sent_set:
                text = f"{title}\n{url}"
                if send_telegram(token, chat_id, text):
                    sent_count += 1
                    print(f"[SENT] {label}: {title}")
                time.sleep(0.5)
            global_sent_set.add(canonical)
            global_sent.append(canonical)

        merged = current_ids + [i for i in state[label] if i not in set(current_ids)]
        state[label] = merged[:MAX_KEEP_PER_SOURCE]

    state[GLOBAL_SENT_KEY] = global_sent[-MAX_GLOBAL_SENT:]
    save_json(STATE_PATH, state)
    print(f"Done. Sent {sent_count} new articles.")
    commit_and_push(sent_count)


if __name__ == "__main__":
    main()
