#!/usr/bin/env python3
"""
早朝简报采集脚本
每日 06:00 自动运行，抓取全球新闻 RSS → data/morning_brief_YYYYMMDD.json
覆盖分类由配置文件决定，信息源全部从配置读取
"""
import json, pathlib, datetime, subprocess, re, sys, os, logging, time
from xml.etree import ElementTree as ET
from file_lock import atomic_json_write
from utils import validate_url
from urllib.request import Request, urlopen

log = logging.getLogger('朝报')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

DATA = pathlib.Path(__file__).resolve().parent.parent / 'data'

# ── 受保护 RSS 源（始终存在于配置中，不可删除）──────────────────────────
PROTECTED_FEEDS = [
    {'name': '微博热搜', 'url': 'https://rsshub.app/weibo/search/hot', 'category': '社会', 'protected': True},
    {'name': '知乎日报', 'url': 'https://rsshub.app/zhihu/daily', 'category': '科技', 'protected': True},
    {'name': '少数派',   'url': 'https://sspai.com/feed', 'category': '科技', 'protected': True},
]

# ── 关键词过滤（用于军事/AI等需要精准分类的场景）────────────────────────
CATEGORY_KEYWORDS = {
    '军事': ['war', 'military', 'troops', 'attack', 'missile', 'army', 'navy', 'weapons',
              '战', '军', '导弹', '士兵', 'ukraine', 'russia', 'china sea', 'nato'],
    'AI大模型': ['ai', 'llm', 'gpt', 'claude', 'gemini', 'openai', 'anthropic', 'deepseek',
                'machine learning', 'neural', 'model', '大模型', '人工智能', 'chatgpt'],
}

# ── RSS 抓取 ──────────────────────────────────────────────────────────
def curl_rss(url, timeout=20, retries=2):
    """用 urllib 抓取 RSS，支持重试"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
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

def _ensure_protected_feeds(config):
    """确保受保护源始终存在于配置中"""
    feeds = config.get('feeds', [])
    existing_urls = {f.get('url') for f in feeds}
    changed = False
    for pf in PROTECTED_FEEDS:
        if pf['url'] not in existing_urls:
            feeds.insert(0, pf)
            changed = True
    if changed:
        config['feeds'] = feeds
    return config, changed

def _migrate_config(config):
    """迁移旧配置格式 → 新格式"""
    changed = False
    # 1. custom_feeds → feeds
    if 'custom_feeds' in config and 'feeds' not in config:
        config['feeds'] = [{'name': f.get('name', ''), 'url': f.get('url', ''),
                            'category': f.get('category', ''), 'protected': False}
                           for f in config.pop('custom_feeds') if f.get('url')]
        changed = True
    # 2. feishu_webhook → notification
    if 'feishu_webhook' in config and 'notification' not in config:
        webhook = config.pop('feishu_webhook', '').strip()
        config['notification'] = {'enabled': bool(webhook), 'channel': 'feishu', 'webhook': webhook}
        changed = True
    # 3. 确保受保护源存在
    config, prot_changed = _ensure_protected_feeds(config)
    return config, changed or prot_changed

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

    # 读取用户配置
    config_file = DATA / 'morning_brief_config.json'
    config = {}
    try:
        config = json.loads(config_file.read_text())
    except Exception:
        pass

    # 迁移旧配置 + 确保受保护源
    config, config_changed = _migrate_config(config)
    if config_changed:
        try:
            atomic_json_write(config_file, config)
            log.info('已自动迁移/补全配置')
        except Exception as e:
            log.warning(f'配置迁移写入失败: {e}')

    # 已启用的分类
    enabled_cats = set()
    if config.get('categories'):
        for c in config['categories']:
            if c.get('enabled', True):
                enabled_cats.add(c['name'])
    else:
        # 默认启用所有在 feeds 中出现的分类
        for f in config.get('feeds', []):
            cat = f.get('category', '')
            if cat:
                enabled_cats.add(cat)
        if not enabled_cats:
            enabled_cats = {'社会', '科技'}

    # 用户自定义关键词（全局加权）
    user_keywords = [kw.lower() for kw in config.get('keywords', [])]

    # 从配置中读取 feeds 列表，按分类分组
    merged_feeds = {}
    for f in config.get('feeds', []):
        cat = f.get('category', '')
        url = f.get('url', '')
        name = f.get('name', '未命名')
        if cat in enabled_cats and url:
            if validate_url(url):
                merged_feeds.setdefault(cat, []).append((name, url))
            else:
                log.warning(f'源 URL 不合法，跳过: {url}')

    log.info(f'开始采集 {today}...')
    log.info(f'  启用分类: {", ".join(enabled_cats)}')
    if user_keywords:
        log.info(f'  关注词: {", ".join(user_keywords)}')
    total_feeds = sum(len(v) for v in merged_feeds.values())
    log.info(f'  信息源: {total_feeds} 个')

    if not merged_feeds:
        log.warning('当前无可用信息源，请在前端添加 RSS 源')
        return

    result = {
        'date': today,
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'categories': {}
    }

    # 全局 seen_urls 实现跨分类去重
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
    if os.environ.get('SKIP_PUSH') != '1':
        _push_notification(result)


# ── 通知推送（使用 channels 模块统一发送）────────────────────────────────

def _push_notification(result):
    """采集成功后，按配置推送通知（使用 channels 模块）"""
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

    channel_type = notification.get('channel', '').strip().lower()
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
    content = f'共 {total} 条要闻已更新\n' + '\n'.join(cat_lines)

    # 使用 channels 模块统一发送
    channels_dir = pathlib.Path(__file__).resolve().parent.parent / 'dashboard' / 'channels'
    try:
        if channels_dir.is_dir() and str(channels_dir.parent) not in sys.path:
            sys.path.insert(0, str(channels_dir.parent))
        from channels import get_channel
        channel_cls = get_channel(channel_type)
        if not channel_cls:
            log.warning(f'未知通知渠道: {channel_type}')
            return
        if not channel_cls.validate_webhook(webhook):
            log.warning(f'{channel_cls.label} Webhook URL 无效: {webhook[:50]}')
            return
        ok = channel_cls.send(webhook, title, content)
        log.info(f'[{channel_cls.label}] 推送{"成功" if ok else "失败"}')
    except ImportError:
        log.warning('channels 模块不可用，跳过通知推送')
    except Exception as e:
        log.warning(f'通知推送异常: {e}')


if __name__ == '__main__':
    main()
