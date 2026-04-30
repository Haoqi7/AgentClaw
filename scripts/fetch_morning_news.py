#!/usr/bin/env python3
"""
早朝简报采集脚本
每日 06:00 自动运行，抓取全球新闻 RSS → data/morning_brief_YYYYMMDD.json
覆盖: 政治 | 军事 | 经济 | AI大模型
"""
import json, pathlib, datetime, subprocess, re, sys, os, logging, time
from xml.etree import ElementTree as ET
from file_lock import atomic_json_write
from utils import validate_url
from urllib.request import Request, urlopen

log = logging.getLogger('朝报')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

DATA = pathlib.Path(__file__).resolve().parent.parent / 'data'

# ── RSS 源配置 ──────────────────────────────────────────────────────────
# [Fix 1] 替换死掉的 Reuters RSS (404) 和不稳定的 RSSHub 公共实例
# 使用验证可达的公开 RSS 源
FEEDS = {
    '政治': [
        ('BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
        ('Al Jazeera', 'https://www.aljazeera.com/xml/rss/all.xml'),
        ('CNN World', 'http://rss.cnn.com/rss/edition_world.rss'),
    ],
    '军事': [
        ('Defense News', 'https://www.defensenews.com/rss/'),
        ('BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
        ('Al Jazeera', 'https://www.aljazeera.com/xml/rss/all.xml'),
    ],
    '经济': [
        ('CNBC', 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'),
        ('BBC Business', 'https://feeds.bbci.co.uk/news/business/rss.xml'),
        ('Financial Times', 'https://www.ft.com/rss/home'),
    ],
    'AI大模型': [
        ('Hacker News', 'https://hnrss.org/newest?q=AI+LLM+model&points=50'),
        ('VentureBeat AI', 'https://venturebeat.com/category/ai/feed/'),
        ('MIT Tech Review', 'https://www.technologyreview.com/feed/'),
    ],
}

CATEGORY_KEYWORDS = {
    '军事': ['war', 'military', 'troops', 'attack', 'missile', 'army', 'navy', 'weapons',
              '战', '军', '导弹', '士兵', 'ukraine', 'russia', 'china sea', 'nato'],
    'AI大模型': ['ai', 'llm', 'gpt', 'claude', 'gemini', 'openai', 'anthropic', 'deepseek',
                'machine learning', 'neural', 'model', '大模型', '人工智能', 'chatgpt'],
}

# [Fix 2] curl_rss 增加 2 次重试 + 20s 超时 + Accept 头
def curl_rss(url, timeout=20, retries=2):
    """用 urllib 抓取 RSS，支持重试"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            from urllib.request import Request, urlopen
            req = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; MorningBrief/1.0)',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
            })
            response = urlopen(req, timeout=timeout)
            data = response.read().decode('utf-8', errors='ignore')
            if len(data) < 50:
                raise ValueError(f'RSS 内容过短 ({len(data)} bytes)')
            return data
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = attempt * 2
                log.debug(f'RSS 抓取失败 ({url}): {e}, {wait}s 后重试...')
                time.sleep(wait)
    log.warning(f'RSS 抓取最终失败 ({url}): {last_err}')
    return ''

def _safe_parse_xml(xml_text, max_size=5*1024*1024):
    """安全解析 XML：限制大小，禁用外部实体（防 XXE）。"""
    if len(xml_text) > max_size:
        log.warning(f'XML 内容过大 ({len(xml_text)} bytes)，跳过')
        return None
    # 剥离 DOCTYPE / ENTITY 声明以防 XXE
    cleaned = re.sub(r'<!DOCTYPE[^>]*>', '', xml_text, flags=re.IGNORECASE)
    cleaned = re.sub(r'<!ENTITY[^>]*>', '', cleaned, flags=re.IGNORECASE)
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError:
        return None


def parse_rss(xml_text):
    """解析 RSS XML → list of {title, desc, link, pub_date, image}"""
    items = []
    try:
        root = _safe_parse_xml(xml_text)
        if root is None:
            return items
        # RSS 2.0
        ns = {'media': 'http://search.yahoo.com/mrss/'}
        for item in root.findall('.//item')[:8]:
            def get(tag):
                el = item.find(tag)
                return (el.text or '').strip() if el is not None else ''
            title = get('title')
            desc  = re.sub(r'<[^>]+>', '', get('description'))[:200]
            link  = get('link')
            pub   = get('pubDate')
            # 图片
            img = ''
            enc = item.find('enclosure')
            if enc is not None and 'image' in (enc.get('type') or ''):
                img = enc.get('url', '')
            media = item.find('media:thumbnail', ns) or item.find('media:content', ns)
            if media is not None:
                img = media.get('url', img)
            items.append({'title': title, 'desc': desc, 'link': link,
                          'pub_date': pub, 'image': img})
    except Exception:
        pass
    return items

def match_category(item, category):
    """判断新闻是否属于该分类（用于军事/AI过滤）"""
    kws = CATEGORY_KEYWORDS.get(category, [])
    if not kws:
        return True
    text = (item['title'] + ' ' + item['desc']).lower()
    return any(k in text for k in kws)

# [Fix 3] fetch_category 接受 global_seen 参数实现跨分类去重
def fetch_category(category, feeds, max_items=5, global_seen=None):
    """抓取一个分类的新闻"""
    seen_urls = global_seen if global_seen is not None else set()
    results = []
    for source_name, url in feeds:
        if len(results) >= max_items:
            break
        xml = curl_rss(url)
        if not xml:
            continue
        items = parse_rss(xml)
        for item in items:
            if not item['title']:
                continue
            if item['link'] in seen_urls:
                continue
            # 军事和AI分类需要关键词过滤
            if category in CATEGORY_KEYWORDS and not match_category(item, category):
                continue
            seen_urls.add(item['link'])
            results.append({
                'title': item['title'],
                'summary': item['desc'] or item['title'],
                'link': item['link'],
                'pub_date': item['pub_date'],
                'image': item['image'],
                'source': source_name,
            })
            if len(results) >= max_items:
                break
    return results

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='强制采集，忽略幂等锁')
    args = parser.parse_args()

    # 幂等锁：防重复执行
    today = datetime.date.today().strftime('%Y%m%d')
    lock_file = DATA / f'morning_brief_{today}.lock'
    if lock_file.exists() and not args.force:
        age = datetime.datetime.now().timestamp() - lock_file.stat().st_mtime
        if age < 3600:  # 1小时内不重复
            log.info(f'今日已采集（{today}），跳过（使用 --force 强制采集）')
            return
    # 注意：lock 放到采集成功后再 touch，防止失败也锁定

    # 读取用户配置
    config_file = DATA / 'morning_brief_config.json'
    config = {}
    try:
        config = json.loads(config_file.read_text())
    except Exception:
        pass

    # 已启用的分类
    enabled_cats = set()
    if config.get('categories'):
        for c in config['categories']:
            if c.get('enabled', True):
                enabled_cats.add(c['name'])
    else:
        enabled_cats = set(FEEDS.keys())

    # 用户自定义关键词（全局加权）
    user_keywords = [kw.lower() for kw in config.get('keywords', [])]

    # 合并自定义 RSS 源
    custom_feeds = config.get('custom_feeds', [])
    merged_feeds = {}
    for cat, feeds in FEEDS.items():
        if cat in enabled_cats:
            merged_feeds[cat] = list(feeds)
    for cf in custom_feeds:
        cat = cf.get('category', '')
        feed_url = cf.get('url', '')
        if cat in enabled_cats and feed_url:
            # 校验自定义源 URL（SSRF 防护）
            if validate_url(feed_url):
                merged_feeds.setdefault(cat, []).append((cf.get('name', '自定义'), feed_url))
            else:
                log.warning(f'自定义源 URL 不合法，跳过: {feed_url}')

    log.info(f'开始采集 {today}...')
    log.info(f'  启用分类: {", ".join(enabled_cats)}')
    if user_keywords:
        log.info(f'  关注词: {", ".join(user_keywords)}')
    if custom_feeds:
        log.info(f'  自定义源: {len(custom_feeds)} 个')

    result = {
        'date': today,
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'categories': {}
    }

    # [Fix 3] 全局 seen_urls 实现跨分类去重
    # 例如 BBC World 同时出现在政治和军事，同一条新闻不会在两个分类中重复
    global_seen = set()

    for category, feeds in merged_feeds.items():
        log.info(f'  采集 {category}...')
        items = fetch_category(category, feeds, global_seen=global_seen)
        # Boost items matching user keywords
        if user_keywords:
            for item in items:
                text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
                item['_kw_hits'] = sum(1 for kw in user_keywords if kw in text)
            items.sort(key=lambda x: x.get('_kw_hits', 0), reverse=True)
            for item in items:
                item.pop('_kw_hits', None)
        result['categories'][category] = items
        log.info(f'    {category}: {len(items)} 条')

    # 写入今日文件
    today_file = DATA / f'morning_brief_{today}.json'
    atomic_json_write(today_file, result)

    # 覆写 latest（看板读这个）
    latest_file = DATA / 'morning_brief.json'
    atomic_json_write(latest_file, result)

    total = sum(len(v) for v in result['categories'].values())
    log.info(f'✅ 完成：共 {total} 条新闻 → {today_file.name}')

    # 采集成功后才写入幂等锁
    lock_file.touch()

    # ── 推送通知 ─────────────────────────────────────────────────────────
    _push_notification(result)


# ── 通知推送（企业微信 / 钉钉 / 飞书 / 通用 Webhook）────────────────────

def _push_notification(result):
    """采集成功后，按配置推送通知（纯脚本端，不依赖 server.py）"""
    config_file = DATA / 'morning_brief_config.json'
    try:
        config = json.loads(config_file.read_text())
    except Exception:
        return

    # 兼容旧配置 (feishu_webhook) 和新配置 (notification)
    notification = config.get('notification', {})
    if not notification and config.get('feishu_webhook'):
        notification = {'enabled': True, 'channel': 'feishu', 'webhook': config['feishu_webhook']}
    if not notification.get('enabled', False):
        return

    channel = notification.get('channel', '').strip().lower()
    webhook = notification.get('webhook', '').strip()
    if not webhook:
        return

    # 构造摘要消息
    date_str = result.get('date', '')
    total = sum(len(v) for v in result.get('categories', {}).values())
    if not total:
        return

    cat_lines = []
    for cat, items in result.get('categories', {}).items():
        if items:
            cat_lines.append(f'  {cat}: {len(items)} 条')
    date_fmt = f'{date_str[:4]}年{date_str[4:6]}月{date_str[6:]}日' if len(date_str) == 8 else date_str
    title = f'📰 天下要闻 · {date_fmt}'
    summary = f'共 {total} 条要闻已更新\n' + '\n'.join(cat_lines)

    # 按渠道分发
    _senders = {
        'wechat_work': _send_wechat_work,
        'wecom': _send_wechat_work,        # 别名
        'dingtalk': _send_dingtalk,
        'feishu': _send_feishu,
        'lark': _send_feishu,               # 别名
        'generic': _send_generic_webhook,
    }
    sender = _senders.get(channel)
    if not sender:
        log.warning(f'未知通知渠道: {channel}，可选: {", ".join(_senders.keys())}')
        return

    try:
        ok = sender(webhook, title, summary)
        label = {'wechat_work': '企业微信', 'wecom': '企业微信', 'dingtalk': '钉钉',
                 'feishu': '飞书', 'lark': '飞书', 'generic': 'Webhook'}.get(channel, channel)
        log.info(f'  [{label}] 推送{"成功" if ok else "失败"}')
    except Exception as e:
        log.warning(f'  通知推送异常: {e}')


def _post_json(url, payload, timeout=10):
    """通用 JSON POST 请求"""
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = Request(url, data=data, headers={
        'Content-Type': 'application/json; charset=utf-8',
        'User-Agent': 'MorningBrief/1.0',
    })
    resp = urlopen(req, timeout=timeout)
    body = resp.read().decode('utf-8', errors='ignore')
    return resp.status, body


def _send_wechat_work(webhook, title, content):
    """企业微信机器人 Webhook 推送
    https://developer.work.weixin.qq.com/document/path/91770
    """
    # 企业微信 markdown 不支持 \n，需要用 <br> 或换行
    md_content = content.replace('\\n', '\n')
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'content': f'## {title}\n{md_content}',
        },
    }
    status, body = _post_json(webhook, payload)
    resp = json.loads(body)
    return resp.get('errcode', -1) == 0


def _send_dingtalk(webhook, title, content):
    """钉钉机器人 Webhook 推送
    https://open.dingtalk.com/document/robots/custom-robot-access
    """
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': f'## {title}\n\n{content}',
        },
    }
    status, body = _post_json(webhook, payload)
    resp = json.loads(body)
    return resp.get('errcode', -1) == 0


def _send_feishu(webhook, title, content):
    """飞书机器人 Webhook 推送
    https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """
    # 飞书使用富文本消息
    payload = {
        'msg_type': 'interactive',
        'card': {
            'header': {
                'title': {'tag': 'plain_text', 'content': title},
                'template': 'blue',
            },
            'elements': [
                {'tag': 'markdown', 'content': content.replace('\\n', '\n')},
            ],
        },
    }
    status, body = _post_json(webhook, payload)
    resp = json.loads(body)
    return resp.get('code', -1) == 0 or resp.get('StatusCode', -1) == 0


def _send_generic_webhook(webhook, title, content):
    """通用 Webhook 推送（发送 JSON 格式）"""
    payload = {
        'title': title,
        'content': content,
        'timestamp': datetime.datetime.now().isoformat(),
    }
    status, body = _post_json(webhook, payload)
    return 200 <= status < 300


if __name__ == '__main__':
    main()
