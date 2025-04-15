To test the scalability of an RSS reader project I wanted a very large list of RSS feeds and couldn't find one. A good curated source is [ooh.directory](https://ooh.directory/) but I was looking for a much bigger list, quantity over quality.

Because Bluesky is so open and friendly to data analysis I decided to source links by reading all posts from its firehose. The code in this repo finds all links included in posts, retrieves those pages, and looks in the meta tags for the presence of an RSS href. If one is found, it's fetched and its metadata is stored in the output TSV file.

I turned this on long enough to collect about 29,000 feeds, which are in feeds.tsv. The entries in the file are current as of April 2025, at which time 26,000 were live with at least one article and 22,000 had an article published within the last six weeks.

I use [websocat](https://github.com/vi/websocat) to read from the [Bluesky Jetstream](https://github.com/bluesky-social/jetstream) and together with [jq](https://github.com/jqlang/jq) it provides the "fountain" of URLs without having to write much code. The feeds are parsed with [feedparser](https://github.com/kurtmckee/feedparser).

The necessary Python packages to install are aiohttp, aiofiles, feedparser, and beautifulsoup4.

## Usage:

```
websocat "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post" \
    | tee >(grep -v "^{") | grep "^{" \
    | jq --unbuffered -r 'select(.commit.operation == "create")
    | [(.commit.record.facets
        | select(length > 0)[]
        | select(.features.[]."$type" | contains("#link")).features[].uri)][]' \
    | python -u find-feeds.py feeds.tsv
```

The Python program input is one URL per line and can come from any source.

The output TSV file has the following fields:

1. RSS href
1. language
1. title
1. subtitle
1. site href
1. tags (json list) (first 32 only)
1. update period
1. update frequency
1. feed updated (seconds)
1. feed updated (timestamp)
1. latest article published (seconds)
1. latest article published (timestamp)
1. number of entries fetched
1. HTTP status code of fetch
1. timestamp of fetch
1. exception class, if unsuccessful fetch
1. exception text, if unsuccessful fetch

Feed updated and last article published timestamp fields may be null where parsing is not possible.

This data set loads cleanly into Postgres with this table structure and load statement:


```sql
CREATE TABLE rss_feeds (
    rss_href varchar,
    language varchar,
    title varchar,
    subtitle varchar,
    site_href varchar,
    tags jsonb,
    sy_updateperiod varchar,
    sy_updatefrequency varchar,
    updated_text varchar,
    updated timestamptz,
    latest_article_published_text varchar,
    latest_article_published timestamptz,
    entry_count int,
    status_code varchar,
    fetched timestamptz,
    exception_class varchar,
    exception_text varchar
);
```

```bash
cat feeds.tsv |psql -Xc "copy rss_feeds from stdin with delimiter E'\t' csv"
```

## Limitations

This only attempts one lookup per domain, which means that it does not handle the case of many sites/feeds on one domain, such as each Youtube channel or Bluesky account having its own RSS feed.

Many sites produce a distinct RSS feed of the comments of each article they publish. This code attempts to exclude those. When an article's meta tags specify more than one RSS feed, only the one with the shortest href is chosen. This prefers `https://site.com/rss` over `https://site.com/article-title/rss`. But it's not a perfect heuristic. Sometimes the only feed present in the meta tags is a comments feed, and it gets chosen. Other times, there are multiple legit feeds in the meta tags and only one is chosen.

Any errors encountered fetching a link (HTML) are silently ignored, whereas errors encountered fetching a feed are logged to the exception_class and exception_text columns.

There's no automated mechanism for re-establishing the Jetstream connection when it goes away, the command needs to be restarted.

Note that there will be a considerable amount of NFSW content in the feeds that are output.
