"""Python 3.10+; standard library only. See README.md for the complete workflow."""
import argparse
import csv
import json
import os
import random
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
TOPICS = {
    "crash": ("BUG", "闪退/启动失败"),
    "progress": ("BUG", "存档/进度异常"),
    "bug_other": ("BUG", "其他功能异常"),
    "balance": ("数值平衡", "强度/收益失衡"),
    "onboarding": ("玩法体验", "引导/上手困难"),
    "grind": ("玩法体验", "重复劳动/内容不足"),
    "matchmaking": ("数值平衡", "匹配公平性"),
    "rank_scoring": ("数值平衡", "排位加减分机制"),
    "content_direction": ("玩法体验", "模式/内容方向"),
    "gameplay_other": ("玩法体验", "其他玩法体验"),
    "fps": ("性能优化", "帧率/卡顿"),
    "network": ("性能优化", "延迟/掉线"),
    "storage": ("性能优化", "安装包/存储占用"),
    "visual_audio": ("视听表现", "画面/建模/声音"),
    "price": ("付费体验", "定价/付费设计"),
    "payment": ("付费体验", "支付/到账异常"),
    "gacha_rewards": ("付费体验", "抽取概率/奖励福利"),
    "login_account": ("账号服务", "登录/账号/防沉迷"),
    "customer_service": ("运营服务", "客服/问题响应"),
    "events_updates": ("运营服务", "活动/更新/内容节奏"),
    "community": ("社区生态", "辱骂/骚扰/社交环境"),
    "moderation": ("社区生态", "举报/审核/社区治理"),
    "cheating": ("社区生态", "外挂/作弊/违规行为"),
    "ugc_editor": ("玩法体验", "乐园地图/创作工具"),
    "content_compliance": ("运营服务", "联动/IP/内容合规争议"),
    "praise": ("其他", "正向体验"),
    "other": ("其他", "其他/信息不足"),
}
CATEGORIES = list(dict.fromkeys(v[0] for v in TOPICS.values()))
FIELDS = ["topic", "sentiment", "intensity", "impact", "summary", "evidence", "suggestion", "needs_review"]


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def excel_safe(value):
    # Player text and model text are data, never executable spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: excel_safe(row.get(k, "")) for k in fields})


def http_json(url, body=None, key=None):
    headers = {"User-Agent": "GameFeedbackPortfolio/1.0", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = "Bearer " + key
    req = Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
    for attempt in range(3):
        try:
            with urlopen(req, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            # Never log request headers or server bodies that may include sensitive data.
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"HTTP {exc.code}; check endpoint, model, quota and key") from None
            retry_after = exc.headers.get("Retry-After", "")
            delay = min(float(retry_after), 30) if retry_after.isdigit() else 2 ** (attempt + 1)
            time.sleep(delay)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("Network timeout/failure after 3 attempts") from None
            time.sleep(2 ** (attempt + 1))


def validate(result, text):
    if not isinstance(result, dict) or set(result) != set(FIELDS):
        raise ValueError("Missing or extra output fields")
    if result["topic"] not in TOPICS or result["sentiment"] not in ("正向", "中性", "负向"):
        raise ValueError("Unknown topic or sentiment")
    for name, lo, hi in (("intensity", 0, 3), ("impact", 0, 3)):
        if type(result[name]) is not int or not lo <= result[name] <= hi:
            raise ValueError(f"Invalid {name}")
    if result["sentiment"] != "负向" and (result["intensity"] or result["impact"]):
        raise ValueError("Non-negative comments must have zero negative intensity and impact")
    if result["sentiment"] == "负向" and result["intensity"] == 0:
        raise ValueError("Negative comments need intensity 1-3")
    for name in ("summary", "evidence", "suggestion"):
        if not isinstance(result[name], str) or not result[name].strip() or len(result[name]) > 400:
            raise ValueError(f"Invalid {name}")
    if result["evidence"] not in text:
        raise ValueError("Evidence is not an exact substring of input")
    if type(result["needs_review"]) is not bool:
        raise ValueError("needs_review must be boolean")
    if result["sentiment"] == "负向" and result["topic"] == "praise":
        raise ValueError("Negative feedback cannot use praise topic")
    return result


def load_rows(path):
    rows = read_json(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError("Input must be a nonempty JSON array")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each review must be an object")
        for field in ("review_id", "text", "source", "created_at", "source_url"):
            if not isinstance(row.get(field), str):
                raise ValueError(f"Missing string field: {field}")
        if not row["review_id"] or row["review_id"] in seen:
            raise ValueError("Missing or duplicate review_id")
        seen.add(row["review_id"])
        if row["created_at"]:
            stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("created_at needs a timezone; use empty string if unknown")
    return rows


def fetch(args):
    cursor, seen, rows = "*", set(), []
    fetched_at = now()
    for _ in range(args.max_pages):
        params = dict(json=1, filter="recent", language=args.language, review_type="all",
                      purchase_type="all", num_per_page=100, cursor=cursor)
        url = f"https://store.steampowered.com/appreviews/{args.appid}?{urlencode(params)}"
        response = http_json(url)
        if response.get("success") != 1:
            raise ValueError("Steam returned unsuccessful response")
        page = response.get("reviews", [])
        for review in page:
            rid = str(review["recommendationid"])
            if rid in seen:
                continue
            seen.add(rid)
            rows.append(dict(review_id=rid, text=review["review"], source="steam",
                             source_url=f"https://store.steampowered.com/app/{args.appid}/#app_reviews_hash",
                             created_at=datetime.fromtimestamp(review["timestamp_created"], timezone.utc).isoformat(),
                             voted_up=review["voted_up"], appid=args.appid, fetched_at=fetched_at))
            if len(rows) >= args.limit:
                break
        dump(args.out, rows)  # Preserve partial data if a later page fails.
        next_cursor = response.get("cursor")
        if len(rows) >= args.limit or not page or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(1)
    dump(str(args.out) + ".meta.json", dict(requested=args.limit, collected=len(rows),
         appid=args.appid, language=args.language, filter="recent", collected_at=fetched_at,
         note="Recent review sample, not all players; API may exclude off-topic activity by default."))
    print(f"Saved {len(rows)} / requested {args.limit} reviews: {args.out}")


def fetch_appstore(args):
    """Collect Apple's public China-store RSS review feed without reviewer identity fields."""
    patterns = [
        "https://itunes.apple.com/{store}/rss/customerreviews/id={appid}/sortBy={sort}/page={page}/json",
        "https://itunes.apple.com/{store}/rss/customerreviews/page={page}/id={appid}/sortBy={sort}/json",
        "https://itunes.apple.com/{store}/rss/customerreviews/id={appid}/sortby={sort_lower}/page={page}/json",
        "https://itunes.apple.com/{store}/rss/customerreviews/page={page}/id={appid}/sortby={sort_lower}/json",
    ]
    suffixes = ["", "?l=zh_CN", "?l=zh-Hans-CN", "?cc=cn"]
    fetched_at, found, requested_urls = now(), {}, []
    for pattern in patterns:
        for suffix in suffixes:
            for page in range(1, 11):
                url = pattern.format(store=args.store, appid=args.appid, sort=args.sort,
                                     sort_lower=args.sort.lower(), page=page) + suffix
                requested_urls.append(url)
                feed = http_json(url).get("feed", {})
                entries = feed.get("entry", [])
                if isinstance(entries, dict):
                    entries = [entries]
                for entry in entries:
                    if "im:rating" not in entry:  # Some feeds may include an app metadata entry.
                        continue
                    rid = str(entry["id"]["label"])
                    title = entry.get("title", {}).get("label", "").strip()
                    body = entry.get("content", {}).get("label", "").strip()
                    text = body if not title or title == body else f"{title}\n{body}"
                    found[rid] = dict(
                        review_id=f"appstore-{args.store}-{rid}", text=text, source=f"apple_app_store_{args.store}",
                        source_url=entry.get("link", {}).get("attributes", {}).get("href", ""),
                        created_at=entry.get("updated", {}).get("label", ""), rating=int(entry["im:rating"]["label"]),
                        app_version=entry.get("im:version", {}).get("label", ""), appid=str(args.appid),
                        fetched_at=fetched_at, feed_sort=args.sort,
                    )
                if len(found) >= args.limit:
                    break
                time.sleep(args.delay)
            if len(found) >= args.limit:
                break
        if len(found) >= args.limit:
            break
    rows = sorted(found.values(), key=lambda r: r["created_at"], reverse=True)[:args.limit]
    dump(args.out, rows)
    dump(str(args.out) + ".meta.json", dict(
        requested=args.limit, collected=len(rows), appid=str(args.appid), storefront=args.store,
        sort=args.sort, collected_at=fetched_at, pages_scanned=len(requested_urls),
        page_limit_note="Apple RSS returned empty holes on some page URLs; equivalent documented URL forms were scanned and IDs deduplicated.",
        privacy_note="Reviewer names and reviewer profile IDs were intentionally omitted.",
        sample_note="Recent public App Store review snapshot; not a random sample of all players or all platforms.",
    ))
    print(f"Saved {len(rows)} / requested {args.limit} App Store reviews: {args.out}")


def call_model(row, prompt, model, base, key, thinking=""):
    body = dict(model=model, messages=[
        {"role": "system", "content": prompt + "\n允许的 topic → [类别,名称]:\n" + json.dumps(TOPICS, ensure_ascii=False)},
        {"role": "user", "content": json.dumps({"review_text": row["text"]}, ensure_ascii=False)}
    ], response_format={"type": "json_object"}, max_tokens=1200)
    if thinking:
        body["thinking"] = {"type": thinking}
    usage = Counter()
    for attempt in range(2):
        response = http_json(base.rstrip("/") + "/chat/completions", body, key)
        u = response.get("usage") or {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[field] += u.get(field, 0) or 0
        try:
            choice = response["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("Incomplete model response")
            output = validate(json.loads(choice["message"]["content"]), row["text"])
            return output, dict(usage)
        except (KeyError, IndexError, TypeError, ValueError):
            if attempt:
                raise ValueError("Model response failed JSON/field/evidence validation twice") from None
            body["messages"].append({"role": "user", "content": "上次输出未通过校验。重新分析同一评论，只输出符合全部字段、枚举和原文证据规则的 JSON。"})


def analyze(args):
    rows = load_rows(args.input)
    prompt = (ROOT / "prompt.txt").read_text(encoding="utf-8")
    key, model, base = (os.getenv(k, "") for k in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"))
    thinking = os.getenv("LLM_THINKING", "").strip()
    if not key or not model or not base:
        raise ValueError("Set LLM_API_KEY, LLM_MODEL and LLM_BASE_URL first")
    if urlparse(base).scheme != "https":
        raise ValueError("LLM_BASE_URL must use HTTPS")
    if thinking not in ("", "enabled", "disabled"):
        raise ValueError("LLM_THINKING must be empty, enabled or disabled")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = dict(model=model, base_url=base, thinking=thinking, prompt=prompt, topics=TOPICS, input_rows=rows)
    # Ordinary equality keeps resumes tied to the actual input/configuration, without hashes.
    config = json.loads(json.dumps(config))
    config_path = out / "run_config.json"
    if config_path.exists() and read_json(config_path) != config:
        raise ValueError("Input/prompt/model changed. Choose a new --out directory")
    dump(config_path, config)
    log = out / "results.jsonl"
    latest = {}
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                latest[item["review_id"]] = item
    count = 0
    for row in rows:
        if latest.get(row["review_id"], {}).get("status") == "ok":
            continue
        if count >= args.limit:
            break
        count += 1
        item = dict(row, processed_at=now(), model=model, mode="api")
        try:
            if not row["text"].strip() or len(row["text"]) > 12000:
                raise ValueError("Blank or >12000 character comment; manually review, no silent truncation")
            result, usage = call_model(row, prompt, model, base, key, thinking)
            item.update(status="ok", result=result, usage=usage)
        except (ValueError, RuntimeError) as exc:
            item.update(status="error", error=str(exc))
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            f.flush()
        latest[row["review_id"]] = item
        print(f"{count}: {row['review_id']} {item['status']}", flush=True)
        if item["status"] == "error" and any(code in item["error"] for code in ("HTTP 400", "HTTP 401", "HTTP 402", "HTTP 403", "HTTP 404")):
            print("Stopping: fix API configuration or balance before retrying.")
            break
    # Always include unattempted records, so coverage denominators stay correct.
    dump(out / "results.json", [latest.get(r["review_id"], dict(r, status="pending", mode="api")) for r in rows])


def aggregate(rows):
    modes = {r.get("mode") for r in rows}
    if len(modes) > 1:
        raise ValueError("Do not mix demo and API runs")
    good = [r for r in rows if r["status"] == "ok"]
    detail, groups, days = [], defaultdict(list), defaultdict(list)
    for r in good:
        a = validate(r["result"], r["text"])
        date = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat() if r["created_at"] else "日期未知"
        item = dict(review_id=r["review_id"], created_date_utc=date, source=r["source"],
                    source_url=r["source_url"], text=r["text"], category=TOPICS[a["topic"]][0],
                    topic_label=TOPICS[a["topic"]][1], **a)
        item["negative"] = int(a["sentiment"] == "负向")
        item["priority_contribution"] = a["intensity"] * a["impact"] if item["negative"] else 0
        item["needs_review"] = int(a["needs_review"] or a["impact"] == 3 or a["topic"] == "other")
        detail.append(item)
        days[date].append(item)
        if item["negative"]:
            groups[a["topic"]].append(item)
    topics = []
    for topic, items in groups.items():
        n = len(items)
        score = sum(r["priority_contribution"] for r in items)
        example = max(items, key=lambda r: r["priority_contribution"])
        topics.append(dict(topic=topic, category=TOPICS[topic][0], topic_label=TOPICS[topic][1],
                           negative_count=n, avg_intensity=round(sum(r["intensity"] for r in items)/n, 3),
                           avg_impact=round(sum(r["impact"] for r in items)/n, 3),
                           avg_product=score/n, priority_score=score,
                           impact3_count=sum(r["impact"] == 3 for r in items),
                           example_id=example["review_id"], evidence=example["evidence"],
                           suggestion=example["suggestion"]))
    topics.sort(key=lambda r: (-r["priority_score"], r["topic"]))
    trend = [dict(date_utc=d, analyzed=len(items), negative=sum(r["negative"] for r in items),
                  negative_rate=sum(r["negative"] for r in items)/len(items)) for d, items in sorted(days.items())]
    counts = Counter(r["status"] for r in rows)
    summary = dict(mode=next(iter(modes), "unknown"), total=len(rows), analyzed=len(good),
                   errors=counts["error"], pending=counts["pending"],
                   negative=sum(r["negative"] for r in detail),
                   negative_rate=sum(r["negative"] for r in detail)/len(good) if good else None,
                   coverage=len(good)/len(rows) if rows else None,
                   needs_review=sum(r["needs_review"] for r in detail),
                   usage={field: sum((r.get("usage") or {}).get(field, 0) or 0 for r in good)
                          for field in ("prompt_tokens", "completion_tokens", "total_tokens")})
    return detail, topics, trend, summary


def report(args):
    rows = read_json(args.input)
    detail, topics, trend, summary = aggregate(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "summary.json", summary)
    write_csv(out / "details.csv", detail, ["review_id", "created_date_utc", "source", "source_url", "text",
        "category", "topic", "topic_label", "sentiment", "intensity", "impact", "summary", "evidence",
        "suggestion", "needs_review", "negative", "priority_contribution"])
    write_csv(out / "topics.csv", topics, ["topic", "category", "topic_label", "negative_count", "avg_intensity",
        "avg_impact", "avg_product", "priority_score", "impact3_count", "example_id", "evidence", "suggestion"])
    write_csv(out / "trend.csv", trend, ["date_utc", "analyzed", "negative", "negative_rate"])
    write_csv(out / "failures.csv", [r for r in rows if r["status"] != "ok"], ["review_id", "status", "text", "error"])
    write_csv(out / "review_queue.csv", [r for r in detail if r["needs_review"]],
              ["review_id", "text", "category", "sentiment", "impact", "evidence", "suggestion"])
    label = "人工编写的演示样例，非真实玩家数据、非大模型结果" if summary["mode"] == "demo" else "API 分析草稿，运营建议待人工核实"
    rate = f"{summary['negative_rate']:.1%}" if summary["negative_rate"] is not None else "不可计算"
    lines = ["# 玩家反馈分析摘要", "", label, "",
             f"输入 {summary['total']} 条，成功 {summary['analyzed']} 条，失败 {summary['errors']} 条，待处理 {summary['pending']} 条。",
             f"成功分析样本内负向占比：{rate}；待人工复核 {summary['needs_review']} 条。", "",
             "## 优先核查的问题", ""]
    for index, t in enumerate(topics[:5], 1):
        lines += [f"{index}. {t['topic_label']}：负向 {t['negative_count']} 条，分数 {t['priority_score']}，严重影响候选 {t['impact3_count']} 条。",
                  f"   证据记录 ID：{t['example_id']}。建议草稿：{t['suggestion']}"]
    lines += ["", "## 口径与下一步", "",
              "分数 = 同一 topic 内各负向评论的（情绪强度 × 影响程度）之和，即频次 × 两者乘积的均值。",
              "严重影响候选单独人工核查，不能因频次低而忽略；评论推断不代表已确认事故。",
              "一天一行的图仅描述本次抓取样本；未出现的日期无观测数据，不能填零或推断整体舆情趋势。",
              "先复核原文和重现条件，再写责任人、验证办法和沟通建议。不推断根因、玩家流失率或修复日期。",
              "其他类别较多时先细化分类体系；一个评论只有一个主问题，可能遗漏次要诉求。"]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def sample(args):
    rows = load_rows(args.input)
    chosen = random.Random(args.seed).sample(rows, min(args.limit, len(rows)))
    labels = [dict(review_id=r["review_id"], text=r["text"], gold_category="", gold_sentiment="", notes="") for r in chosen]
    if Path(args.out).exists():
        raise ValueError("Label file already exists; choose another path to preserve human labels")
    write_csv(args.out, labels, ["review_id", "text", "gold_category", "gold_sentiment", "notes"])
    print(f"Saved {len(labels)} blind-label rows; fill all labels before evaluate")


def prepare(args):
    rows = load_rows(args.input)
    if args.eval_size + args.dev_size > len(rows):
        raise ValueError("eval-size + dev-size exceeds available reviews")
    shuffled = rows.copy()
    random.Random(args.seed).shuffle(shuffled)
    eval_rows = shuffled[:args.eval_size]
    dev_rows = shuffled[args.eval_size:args.eval_size + args.dev_size]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    label_path, dev_path = out / "blind_labels.csv", out / "dev_sample.json"
    if label_path.exists() or dev_path.exists():
        raise ValueError("Prepared files already exist; choose another --out to preserve the split")
    labels = [dict(review_id=r["review_id"], text=r["text"], gold_category="", gold_sentiment="", notes="") for r in eval_rows]
    write_csv(label_path, labels, ["review_id", "text", "gold_category", "gold_sentiment", "notes"])
    dump(dev_path, dev_rows)
    dump(out / "split_meta.json", dict(seed=args.seed, total_input=len(rows), eval_size=len(eval_rows),
         dev_size=len(dev_rows), overlap=0, created_at=now()))
    print(f"Prepared {len(dev_rows)} development reviews and {len(eval_rows)} blind-label reviews: {out}")


def audit(args):
    rows = load_rows(args.input)
    ids = [r["review_id"] for r in rows]
    texts = [r["text"] for r in rows]
    dates = [r["created_at"] for r in rows if r["created_at"]]
    ratings = Counter(str(r.get("rating", "unknown")) for r in rows)
    lengths = sorted(len(t) for t in texts)
    duplicate_text_groups = Counter(texts)
    stats = dict(
        rows=len(rows), unique_ids=len(set(ids)), blank_text=sum(not t.strip() for t in texts),
        duplicate_text_rows=sum(n - 1 for n in duplicate_text_groups.values() if n > 1),
        min_created_at=min(dates) if dates else None, max_created_at=max(dates) if dates else None,
        min_chars=lengths[0], median_chars=lengths[len(lengths)//2], max_chars=lengths[-1],
        rating_distribution=dict(sorted(ratings.items())),
        formula_like_text=sum(t.lstrip().startswith(("=", "+", "-", "@")) for t in texts),
    )
    dump(args.out, stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def evaluate(args):
    rows = read_json(args.input)
    if any(r.get("mode") != "api" for r in rows):
        raise ValueError("Only real API runs can be evaluated, not demo fixtures")
    index = {r["review_id"]: r for r in rows}
    with Path(args.labels).open(encoding="utf-8-sig", newline="") as f:
        labels = list(csv.DictReader(f))
    if not labels or len({r["review_id"] for r in labels}) != len(labels):
        raise ValueError("Empty labels or duplicate IDs")
    pairs, missing, confusion = [], [], Counter()
    for label in labels:
        cat, sent = label["gold_category"].strip(), label["gold_sentiment"].strip()
        if cat not in CATEGORIES or sent not in ("正向", "中性", "负向"):
            raise ValueError("Complete every gold label using allowed categories/sentiments first")
        row = index.get(label["review_id"])
        if row is None:
            raise ValueError("Label ID not found in run")
        if label["text"] != str(excel_safe(row["text"])):
            raise ValueError("Label text changed or labels belong to another dataset")
        if row["status"] != "ok":
            missing.append(label["review_id"])
            continue
        a = validate(row["result"], row["text"])
        predicted = TOPICS[a["topic"]][0]
        pairs.append((cat == predicted, sent == a["sentiment"]))
        confusion[(cat, predicted)] += 1
    n = len(pairs)
    score = dict(labeled=len(labels), predicted=n, failed_or_pending=len(missing),
                 coverage=n/len(labels), category_accuracy=sum(p[0] for p in pairs)/n if n else None,
                 sentiment_accuracy=sum(p[1] for p in pairs)/n if n else None,
                 end_to_end_category_success=sum(p[0] for p in pairs)/len(labels), missing_ids=missing)
    dump(args.out, score)
    write_csv(str(args.out) + ".confusion.csv", [dict(gold=k[0], predicted=k[1], count=v) for k,v in sorted(confusion.items())], ["gold", "predicted", "count"])
    print(json.dumps(score, ensure_ascii=False, indent=2))


def qa_sample(args):
    rows = read_json(args.input)
    good = [r for r in rows if r.get("status") == "ok"]
    if not good:
        raise ValueError("No successful API results available for QA")
    chosen = random.Random(args.seed).sample(good, min(args.limit, len(good)))
    output = []
    for row in chosen:
        a = validate(row["result"], row["text"])
        output.append(dict(
            review_id=row["review_id"], text=row["text"], predicted_category=TOPICS[a["topic"]][0],
            predicted_topic=a["topic"], topic_label=TOPICS[a["topic"]][1], sentiment=a["sentiment"],
            intensity=a["intensity"], impact=a["impact"], summary=a["summary"], evidence=a["evidence"],
            suggestion=a["suggestion"], needs_review=a["needs_review"], category_correct="",
            sentiment_correct="", score_reasonable="", suggestion_useful="", human_notes="",
        ))
    fields = ["review_id", "text", "predicted_category", "predicted_topic", "topic_label", "sentiment",
              "intensity", "impact", "summary", "evidence", "suggestion", "needs_review", "category_correct",
              "sentiment_correct", "score_reasonable", "suggestion_useful", "human_notes"]
    if Path(args.out).exists():
        raise ValueError("QA sample already exists; choose another --out to preserve human review")
    write_csv(args.out, output, fields)
    print(f"Saved {len(output)} API result rows for human QA: {args.out}")


def demo(args):
    examples = [
        ("更新后打开游戏就闪退，完全进不去。", "crash", "负向", 2, 3, "启动后无法进入游戏", "收集设备与版本信息，复现启动闪退并核对崩溃日志。"),
        ("昨晚的存档没了，十小时白玩，气死了！", "progress", "负向", 3, 3, "玩家报告进度丢失", "核查本地与云端存档同步，确认恢复可能性。"),
        ("这把武器太强，其他武器根本没法打。", "balance", "负向", 2, 2, "武器强度存在主观失衡反馈", "按分段检查使用率与胜率，再判断是否需要调整。"),
        ("新手教程没讲明白，我不知道任务怎么交。", "onboarding", "负向", 1, 1, "任务提交引导不清", "走查新手流程，检查任务提示可见性。"),
        ("每天都是重复刷材料，无聊透了。", "grind", "负向", 2, 2, "重复刷取带来疲劳", "核对日常任务耗时与内容重复程度。"),
        ("一进城就掉帧，战斗也卡得难受。", "fps", "负向", 2, 2, "城市场景与战斗出现卡顿", "按设备配置和场景采样帧时间，定位性能瓶颈。"),
        ("团战掉到十几帧，太难受了。", "fps", "负向", 2, 2, "团战帧率过低", "收集团战场景和画质配置，尝试复现。"),
        ("每局都掉线，根本打不完。", "network", "负向", 2, 3, "频繁掉线中断对局", "按地区与时间核查连接日志。"),
        ("皮肤价格太高了，希望便宜点。", "price", "负向", 1, 1, "皮肤价格接受度低", "调查价格接受区间，区分外观定价和付费强度问题。"),
        ("钱扣了道具没到账，等一天了！", "payment", "负向", 2, 3, "玩家报告付费未到账", "通过客服核对订单和发货记录，确认补发条件。"),
        ("音乐和探索都很棒，很喜欢。", "visual_audio", "正向", 0, 0, "认可音乐和探索体验", "归档视听表现的正向反馈，供内容策划参考。"),
        ("今天玩了两小时。", "other", "中性", 0, 0, "仅描述游玩时长", "无明确诉求，保留观察。"),
    ]
    raw, results = [], []
    for i, (text, topic, sent, intensity, impact, summary, suggestion) in enumerate(examples, 1):
        row = dict(review_id=f"DEMO-{i:03d}", text=text, source="synthetic_demo",
                   source_url="", created_at=f"2026-09-{10+(i-1)//4:02d}T12:00:00+00:00")
        result = dict(topic=topic, sentiment=sent, intensity=intensity, impact=impact, summary=summary,
                      evidence=text, suggestion=suggestion, needs_review=topic == "other")
        raw.append(row)
        results.append(dict(row, status="ok", mode="demo", model="handwritten_fixture", result=validate(result,text)))
    out = Path(args.out)
    dump(out / "reviews.json", raw)
    dump(out / "results.json", results)
    report(argparse.Namespace(input=out / "results.json", out=out / "report"))


def positive(value):
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return n


def main():
    p = argparse.ArgumentParser(description="AI 玩家反馈运营助手")
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo"); d.add_argument("--out", default="runs/demo"); d.set_defaults(fn=demo)
    f = sub.add_parser("fetch")
    f.add_argument("--appid", type=positive, required=True)
    f.add_argument("--limit", type=positive, default=300)
    f.add_argument("--max-pages", type=positive, default=30)
    f.add_argument("--language", default="schinese")
    f.add_argument("--out", default="data/reviews.json"); f.set_defaults(fn=fetch)
    fa = sub.add_parser("fetch-appstore")
    fa.add_argument("--appid", type=positive, required=True)
    fa.add_argument("--store", default="cn")
    fa.add_argument("--limit", type=positive, default=200)
    fa.add_argument("--delay", type=float, default=0.15)
    fa.add_argument("--sort", choices=("mostRecent", "mostHelpful"), default="mostRecent")
    fa.add_argument("--out", default="data/reviews.json"); fa.set_defaults(fn=fetch_appstore)
    a = sub.add_parser("analyze")
    a.add_argument("--input", default="data/reviews.json")
    a.add_argument("--out", default="runs/v1")
    a.add_argument("--limit", type=positive, default=10); a.set_defaults(fn=analyze)
    r = sub.add_parser("report")
    r.add_argument("--input", default="runs/v1/results.json")
    r.add_argument("--out", default="runs/v1/report"); r.set_defaults(fn=report)
    s = sub.add_parser("sample")
    s.add_argument("--input", default="data/reviews.json")
    s.add_argument("--limit", type=positive, default=50)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--out", default="data/labels.csv"); s.set_defaults(fn=sample)
    ps = sub.add_parser("prepare")
    ps.add_argument("--input", default="data/reviews.json")
    ps.add_argument("--eval-size", type=positive, default=50)
    ps.add_argument("--dev-size", type=positive, default=30)
    ps.add_argument("--seed", type=int, default=42)
    ps.add_argument("--out", default="data/day1_split"); ps.set_defaults(fn=prepare)
    au = sub.add_parser("audit")
    au.add_argument("--input", default="data/reviews.json")
    au.add_argument("--out", default="data/day1_audit.json"); au.set_defaults(fn=audit)
    e = sub.add_parser("evaluate")
    e.add_argument("--input", default="runs/v1/results.json")
    e.add_argument("--labels", default="data/labels.csv")
    e.add_argument("--out", default="runs/v1/evaluation.json"); e.set_defaults(fn=evaluate)
    q = sub.add_parser("qa-sample")
    q.add_argument("--input", default="runs/v1/results.json")
    q.add_argument("--limit", type=positive, default=30)
    q.add_argument("--seed", type=int, default=2026)
    q.add_argument("--out", default="runs/v1/qa_sample.csv"); q.set_defaults(fn=qa_sample)
    args = p.parse_args()
    try:
        args.fn(args)
    except (ValueError, RuntimeError, OSError) as exc:
        p.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
