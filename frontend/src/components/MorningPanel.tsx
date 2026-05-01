import { useEffect, useState, useRef, useCallback } from 'react';
import { useStore } from '../store';
import { api } from '../api';
import type { SubConfig, MorningNewsItem, FeedSource, FeedCheckResult, ChannelInfo, SubscriptionTask, PushHistoryItem } from '../api';

const CAT_META: Record<string, { icon: string; color: string; desc: string }> = {
  '政治': { icon: '🏛️', color: '#6a9eff', desc: '全球政治动态' },
  '军事': { icon: '⚔️', color: '#ff5270', desc: '军事与冲突' },
  '经济': { icon: '💹', color: '#2ecc8a', desc: '经济与市场' },
  'AI大模型': { icon: '🤖', color: '#a07aff', desc: 'AI与大模型进展' },
  '社会': { icon: '🌍', color: '#f0b429', desc: '社会热点' },
  '科技': { icon: '💡', color: '#4ecdc4', desc: '科技与创新' },
};

const DEFAULT_CATS = ['社会', '科技', '政治', '军事', '经济', 'AI大模型'];

const EMOJI_PRESETS: Record<string, { emoji: string; label: string }[]> = {
  '政治': [{ emoji: '🏛️', label: '政治' }, { emoji: '🇨🇳', label: '国事' }],
  '军事': [{ emoji: '⚔️', label: '军事' }, { emoji: '🛡️', label: '防务' }],
  '经济': [{ emoji: '💹', label: '经济' }, { emoji: '📈', label: '市场' }, { emoji: '💰', label: '财经' }],
  '科技': [{ emoji: '💡', label: '科技' }, { emoji: '🔬', label: '研究' }],
  '社会': [{ emoji: '🌍', label: '社会' }, { emoji: '📰', label: '热点' }],
  'AI大模型': [{ emoji: '🤖', label: 'AI' }, { emoji: '🧠', label: '智能' }],
};
const DEFAULT_EMOJIS = ['📰', '📋', '🔔', '📌', '🎯', '📡', '🌐', '💡', '⭐', '🔥'];

const MAX_TASKS = 12;

export default function MorningPanel() {
  const morningBrief = useStore((s) => s.morningBrief);
  const subConfig = useStore((s) => s.subConfig);
  const morningTasks = useStore((s) => s.morningTasks);
  const loadMorning = useStore((s) => s.loadMorning);
  const loadSubConfig = useStore((s) => s.loadSubConfig);
  const toast = useStore((s) => s.toast);

  const [showSettings, setShowSettings] = useState(false);
  const [localConfig, setLocalConfig] = useState<SubConfig | null>(null);
  const [collectingTaskId, setCollectingTaskId] = useState<string | null>(null);
  const [testingTaskId, setTestingTaskId] = useState<string | null>(null);
  const [historyTaskId, setHistoryTaskId] = useState<string | null>(null);
  const [pushHistoryData, setPushHistoryData] = useState<PushHistoryItem[]>([]);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);

  useEffect(() => { loadMorning(); }, [loadMorning]);
  useEffect(() => {
    if (subConfig) setLocalConfig(JSON.parse(JSON.stringify(subConfig)));
  }, [subConfig]);
  useEffect(() => {
    return () => { /* cleanup */ };
  }, []);

  const enabledSet = localConfig
    ? new Set((localConfig.categories || []).filter((c) => c.enabled).map((c) => c.name))
    : new Set(DEFAULT_CATS);
  const userKws = (localConfig?.keywords || []).map((k) => k.toLowerCase());

  const cats = morningBrief?.categories || {};
  const dateStr = morningBrief?.date
    ? morningBrief.date.replace(/(\d{4})(\d{2})(\d{2})/, '$1年$2月$3日')
    : '';
  const totalNews = Object.values(cats).flat().length;

  // 单任务采集
  const collectTask = async (task: SubscriptionTask) => {
    setCollectingTaskId(task.id);
    try {
      const r = await api.collectTask(task.id);
      if (r.ok) {
        toast(`✅ ${task.emoji} ${task.name} 采集已触发`, 'ok');
        // 等待一下让后端完成采集
        setTimeout(() => { loadMorning(); }, 5000);
      } else {
        toast(r.error || '采集失败', 'err');
      }
    } catch { toast('采集请求失败', 'err'); }
    setCollectingTaskId(null);
  };

  // 测试推送
  const testPush = async (task: SubscriptionTask) => {
    setTestingTaskId(task.id);
    try {
      const r = await api.pushTest(task.id);
      if (r.ok) toast(`✅ ${task.emoji} ${task.name} 测试推送已发送`, 'ok');
      else toast(r.error || '测试推送失败', 'err');
    } catch { toast('测试推送请求失败', 'err'); }
    setTestingTaskId(null);
  };

  // 查看推送历史
  const loadPushHistory = async (taskId: string) => {
    if (historyTaskId === taskId) { setHistoryTaskId(null); return; }
    try {
      const r = await api.pushHistory(taskId);
      if (r.ok) { setPushHistoryData(r.history || []); setHistoryTaskId(taskId); }
    } catch { toast('推送历史加载失败', 'err'); }
  };

  // 删除任务
  const deleteTask = async (task: SubscriptionTask) => {
    if (!confirm(`确定删除订阅卡片「${task.emoji} ${task.name}」？`)) return;
    setDeletingTaskId(task.id);
    try {
      const r = await api.deleteMorningTask(task.id);
      if (r.ok) { toast(`🗑️ ${task.name} 已删除`, 'ok'); loadSubConfig(); }
      else toast(r.error || '删除失败', 'err');
    } catch { toast('删除请求失败', 'err'); }
    setDeletingTaskId(null);
  };

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>🌅 天下要闻</div>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>
            {dateStr && `${dateStr} | `}
            {morningBrief?.generated_at && `采集于 ${morningBrief.generated_at} | `}
            共 {totalNews} 条要闻 | {morningTasks.length}/{MAX_TASKS} 订阅
          </div>
        </div>
        <button className="btn btn-g" onClick={() => setShowSettings(!showSettings)} style={{ fontSize: 12, padding: '6px 14px' }}>
          ⚙ 设置
        </button>
      </div>

      {/* Settings Panel */}
      {showSettings && localConfig && (
        <SettingsPanel
          config={localConfig}
          setConfig={setLocalConfig}
          onSave={async () => {
            if (!localConfig) return;
            try {
              const r = await api.saveMorningConfig(localConfig);
              if (r.ok) { toast('配置已保存', 'ok'); loadSubConfig(); }
              else { toast(r.error || '保存失败', 'err'); }
            } catch { toast('服务器连接失败', 'err'); }
          }}
          onRefreshTasks={() => loadSubConfig()}
        />
      )}

      {/* Task Cards Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12, marginBottom: 20 }}>
        {morningTasks.map((task) => {
          const taskCats = task.categories || [];
          const hasPush = task.notification?.enabled && task.notification?.webhook;
          const taskNewsCount = taskCats.reduce((sum, cat) => {
            return sum + ((cats[cat] || []) as MorningNewsItem[]).length;
          }, 0);
          return (
            <div key={task.id} style={{
              background: 'var(--panel2)', borderRadius: 12, border: '1px solid var(--line)',
              padding: 16, transition: 'all .15s',
            }}>
              {/* Card Header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 22 }}>{task.emoji}</span>
                  <span style={{ fontSize: 15, fontWeight: 700 }}>{task.name}</span>
                </div>
                <span style={{
                  fontSize: 9, padding: '2px 8px', borderRadius: 999,
                  background: hasPush ? '#0d3322' : '#3d2200',
                  color: hasPush ? '#4caf88' : '#f0b429',
                  fontWeight: 600,
                }}>
                  {hasPush ? '🔔 已配置推送' : '⚠ 仅采集'}
                </span>
              </div>

              {/* Categories */}
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                {taskCats.map((cat) => {
                  const meta = CAT_META[cat] || { icon: '📰', color: 'var(--acc)' };
                  const cnt = ((cats[cat] || []) as MorningNewsItem[]).length;
                  return (
                    <span key={cat} style={{
                      fontSize: 10, padding: '2px 8px', borderRadius: 6,
                      background: `${meta.color}18`, color: meta.color, border: `1px solid ${meta.color}33`,
                    }}>
                      {meta.icon} {cat} {cnt > 0 ? `${cnt}条` : ''}
                    </span>
                  );
                })}
              </div>

              {/* Push info */}
              {hasPush ? (
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
                  → 推送到 {task.notification.channel === 'feishu' ? '飞书' : task.notification.channel === 'wecom' ? '企业微信' : task.notification.channel}
                </div>
              ) : (
                <div style={{ fontSize: 11, color: '#f0b429', marginBottom: 8 }}>
                  未配置推送，仅采集不推送
                </div>
              )}

              {/* Actions */}
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', borderTop: '1px solid var(--line)', paddingTop: 10 }}>
                <button className="btn btn-g" onClick={() => collectTask(task)}
                  disabled={collectingTaskId === task.id}
                  style={{ fontSize: 11, padding: '4px 10px', opacity: collectingTaskId === task.id ? 0.5 : 1 }}>
                  {collectingTaskId === task.id ? '⟳ 采集中…' : '▶ 采集'}
                </button>
                {hasPush && (
                  <button className="btn btn-g" onClick={() => testPush(task)}
                    disabled={testingTaskId === task.id}
                    style={{ fontSize: 11, padding: '4px 10px', opacity: testingTaskId === task.id ? 0.5 : 1 }}>
                    {testingTaskId === task.id ? '⟳ 发送中…' : '🔔 测试'}
                  </button>
                )}
                <button className="btn btn-g" onClick={() => loadPushHistory(task.id)}
                  style={{ fontSize: 11, padding: '4px 10px' }}>
                  📋 历史
                </button>
                <button onClick={() => deleteTask(task)}
                  disabled={deletingTaskId === task.id}
                  style={{ fontSize: 11, padding: '4px 10px', background: 'transparent', color: '#ff5270', border: '1px solid #ff527033', borderRadius: 6, cursor: 'pointer', opacity: deletingTaskId === task.id ? 0.5 : 1 }}>
                  ✕
                </button>
              </div>

              {/* Push History (collapsed) */}
              {historyTaskId === task.id && (
                <div style={{ marginTop: 10, maxHeight: 200, overflowY: 'auto', borderTop: '1px solid var(--line)', paddingTop: 8 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>推送历史</div>
                  {pushHistoryData.length === 0 ? (
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>暂无推送记录</div>
                  ) : pushHistoryData.map((h, i) => (
                    <div key={i} style={{ fontSize: 10, padding: '3px 0', display: 'flex', gap: 8, color: 'var(--muted)' }}>
                      <span>{h.status === 'success' ? '✅' : '❌'}</span>
                      <span>{h.channel}</span>
                      <span>{h.itemCount}条</span>
                      <span style={{ flex: 1 }}>{h.pushedAt?.slice(0, 16) || ''}</span>
                      {h.error && <span style={{ color: '#ff5270' }}>{h.error}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {/* Add New Card */}
        {morningTasks.length < MAX_TASKS && (
          <div onClick={() => { setShowSettings(true); }}
            style={{
              background: 'transparent', borderRadius: 12, border: '2px dashed var(--line)',
              padding: 24, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              cursor: 'pointer', color: 'var(--muted)', transition: 'all .15s',
              minHeight: 120,
            }}>
            <span style={{ fontSize: 24, marginBottom: 4 }}>＋</span>
            <span style={{ fontSize: 12 }}>新建订阅卡片</span>
            <span style={{ fontSize: 10, marginTop: 2 }}>在设置中添加</span>
          </div>
        )}
      </div>

      {/* News Content */}
      {!Object.keys(cats).length ? (
        <div className="mb-empty">暂无数据，在订阅卡片上点击「采集」获取今日简报</div>
      ) : (
        <div className="mb-cats">
          {Object.entries(cats).map(([cat, items]) => {
            if (!enabledSet.has(cat)) return null;
            const meta = CAT_META[cat] || { icon: '📰', color: 'var(--acc)', desc: cat };
            const scored = (items as MorningNewsItem[])
              .map((item) => {
                const text = ((item.title || '') + (item.summary || '')).toLowerCase();
                const kwHits = userKws.filter((k) => text.includes(k)).length;
                return { ...item, _kwHits: kwHits };
              })
              .sort((a, b) => b._kwHits - a._kwHits);
            return (
              <div className="mb-cat" key={cat}>
                <div className="mb-cat-hdr">
                  <span className="mb-cat-icon">{meta.icon}</span>
                  <span className="mb-cat-name" style={{ color: meta.color }}>{cat}</span>
                  <span className="mb-cat-cnt">{scored.length} 条</span>
                </div>
                <div className="mb-news-list">
                  {!scored.length ? (
                    <div className="mb-empty" style={{ padding: 16 }}>暂无新闻</div>
                  ) : (
                    scored.map((item, i) => {
                      const hasImg = !!(item.image && item.image.startsWith('http'));
                      return (
                        <div className="mb-card" key={i} onClick={() => window.open(item.link, '_blank')}>
                          <div className="mb-img">
                            {hasImg ? (
                              <img src={item.image} onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} loading="lazy" alt="" />
                            ) : (
                              <span>{meta.icon}</span>
                            )}
                          </div>
                          <div className="mb-info">
                            <div className="mb-headline">
                              {item.title}
                              {item._kwHits > 0 && (
                                <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 999, background: '#a07aff22', color: '#a07aff', border: '1px solid #a07aff44', marginLeft: 4 }}>
                                  ⭐ 关注
                                </span>
                              )}
                            </div>
                            <div className="mb-summary">{item.summary || item.desc || ''}</div>
                            <div className="mb-meta">
                              <span className="mb-source">📡 {item.source || ''}</span>
                              {item.pub_date && <span className="mb-time">{item.pub_date.substring(0, 16)}</span>}
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Settings Panel with 3 Tabs
// ═══════════════════════════════════════════════════════════

type SettingsTab = 'feeds' | 'subscribe' | 'other';

function SettingsPanel({
  config,
  setConfig,
  onSave,
  onRefreshTasks,
}: {
  config: SubConfig;
  setConfig: (c: SubConfig) => void;
  onSave: () => void;
  onRefreshTasks: () => void;
}) {
  const [activeTab, setActiveTab] = useState<SettingsTab>('feeds');
  const toast = useStore((s) => s.toast);

  return (
    <div style={{ marginBottom: 20, padding: 16, background: 'var(--panel2)', borderRadius: 12, border: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 700 }}>⚙ 设置</div>
      </div>

      {/* Tab Bar */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16, borderBottom: '1px solid var(--line)' }}>
        {[
          { key: 'feeds' as SettingsTab, label: '📡 信息源' },
          { key: 'subscribe' as SettingsTab, label: '📋 添加订阅' },
          { key: 'other' as SettingsTab, label: '⚙ 其他设置' },
        ].map((t) => (
          <div key={t.key} onClick={() => setActiveTab(t.key)}
            style={{
              padding: '6px 14px', cursor: 'pointer', fontSize: 12,
              fontWeight: activeTab === t.key ? 700 : 400,
              color: activeTab === t.key ? 'var(--text)' : 'var(--muted)',
              borderBottom: activeTab === t.key ? '2px solid var(--acc)' : '2px solid transparent',
              transition: 'all .15s',
            }}>
            {t.label}
          </div>
        ))}
      </div>

      {activeTab === 'feeds' && (
        <FeedsTab config={config} setConfig={setConfig} onSave={onSave} />
      )}
      {activeTab === 'subscribe' && (
        <SubscribeTab config={config} onSave={onSave} onRefreshTasks={onRefreshTasks} />
      )}
      {activeTab === 'other' && (
        <OtherTab config={config} setConfig={setConfig} onSave={onSave} />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 1: 信息源管理
// ═══════════════════════════════════════════════════════════

function FeedsTab({
  config,
  setConfig,
  onSave,
}: {
  config: SubConfig;
  setConfig: (c: SubConfig) => void;
  onSave: () => void;
}) {
  const toast = useStore((s) => s.toast);
  const [feedName, setFeedName] = useState('');
  const [feedUrl, setFeedUrl] = useState('');
  const [feedCat, setFeedCat] = useState('科技');
  const [feedCheckResults, setFeedCheckResults] = useState<Record<string, FeedCheckResult>>({});
  const [feedChecking, setFeedChecking] = useState(false);

  const allCats = [...DEFAULT_CATS];
  (config.categories || []).forEach((c) => {
    if (!allCats.includes(c.name)) allCats.push(c.name);
  });

  const checkFeeds = async () => {
    const urls = (config.feeds || []).map(f => f.url).filter(Boolean);
    if (!urls.length) return;
    setFeedChecking(true);
    try {
      const r = await api.checkFeeds(urls);
      if (r.ok && r.results) {
        const map: Record<string, FeedCheckResult> = {};
        r.results.forEach(res => { map[res.url] = res; });
        setFeedCheckResults(map);
        toast('✅ 检测完成', 'ok');
      }
    } catch { /* ignore */ }
    setFeedChecking(false);
  };

  const addFeed = () => {
    if (!feedName || !feedUrl) { toast('请填写源名称和URL', 'err'); return; }
    const feeds = [...(config.feeds || [])];
    feeds.push({ name: feedName, url: feedUrl, category: feedCat, protected: false });
    setConfig({ ...config, feeds });
    setFeedName('');
    setFeedUrl('');
    toast('源已添加，记得保存', 'ok');
  };

  const removeFeed = (i: number) => {
    const feeds = [...(config.feeds || [])];
    if (feeds[i]?.protected) { toast('受保护源不可删除', 'err'); return; }
    feeds.splice(i, 1);
    setConfig({ ...config, feeds });
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>📡 信息源管理</span>
        <button className="btn btn-g" onClick={checkFeeds} disabled={feedChecking}
          style={{ fontSize: 10, padding: '3px 10px', opacity: feedChecking ? 0.5 : 1 }}>
          {feedChecking ? '⟳ 检测中…' : '🔍 检测可用性'}
        </button>
      </div>

      {(config.feeds || []).map((f, i) => {
        const checkResult = feedCheckResults[f.url];
        const statusIcon = !checkResult ? '⚪' : checkResult.status === 'ok' ? '🟢' : '🔴';
        return (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4, fontSize: 11, padding: '4px 6px', background: 'var(--bg)', borderRadius: 6 }}>
            <span style={{ fontWeight: 600, minWidth: 60, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
            <span style={{ color: 'var(--muted)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 10 }}>{f.url}</span>
            <span style={{ color: 'var(--acc)', fontSize: 10, whiteSpace: 'nowrap' }}>{f.category}</span>
            <span title={checkResult?.status === 'ok' ? `可用 (${checkResult.itemCount} 条)` : checkResult?.error || '未检测'}>{statusIcon}</span>
            {f.protected ? (
              <span style={{ fontSize: 9, color: 'var(--muted)' }}>🔒</span>
            ) : (
              <span style={{ cursor: 'pointer', color: 'var(--danger)', fontSize: 10 }} onClick={() => removeFeed(i)}>✕</span>
            )}
          </div>
        );
      })}

      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <input placeholder="源名称" value={feedName} onChange={(e) => setFeedName(e.target.value)}
          style={{ width: 90, padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }} />
        <input placeholder="RSS URL" value={feedUrl} onChange={(e) => setFeedUrl(e.target.value)}
          style={{ flex: 1, padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }} />
        <select value={feedCat} onChange={(e) => setFeedCat(e.target.value)}
          style={{ padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }}>
          {allCats.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button className="btn btn-g" onClick={addFeed} style={{ fontSize: 11, padding: '4px 12px' }}>添加</button>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
        <button className="tpl-go" onClick={onSave} style={{ fontSize: 12, padding: '6px 16px' }}>💾 保存信息源</button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 2: 添加订阅
// ═══════════════════════════════════════════════════════════

function SubscribeTab({
  config,
  onSave,
  onRefreshTasks,
}: {
  config: SubConfig;
  onSave: () => void;
  onRefreshTasks: () => void;
}) {
  const toast = useStore((s) => s.toast);
  const morningTasks = useStore((s) => s.morningTasks);

  const [taskName, setTaskName] = useState('');
  const [taskEmoji, setTaskEmoji] = useState('📰');
  const [taskCats, setTaskCats] = useState<string[]>([]);
  const [taskFeedUrls, setTaskFeedUrls] = useState<string[]>([]);
  const [taskNotiEnabled, setTaskNotiEnabled] = useState(false);
  const [taskNotiChannel, setTaskNotiChannel] = useState('feishu');
  const [taskNotiWebhook, setTaskNotiWebhook] = useState('');
  const [channelList, setChannelList] = useState<ChannelInfo[]>([]);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // 已验证可用的 feeds
  const [verifiedFeeds, setVerifiedFeeds] = useState<FeedSource[]>([]);
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    api.notificationChannels().then(r => {
      if (r.channels) setChannelList(r.channels);
    }).catch(() => {});
  }, []);

  // 获取已验证的 feeds
  const loadVerifiedFeeds = async () => {
    const feeds = (config.feeds || []).filter(f => !f.protected || true);
    if (!feeds.length) return;
    setVerifying(true);
    try {
      const r = await api.checkFeeds(feeds.map(f => f.url));
      if (r.ok && r.results) {
        const okUrls = new Set(r.results.filter(res => res.status === 'ok').map(res => res.url));
        setVerifiedFeeds(feeds.filter(f => okUrls.has(f.url)));
        toast(`✅ ${okUrls.size}/${feeds.length} 个源可用`, 'ok');
      }
    } catch { /* ignore */ }
    setVerifying(false);
  };

  // 自动选择 emoji
  useEffect(() => {
    if (taskCats.length === 1) {
      const presets = EMOJI_PRESETS[taskCats[0]];
      if (presets && presets.length > 0) setTaskEmoji(presets[0].emoji);
    }
  }, [taskCats]);

  const toggleCat = (cat: string) => {
    setTaskCats(prev => prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]);
  };

  const toggleFeed = (url: string) => {
    setTaskFeedUrls(prev => prev.includes(url) ? prev.filter(u => u !== url) : [...prev, url]);
  };

  const handleSubmit = async () => {
    if (!taskName.trim()) { toast('请输入卡片名称', 'err'); return; }
    if (!taskCats.length) { toast('请选择至少一个分类', 'err'); return; }
    if (morningTasks.length >= MAX_TASKS) { toast(`最多 ${MAX_TASKS} 个订阅卡片`, 'err'); return; }
    if (taskNotiEnabled && !taskNotiWebhook.trim()) { toast('请输入 Webhook URL', 'err'); return; }

    setSubmitting(true);
    try {
      const r = await api.createMorningTask({
        name: taskName.trim(),
        emoji: taskEmoji,
        categories: taskCats,
        feedUrls: taskFeedUrls,
        notification: {
          enabled: taskNotiEnabled,
          channel: taskNotiChannel,
          webhook: taskNotiWebhook.trim(),
        },
      });
      if (r.ok) {
        toast(`✅ 订阅卡片「${taskEmoji} ${taskName}」已创建`, 'ok');
        setTaskName(''); setTaskEmoji('📰'); setTaskCats([]); setTaskFeedUrls([]);
        setTaskNotiEnabled(false); setTaskNotiChannel('feishu'); setTaskNotiWebhook('');
        onRefreshTasks();
      } else {
        toast(r.error || '创建失败', 'err');
      }
    } catch { toast('创建请求失败', 'err'); }
    setSubmitting(false);
  };

  const selectedChannel = channelList.find(ch => ch.id === taskNotiChannel) || channelList[0];

  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 10 }}>📋 创建订阅卡片</div>

      {/* Name + Emoji */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <div style={{ position: 'relative' }}>
          <span onClick={() => setShowEmojiPicker(!showEmojiPicker)}
            style={{ fontSize: 24, cursor: 'pointer', padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8 }}>
            {taskEmoji}
          </span>
          {showEmojiPicker && (
            <div style={{ position: 'absolute', top: '100%', left: 0, zIndex: 10, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: 8, display: 'flex', gap: 4, flexWrap: 'wrap', width: 200 }}>
              {taskCats.length === 1 && EMOJI_PRESETS[taskCats[0]]?.map(p => (
                <span key={p.emoji} onClick={() => { setTaskEmoji(p.emoji); setShowEmojiPicker(false); }}
                  style={{ fontSize: 18, cursor: 'pointer', padding: 2 }}>{p.emoji}</span>
              ))}
              {DEFAULT_EMOJIS.map(e => (
                <span key={e} onClick={() => { setTaskEmoji(e); setShowEmojiPicker(false); }}
                  style={{ fontSize: 18, cursor: 'pointer', padding: 2 }}>{e}</span>
              ))}
            </div>
          )}
        </div>
        <input type="text" value={taskName} onChange={(e) => setTaskName(e.target.value)}
          placeholder="卡片名称，如「经济要闻」"
          style={{ flex: 1, padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
      </div>

      {/* Categories */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>采集分类</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {DEFAULT_CATS.map((cat) => {
            const meta = CAT_META[cat] || { icon: '📰', color: 'var(--acc)' };
            const on = taskCats.includes(cat);
            return (
              <div key={cat} onClick={() => toggleCat(cat)}
                style={{
                  cursor: 'pointer', padding: '5px 10px', borderRadius: 8,
                  border: `1px solid ${on ? meta.color : 'var(--line)'}`,
                  background: on ? `${meta.color}18` : 'transparent',
                  display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
                }}>
                <span>{meta.icon}</span>
                <span>{cat}</span>
                {on && <span style={{ color: 'var(--ok)' }}>✓</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Feed Selection (only verified) */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 600 }}>📡 信息源（仅已验证可用）</span>
          <button className="btn btn-g" onClick={loadVerifiedFeeds} disabled={verifying}
            style={{ fontSize: 10, padding: '2px 8px', opacity: verifying ? 0.5 : 1 }}>
            {verifying ? '⟳ 验证中…' : '🔍 验证可用源'}
          </button>
        </div>
        {verifiedFeeds.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)', padding: '8px 0' }}>
            点击「验证可用源」检测哪些 RSS 可用
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {verifiedFeeds.map((f, i) => {
              const on = taskFeedUrls.includes(f.url);
              return (
                <div key={i} onClick={() => toggleFeed(f.url)}
                  style={{
                    cursor: 'pointer', padding: '3px 8px', borderRadius: 6, fontSize: 11,
                    border: `1px solid ${on ? 'var(--acc)' : 'var(--line)'}`,
                    background: on ? '#0d1f45' : 'transparent',
                    display: 'flex', alignItems: 'center', gap: 4,
                  }}>
                  <span>🟢</span>
                  <span>{f.name}</span>
                  {on && <span style={{ color: 'var(--ok)' }}>✓</span>}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Notification Config */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>📢 推送配置</div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6 }}>
          <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={taskNotiEnabled}
              onChange={(e) => setTaskNotiEnabled(e.target.checked)} />
            启用推送
          </label>
          {taskNotiEnabled && (
            <select value={taskNotiChannel}
              onChange={(e) => { setTaskNotiChannel(e.target.value); setTaskNotiWebhook(''); }}
              style={{ padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }}>
              {channelList.map(ch => (
                <option key={ch.id} value={ch.id}>{ch.icon} {ch.label}</option>
              ))}
            </select>
          )}
        </div>
        {taskNotiEnabled && (
          <input type="text" value={taskNotiWebhook}
            onChange={(e) => setTaskNotiWebhook(e.target.value)}
            placeholder={selectedChannel?.placeholder || 'Webhook URL'}
            style={{ width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 12, outline: 'none' }} />
        )}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="tpl-go" onClick={handleSubmit} disabled={submitting}
          style={{ fontSize: 12, padding: '6px 16px', opacity: submitting ? 0.5 : 1 }}>
          {submitting ? '⟳ 创建中…' : '💾 创建订阅'}
        </button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Tab 3: 其他设置
// ═══════════════════════════════════════════════════════════

function OtherTab({
  config,
  setConfig,
  onSave,
}: {
  config: SubConfig;
  setConfig: (c: SubConfig) => void;
  onSave: () => void;
}) {
  const [newKw, setNewKw] = useState('');

  const addKeyword = () => {
    if (!newKw.trim()) return;
    const kws = [...(config.keywords || [])];
    if (!kws.includes(newKw.trim())) kws.push(newKw.trim());
    setConfig({ ...config, keywords: kws });
    setNewKw('');
  };

  const removeKeyword = (i: number) => {
    const kws = [...(config.keywords || [])];
    kws.splice(i, 1);
    setConfig({ ...config, keywords: kws });
  };

  const toggleCat = (name: string) => {
    const cats = [...(config.categories || [])];
    const existing = cats.find((c) => c.name === name);
    if (existing) existing.enabled = !existing.enabled;
    else cats.push({ name, enabled: true });
    setConfig({ ...config, categories: cats });
  };

  const allCats = [...DEFAULT_CATS];
  (config.categories || []).forEach((c) => {
    if (!allCats.includes(c.name)) allCats.push(c.name);
  });
  const enabledSet = new Set((config.categories || []).filter((c) => c.enabled).map((c) => c.name));

  return (
    <div>
      {/* Category visibility */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>展示分类</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {allCats.map((cat) => {
            const meta = CAT_META[cat] || { icon: '📰', color: 'var(--acc)', desc: cat };
            const on = enabledSet.has(cat);
            return (
              <div key={cat} className={`sub-cat ${on ? 'active' : ''}`} onClick={() => toggleCat(cat)}
                style={{ cursor: 'pointer', padding: '6px 12px', borderRadius: 8, border: `1px solid ${on ? 'var(--acc)' : 'var(--line)'}`, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>{meta.icon}</span>
                <span style={{ fontSize: 12 }}>{cat}</span>
                {on && <span style={{ fontSize: 10, color: 'var(--ok)' }}>✓</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Keywords */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>关注关键词</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
          {(config.keywords || []).map((kw, i) => (
            <span key={i} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: 'var(--bg)', border: '1px solid var(--line)' }}>
              {kw}
              <span style={{ cursor: 'pointer', marginLeft: 4, color: 'var(--danger)' }} onClick={() => removeKeyword(i)}>✕</span>
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <input type="text" value={newKw} onChange={(e) => setNewKw(e.target.value)} placeholder="输入关键词"
            onKeyDown={(e) => { if (e.key === 'Enter') { addKeyword(); } }}
            style={{ flex: 1, padding: '6px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          <button className="btn btn-g" onClick={addKeyword} style={{ fontSize: 11, padding: '4px 12px' }}>添加</button>
        </div>
      </div>

      {/* Protected feeds info */}
      <div style={{ marginBottom: 14, padding: 10, background: 'var(--bg)', borderRadius: 8 }}>
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>🔒 受保护源（不可删除）</div>
        <div style={{ fontSize: 10, color: 'var(--muted)' }}>
          微博热搜 · 知乎日报 · 少数派
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="tpl-go" onClick={onSave} style={{ fontSize: 12, padding: '6px 16px' }}>💾 保存设置</button>
      </div>
    </div>
  );
}
