import sys
import csv
import json
import time
import pickle
import asyncio
import warnings
from datetime import datetime
from urllib.parse import urlparse
from time import mktime, struct_time

import aiohttp
import aiofiles
import feedparser
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from headers import headers

FEED_FIELDS = ["language","title","subtitle","link","tags","sy_updateperiod","sy_updatefrequency","updated"]
CONTENT_FIELDS = FEED_FIELDS + ["updated_isoformat","latest_article_published","latest_article_published_isoformat","entry_count","status_code"]
ALL_FIELDS = ["rss_href"] + CONTENT_FIELDS + ["fetch_ts_isoformat","exception_class","exception_text"]

CONTENT_TYPES_HTML = [
    "text/html",
    "text/plain",
]

CONTENT_TYPES_RSS = [
    "application/atom+xml",
    "application/rss+xml",
    "application/rdf+xml",
    "application/x-rss+xml",
    "application/xml",
    "text/xml",
    "text/html",
    "text/plain",
    "xml",
]

COMMENT_FEED_TITLE_PREFIXES = [
    "comments on:",
    "comentarios en:",
    "commentaires sur",
    "reacties op:",
    "komente te:",
    "kommentare zu:",
    "comentários sobre:",
    "kommentarer til:",
    "kommentarer på:",
]


class InvalidContentType(Exception):
    pass


def get_rss_link(soup, url):

    def fix_relative_href(href):
        p = urlparse(url)
        if href.startswith("/"):
            return f"{p.scheme}://{p.netloc}{href}"
        elif not href.startswith("http"):
            return f"{p.scheme}://{p.netloc}/{href}"
        return href

    rss = []
    for tag in soup.find_all("link"):
        _type = tag.attrs.get("type") or ""
        href = tag.attrs.get("href")
        if (_type.startswith("application/rss") or _type.startswith("application/atom")) and href:
            rss.append(fix_relative_href(href).strip())

    rss.sort(key=lambda s: len(s))
    return rss[0] if rss else None


def first_line(val):
    return (val or "").split("\n")[0].replace("\t", " ").replace("\r", " ").strip()


def rss_metadata(rss, text, status_code):

    now = datetime.now().replace(microsecond=0).isoformat()

    if not text or status_code != 200:
        error = f"no content, http status code: {status_code}"
        meta = [rss] + [None for _ in CONTENT_FIELDS] + [now, "HTTPStatusException", error]
        return meta

    meta = {"status_code": status_code}
    feed = feedparser.parse(text)

    for field in FEED_FIELDS:
        val = getattr(feed.feed, field, None)
        meta[field] = first_line(val) if isinstance(val, str) else val

    if any(meta["title"].lower().startswith(prefix) for prefix in COMMENT_FEED_TITLE_PREFIXES):
        return None

    try:
        tags = meta["tags"] or []
        if feed.entries:
            if not isinstance(tags, list) or not tags:
                tags = getattr(feed.entries[0], "tags", []) or []

        max_tag_count = 32
        tags = [t.get("term","") for t in tags if t.get("term","")][:max_tag_count]
        tags = [first_line(t) for t in tags if t and t.strip()]
        meta["tags"] = json.dumps(tags)
    except Exception as e:
        meta["tags"] = "[]"    

    def isoformat(ts):
        return datetime.fromtimestamp(mktime(ts)).isoformat()

    if meta["updated"]:
        if isinstance(getattr(feed.feed, "updated_parsed", None), struct_time):
            meta["updated_isoformat"] = isoformat(feed.feed.updated_parsed)
            try:
                meta["updated"] = int(mktime(feed.feed.updated_parsed))
            except:
                pass

    if feed.entries:
        if isinstance(getattr(feed.entries[0], "published_parsed", None), struct_time):
            meta["latest_article_published_isoformat"] = isoformat(feed.entries[0].published_parsed)
            try:
                meta["latest_article_published"] = int(mktime(feed.entries[0].published_parsed))
            except:
                pass

        meta["entry_count"] = len(feed.entries)

    return [rss] + [meta.get(field) for field in CONTENT_FIELDS] + [now, None, None]


async def get_url(session, url, allowed_content_types):
    async with session.get(url, headers=headers) as response:

        if response.status != 200:
            return None, response.status

        content_type = response.headers.get("content-type") or ""
        content_type = content_type.split(";")[0].strip()

        if content_type not in allowed_content_types:
            raise InvalidContentType(content_type)

        text = await response.text()
        return text, response.status


async def get_rss(session, url):
    try:
        try:
            text, status_code = await get_url(session, url, CONTENT_TYPES_HTML)
            if not text or status_code != 200:
                return
            soup = BeautifulSoup(text, "lxml")
            rss = get_rss_link(soup, url)
            if not rss:
                return
        except Exception as e:
            return

        try:
            text, status_code = await get_url(session, rss, CONTENT_TYPES_RSS)
            meta = rss_metadata(rss, text, status_code)
            if meta is None:
                return
        except Exception as e:
            now = datetime.now().replace(microsecond=0).isoformat()
            meta = [rss] + [None for _ in CONTENT_FIELDS] + [now, e.__class__.__name__, str(e)]

        async with aiofiles.open(sys.argv[1], mode="a") as f:
            writer = csv.writer(f, delimiter="\t")
            await writer.writerow(meta)

    except Exception as e:
        print(f"{e.__class__.__name__} - {e} - {url}")


async def main():
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=1)
    timeout = aiohttp.ClientTimeout(total=3600, sock_connect=10, sock_read=10)

    try:
        seen_domains = pickle.load(open("seen_domains.pickle", "rb"))
    except FileNotFoundError:
        seen_domains = set()

    try:
        max_line_size = 8190 * 4
        max_field_size = 8190 * 4
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, max_line_size=max_line_size, max_field_size=max_field_size) as session:
            async with asyncio.TaskGroup() as tg:
                async for url in aiofiles.stdin:

                    url = url.strip()
                    try:
                        p = urlparse(url)
                    except:
                        continue

                    if p.netloc in seen_domains:
                        continue

                    seen_domains.add(p.netloc)

                    task = tg.create_task(get_rss(session, url))
    except asyncio.CancelledError:
        pickle.dump(seen_domains, open("seen_domains.pickle", "wb"))


if __name__=="__main__":
    asyncio.run(main())
