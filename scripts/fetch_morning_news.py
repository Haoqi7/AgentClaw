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

# ── 预置 RSS 源（首次使用时自动添加，用户可删除）──────────────────────────
PRESET_FEEDS = [
    {'name': '微博热搜', 'url': 'https://rsshub.app/weibo/search/hot', 'category': '社会', 'protected': False},
    {'name': '知乎日报', 'url': 'https://rsshub.app/zhihu/daily', 'category': '科技', 'protected': False},
    {'name': '少数派',   'url': 'https://sspai.com/feed', 'category': '科技', 'protected': False},
]

# ── RSS 抓取 ──────────────────────────────────────────────────────────
def curl_rss(url, timeout=15, retries=1):
    """用 urllib 抓取 RSS，支持重试。默认减少重试次数和超时防止卡死系统。"""
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

def fetch_category(category, feeds, max_items=5, global_seen=None, keywords=None):
    """抓取一个分类的新闻。keywords: 用户关键词列表，传入后边抓边过滤（不匹配的不计入条数）"""
    seen_urls = global_seen if global_seen is not None else set()
    results = []
    kw_lower = [k.lower() for k in (keywords or [])]
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
            # 用户关键词过滤：不匹配的不计入条数，继续找
            if kw_lower:
                text = (item['title'] + ' ' + (item['desc'] or '')).lower()
                if not any(k in text for k in kw_lower):
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

def _ensure_preset_feeds(config):
    """首次使用时添加预置源（用户可删除）"""
    feeds = config.get('feeds', [])
    if feeds:  # 已有源则不添加
        return config, False
    changed = False
    for pf in PRESET_FEEDS:
        feeds.append(pf)
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
    # 3. 清理受保护标记
    feeds = config.get('feeds', [])
    for f in feeds:
        if f.get('protected'):
            f['protected'] = False
            changed = True
    # 4. 首次使用添加预置源
    config, preset_changed = _ensure_preset_feeds(config)
    return config, changed or preset_changed

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='强制采集，忽略幂等锁')
    parser.add_argument('--categories', type=str, default='', help='仅采集指定分类（逗号分隔，如 科技,经济）')
    parser.add_argument('--keywords', type=str, default=None, help='仅采集包含指定关键词的新闻（逗号分隔，如 AI,大模型）；传空串则覆盖配置文件关键词')
    parser.add_argument('--feed-urls', type=str, default='', help='仅采集指定URL的信息源（逗号分隔）')
    parser.add_argument('--max-items', type=int, default=5, help='每个分类最多采集条数（默认5）')
    parser.add_argument('--cleanup', type=int, nargs='?', const=7, default=None,
                        help='独立清理模式：删除超过N天的简报（默认7天），不执行采集')
    args = parser.parse_args()

    # 独立清理模式
    if args.cleanup is not None:
        _cleanup_old_briefs(max_days=args.cleanup)
        return

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

    # 已启用的分类（支持 --categories 过滤）
    filter_cats = set(c.strip() for c in args.categories.split(',') if c.strip()) if args.categories else set()
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
    # 如果指定了 --categories，仅采集指定分类
    if filter_cats:
         enabled_cats = filter_cats

    # 用户自定义关键词（全局加权）+ 命令行关键词过滤
    cmd_keywords = set()
    if args.keywords is not None:
        # --keywords 被显式传入（含空串），覆盖配置文件关键词
        cmd_keywords = set(k.strip().lower() for k in args.keywords.split(',') if k.strip())
        user_keywords = list(cmd_keywords)
    else:
        # 未传 --keywords，使用配置文件关键词（兼容直接运行脚本）
        user_keywords = [kw.lower() for kw in config.get('keywords', [])]

    # 从配置中读取 feeds 列表，按分类分组
    merged_feeds = {}
    # 支持 --feed-urls 过滤：仅使用指定URL的信息源
    allowed_feed_urls = set(args.feed_urls.split(',')) if args.feed_urls else None
    for f in config.get('feeds', []):
        cat = f.get('category', '')
        url = f.get('url', '')
        name = f.get('name', '未命名')
        if allowed_feed_urls and url not in allowed_feed_urls:
            continue
        if cat in enabled_cats and url:
            if validate_url(url, allowed_schemes=('http', 'https')):
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
        # 边抓边过滤：传入 keywords 和 max_items，不匹配关键词的不计入条数
        items = fetch_category(category, feeds, max_items=args.max_items, global_seen=global_seen, keywords=user_keywords if user_keywords else None)
        # 关键词过滤已在 fetch_category 内完成，这里只做加权排序（非严格模式）
        if user_keywords and not cmd_keywords:
            # 加权排序：包含关键词的排前面
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

    # ── 清理超过7天的历史简报 ──────────────────────────────────────────────
    _cleanup_old_briefs(max_days=7)

    # ── 推送通知 ─────────────────────────────────────────────────────────
    if os.environ.get('SKIP_PUSH') != '1':
        _push_notification(result)


# ── 清理过期历史简报 ─────────────────────────────────────────────────
def _cleanup_old_briefs(max_days=7):
    """删除超过 max_days 天的简报 JSON 和 lock 文件"""
    cutoff = (datetime.date.today() - datetime.timedelta(days=max_days)).strftime('%Y%m%d')
    count = 0
    for f in DATA.glob('morning_brief_????????.json'):
        d = f.stem.replace('morning_brief_', '')
        if d.isdigit() and len(d) == 8 and d < cutoff:
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
    for f in DATA.glob('morning_brief_????????.lock'):
        d = f.stem.replace('morning_brief_', '')
        if d.isdigit() and len(d) == 8 and d < cutoff:
            try:
                f.unlink()
            except Exception:
                pass
    if count:
        log.info(f'已清理 {count} 个超过 {max_days} 天的历史简报')


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
