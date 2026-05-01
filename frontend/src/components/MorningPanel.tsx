import { useEffect, useState, useCallback, useRef } from 'react';
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

/** 获取近7天日期列表（从6天前到今天） */
function getLast7Days() {
  const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    const yyyymmdd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
    return {
      yyyymmdd,
      shortDate: `${d.getMonth() + 1}/${d.getDate()}`,
      weekday: `周${weekdays[d.getDay()]}`,
      label: `${d.getMonth() + 1}月${d.getDate()}日`,
    };
  });
}

/** 按卡片配置过滤+排序新闻 */
function getTaskNews(task: SubscriptionTask, allCats: Record<string, MorningNewsItem[]>): (MorningNewsItem & { _kwHits: number })[] {
  const taskCatSet = new Set(task.categories || []);
  const taskKws = (task.keywords || []).map(k => k.toLowerCase());
  const items: (MorningNewsItem & { _kwHits: number })[] = [];
  for (const [cat, catItems] of Object.entries(allCats)) {
    if (!taskCatSet.has(cat)) continue;
    for (const item of catItems) {
      if (!item.title) continue;
      const text = ((item.title || '') + (item.summary || '')).toLowerCase();
      const kwHits = taskKws.filter(k => text.includes(k)).length;
      items.push({ ...item, _kwHits: kwHits });
    }
  }
  // 关键词匹配的排前面
  items.sort((a, b) => b._kwHits - a._kwHits);
  return items;
}

export default function MorningPanel() {
  const morningBrief = useStore((s) => s.morningBrief);
  const morningBriefDate = useStore((s) => s.morningBriefDate);
  const morningBriefHistory = useStore((s) => s.morningBriefHistory);
  const subConfig = useStore((s) => s.subConfig);
  const morningTasks = useStore((s) => s.morningTasks);
  const loadMorning = useStore((s) => s.loadMorning);
  const loadSubConfig = useStore((s) => s.loadSubConfig);
  const loadMorningByDate = useStore((s) => s.loadMorningByDate);
  const toast = useStore((s) => s.toast);

  const [showSettings, setShowSettings] = useState(false);
  const [localConfig, setLocalConfig] = useState<SubConfig | null>(null);
  const [collectingTaskId, setCollectingTaskId] = useState<string | null>(null);
  const [testingTaskId, setTestingTaskId] = useState<string | null>(null);
  const [expandedHistory, setExpandedHistory] = useState<Set<string>>(new Set());
  const [pushHistoryMap, setPushHistoryMap] = useState<Record<string, PushHistoryItem[]>>({});
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const [editingTask, setEditingTask] = useState<SubscriptionTask | null>(null);
  const [collectingProgress, setCollectingProgress] = useState<Record<string, string>>({});
  const [expandedHistoryDay, setExpandedHistoryDay] = useState<Set<string>>(new Set());
  const [historyDayNewsMap, setHistoryDayNewsMap] = useState<Record<string, Record<string, MorningNewsItem[]>>>({});
  const [historyDayDates, setHistoryDayDates] = useState<Record<string, string[]>>({});
  const [deleteConfirm, setDeleteConfirm] = useState<SubscriptionTask | null>(null);
  const [selectedHistoryDate, setSelectedHistoryDate] = useState<Record<string, string>>({});

  useEffect(() => { loadMorning(); }, [loadMorning]);
  useEffect(() => {
    if (subConfig) setLocalConfig(JSON.parse(JSON.stringify(subConfig)));
  }, [subConfig]);

  const cats = morningBrief?.categories || {};
  const dateStr = morningBrief?.date
    ? morningBrief.date.replace(/(\d{4})(\d{2})(\d{2})/, '$1年$2月$3日')
    : '';
  const isToday = !morningBriefDate;

  // 单任务采集（带进度反馈）
  const collectTask = async (task: SubscriptionTask) => {
    setCollectingTaskId(task.id);
    setCollectingProgress(prev => ({ ...prev, [task.id]: '采集中…' }));
    try {
      const r = await api.collectTask(task.id);
      if (r.ok) {
        setCollectingProgress(prev => ({ ...prev, [task.id]: '采集完成，等待数据刷新' }));
        toast(`✅ ${task.emoji} ${task.name} 采集已触发`, 'ok');
        setTimeout(() => {
          loadMorning();
          setCollectingProgress(prev => {
            const next = { ...prev };
            delete next[task.id];
            return next;
          });
        }, 5000);
      } else {
        toast(r.error || '采集失败', 'err');
        setCollectingProgress(prev => {
          const next = { ...prev };
          delete next[task.id];
          return next;
        });
      }
    } catch {
      toast('采集请求失败', 'err');
      setCollectingProgress(prev => {
        const next = { ...prev };
        delete next[task.id];
        return next;
      });
    }
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

  // 切换推送历史展开
  const toggleHistory = async (taskId: string) => {
    setExpandedHistory(prev => {
      const next = new Set(prev);
      if (next.has(taskId)) { next.delete(taskId); return next; }
      next.add(taskId);
      return next;
    });
    // 懒加载历史数据
    if (!pushHistoryMap[taskId]) {
      try {
        const r = await api.pushHistory(taskId);
        if (r.ok) setPushHistoryMap(prev => ({ ...prev, [taskId]: r.history || [] }));
      } catch { /* ignore */ }
    }
  };

  // 删除任务（弹框确认）
  const deleteTask = async (task: SubscriptionTask) => {
    setDeletingTaskId(task.id);
    try {
      const r = await api.deleteMorningTask(task.id);
      if (r.ok) { toast(`🗑️ ${task.name} 已删除`, 'ok'); loadSubConfig(); }
      else toast(r.error || '删除失败', 'err');
    } catch { toast('删除请求失败', 'err'); }
    setDeletingTaskId(null);
    setDeleteConfirm(null);
  };

  // 加载卡片的历史日报
  const toggleHistoryDay = async (taskId: string) => {
    setExpandedHistoryDay(prev => {
      const next = new Set(prev);
      if (next.has(taskId)) { next.delete(taskId); return next; }
      next.add(taskId);
      return next;
    });
    if (!historyDayDates[taskId]) {
      try {
        const r = await api.morningBriefHistory();
        if (r.ok && r.dates) setHistoryDayDates(prev => ({ ...prev, [taskId]: r.dates || [] }));
      } catch { /* ignore */ }
    }
  };

  const loadHistoryDayNews = async (taskId: string, date: string) => {
    try {
      const brief = await api.morningBrief(date);
      const cats = brief?.categories || {};
      setHistoryDayNewsMap(prev => ({ ...prev, [`${taskId}_${date}`]: cats }));
    } catch { /* ignore */ }
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
            {morningTasks.length}/{MAX_TASKS} 订阅
          </div>
        </div>
        <button className="btn btn-g" onClick={() => setShowSettings(true)} style={{ fontSize: 12, padding: '6px 14px' }}>
          ⚙ 设置
        </button>
      </div>

      {/* Settings Modal - 修复6：设置改为弹框 */}
      {showSettings && localConfig && (
        <div className="modal-bg" onClick={() => setShowSettings(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}
            style={{ width: 640, maxWidth: '92vw', maxHeight: '80vh', display: 'flex', flexDirection: 'column' }}>
            {/* 弹框头部 */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 20px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontSize: 15, fontWeight: 700 }}>⚙ 天下要闻设置</span>
              <button style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 18, padding: 4 }} onClick={() => setShowSettings(false)}>✕</button>
            </div>
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
              onClose={() => setShowSettings(false)}
            />
          </div>
        </div>
      )}

      {/* 空状态引导 */}
      {morningTasks.length === 0 && !showSettings && (
        <div style={{
          textAlign: 'center', padding: 40, marginBottom: 20,
          background: 'var(--panel2)', borderRadius: 12, border: '1px solid var(--line)',
        }}>
          <div style={{ fontSize: 36, marginBottom: 12 }}>🌅</div>
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>欢迎使用天下要闻</div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 16, maxWidth: 400, margin: '0 auto 16px' }}>
            创建你的第一个订阅卡片，系统会每日自动采集新闻推送给你。
          </div>
          <button className="tpl-go" onClick={() => setShowSettings(true)} style={{ fontSize: 13, padding: '8px 20px' }}>
            📋 新建订阅卡片
          </button>
        </div>
      )}

      {/* 订阅卡片网格 */}
      {morningTasks.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12 }}>
          {morningTasks.map((task) => {
            const hasPush = task.notification?.enabled && task.notification?.webhook;
            const taskKws = task.keywords || [];
            const taskNews = getTaskNews(task, cats);
            const isExpanded = expandedHistory.has(task.id);
            const historyItems = pushHistoryMap[task.id] || [];
            const historyCount = historyItems.length;

            return (
              <div key={task.id} style={{
                background: 'var(--panel2)', borderRadius: 12, border: '1px solid var(--line)',
                display: 'flex', flexDirection: 'column', height: 460, overflow: 'hidden',
              }}>
                {/* ── 头部行 ── */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 14px 0' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 20 }}>{task.emoji}</span>
                    <span style={{ fontSize: 14, fontWeight: 700 }}>{task.name}</span>
                  </div>
                  <span style={{
                    fontSize: 9, padding: '2px 8px', borderRadius: 999,
                    background: hasPush ? '#0d3322' : '#3d2200',
                    color: hasPush ? '#4caf88' : '#f0b429',
                    fontWeight: 600, whiteSpace: 'nowrap',
                  }}>
                    {hasPush ? '🔔 已推送' : '⚠ 仅采集'}
                  </span>
                </div>

                {/* ── 标签行 ── */}
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', padding: '4px 14px 0' }}>
                  {(task.categories || []).map((cat) => {
                    const meta = CAT_META[cat] || { icon: '📰', color: 'var(--acc)' };
                    return (
                      <span key={cat} style={{
                        fontSize: 10, padding: '1px 6px', borderRadius: 4,
                        background: `${meta.color}18`, color: meta.color, border: `1px solid ${meta.color}33`,
                      }}>
                        {meta.icon} {cat}
                      </span>
                    );
                  })}
                  {taskKws.map((kw, i) => (
                    <span key={i} style={{
                      fontSize: 9, padding: '1px 5px', borderRadius: 4,
                      background: '#a07aff18', color: '#a07aff', border: '1px solid #a07aff33',
                    }}>
                      🔑{kw}
                    </span>
                  ))}
                </div>

                {/* ── 操作行 + 采集进度 ── */}
                <div style={{ display: 'flex', gap: 6, padding: '8px 14px', borderBottom: '1px solid var(--line)', alignItems: 'center', flexWrap: 'wrap' }}>
                  <button className="btn btn-g" onClick={() => collectTask(task)}
                    disabled={collectingTaskId === task.id}
                    style={{ fontSize: 11, padding: '3px 10px', opacity: collectingTaskId === task.id ? 0.5 : 1 }}>
                    {collectingTaskId === task.id ? '⟳ 采集中…' : '▶ 采集'}
                  </button>
                  {hasPush && (
                    <button className="btn btn-g" onClick={() => testPush(task)}
                      disabled={testingTaskId === task.id}
                      style={{ fontSize: 11, padding: '3px 10px', opacity: testingTaskId === task.id ? 0.5 : 1 }}>
                      {testingTaskId === task.id ? '⟳ 发送中…' : '🔔 测试'}
                    </button>
                  )}
                  <button className="btn btn-g" onClick={() => setEditingTask(task)}
                    style={{ fontSize: 11, padding: '3px 10px' }}>
                    ✏️ 编辑
                  </button>
                  {collectingProgress[task.id] && (
                    <span style={{ fontSize: 10, color: '#4caf88', background: '#0d3322', padding: '2px 8px', borderRadius: 4 }}>
                      {collectingProgress[task.id]}
                    </span>
                  )}
                </div>

                {/* ── 新闻列表区（固定高度，可滚动）── */}
                <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
                  {!taskNews.length ? (
                    <div style={{ padding: '20px 14px', textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
                      {Object.keys(cats).length > 0 ? '该分类暂无新闻' : '暂无数据，点击「采集」获取'}
                    </div>
                  ) : taskNews.map((item, i) => (
                    <div key={i} onClick={() => item.link && window.open(item.link, '_blank')}
                      title={item.title}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '5px 14px', cursor: item.link ? 'pointer' : 'default',
                        fontSize: 12, transition: 'background .1s',
                      }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'var(--bg)'; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
                    >
                      {item._kwHits > 0 && <span style={{ color: '#a07aff', fontSize: 10, flexShrink: 0 }}>⭐</span>}
                      <span style={{ color: 'var(--muted)', fontSize: 10, flexShrink: 0, minWidth: 36, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        📡{item.source || ''}
                      </span>
                      <span style={{
                        flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        color: 'var(--text)',
                      }}>
                        {item.title}
                      </span>
                      <span style={{ color: '#6a9eff', fontSize: 10, flexShrink: 0, opacity: 0.6 }}>↗</span>
                    </div>
                  ))}
                </div>

                {/* ── 推送历史（向下展开的内联面板）── */}
                <div style={{ borderTop: '1px solid var(--line)' }}>
                  <div onClick={() => toggleHistory(task.id)}
                    style={{ padding: '6px 14px', fontSize: 11, cursor: 'pointer', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>📋 推送历史{historyCount > 0 ? `(${historyCount})` : ''}</span>
                    <span style={{ transition: 'transform .15s', transform: isExpanded ? 'rotate(180deg)' : 'rotate(0)', fontSize: 12 }}>▼</span>
                  </div>
                  {isExpanded && (
                    <div style={{
                      background: 'var(--bg)', maxHeight: 150, overflowY: 'auto', padding: '6px 14px',
                      borderTop: '1px solid var(--line)',
                    }}>
                      {historyItems.length === 0 ? (
                        <div style={{ fontSize: 10, color: 'var(--muted)', paddingBottom: 4 }}>暂无推送记录</div>
                      ) : historyItems.slice(0, 10).map((h, i) => (
                        <div key={i} style={{ fontSize: 10, padding: '2px 0', display: 'flex', gap: 6, color: 'var(--muted)' }}>
                          <span>{h.status === 'success' ? '✅' : '❌'}</span>
                          <span>{h.channel}</span>
                          <span>{h.itemCount}条</span>
                          <span style={{ flex: 1 }}>{h.pushedAt?.slice(5, 16) || ''}</span>
                          {h.error && <span style={{ color: '#ff5270', maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.error}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* ── 历史日报（向下展开 + 近7天小格子）── 修复7 */}
                <div style={{ borderTop: '1px solid var(--line)' }}>
                  <div onClick={() => toggleHistoryDay(task.id)}
                    style={{ padding: '6px 14px', fontSize: 11, cursor: 'pointer', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>📅 历史日报</span>
                    <span style={{ transition: 'transform .15s', transform: expandedHistoryDay.has(task.id) ? 'rotate(180deg)' : 'rotate(0)', fontSize: 12 }}>▼</span>
                  </div>
                  {expandedHistoryDay.has(task.id) && (() => {
                    const historyDates = historyDayDates[task.id] || [];
                    const last7 = getLast7Days();
                    const selectedDate = selectedHistoryDate[task.id] || '';
                    const dayCats = selectedDate ? historyDayNewsMap[`${task.id}_${selectedDate}`] : null;
                    const dayNews = dayCats ? getTaskNews(task, dayCats) : null;
                    return (
                      <div style={{ background: 'var(--bg)', borderTop: '1px solid var(--line)', padding: '8px 14px' }}>
                        {/* 7天日期小格子 */}
                        <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
                          {last7.map(dateInfo => {
                            const hasData = historyDates.includes(dateInfo.yyyymmdd);
                            const isSelected = selectedDate === dateInfo.yyyymmdd;
                            return (
                              <div key={dateInfo.yyyymmdd} onClick={() => {
                                if (hasData) {
                                  setSelectedHistoryDate(prev => ({ ...prev, [task.id]: dateInfo.yyyymmdd }));
                                  loadHistoryDayNews(task.id, dateInfo.yyyymmdd);
                                }
                              }}
                                style={{
                                  flex: 1, textAlign: 'center', padding: '5px 0',
                                  borderRadius: 6, fontSize: 11, cursor: hasData ? 'pointer' : 'default',
                                  background: isSelected ? 'var(--acc)' : hasData ? 'var(--panel2)' : 'transparent',
                                  color: isSelected ? '#fff' : hasData ? 'var(--text)' : 'var(--muted)',
                                  border: `1px solid ${isSelected ? 'var(--acc)' : hasData ? 'var(--line)' : 'transparent'}`,
                                  opacity: hasData ? 1 : 0.4,
                                  transition: 'all .15s',
                                }}
                                title={hasData ? `查看${dateInfo.label}早报` : `${dateInfo.label}无数据`}
                              >
                                <div style={{ fontSize: 9, color: isSelected ? '#ffffffaa' : 'var(--muted)' }}>
                                  {dateInfo.weekday}
                                </div>
                                <div style={{ fontWeight: isSelected ? 700 : 500 }}>
                                  {dateInfo.shortDate}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                        {/* 选中日期的新闻内容 */}
                        {selectedDate && dayNews && dayNews.length > 0 && (
                          <div style={{ maxHeight: 120, overflowY: 'auto' }}>
                            {dayNews.slice(0, 5).map((item, i) => (
                              <div key={i} onClick={() => item.link && window.open(item.link, '_blank')}
                                style={{ fontSize: 10, padding: '2px 0', display: 'flex', gap: 4, color: 'var(--muted)', cursor: item.link ? 'pointer' : 'default' }}>
                                {item._kwHits > 0 && <span style={{ color: '#a07aff', flexShrink: 0 }}>⭐</span>}
                                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{item.title}</span>
                              </div>
                            ))}
                          </div>
                        )}
                        {selectedDate && dayNews && dayNews.length === 0 && (
                          <div style={{ fontSize: 10, color: 'var(--muted)', textAlign: 'center', padding: 4 }}>该日无相关新闻</div>
                        )}
                        {!selectedDate && (
                          <div style={{ fontSize: 10, color: 'var(--muted)', textAlign: 'center', padding: 4 }}>点击上方日期查看早报</div>
                        )}
                      </div>
                    );
                  })()}
                </div>

                {/* ── 底部删除 - 修复11：改为触发弹框 ── */}
                <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '4px 14px 8px' }}>
                  <button onClick={() => setDeleteConfirm(task)}
                    disabled={deletingTaskId === task.id}
                    style={{ fontSize: 10, padding: '2px 8px', background: 'transparent', color: '#ff527088', border: '1px solid #ff527022', borderRadius: 4, cursor: 'pointer', opacity: deletingTaskId === task.id ? 0.5 : 1 }}>
                    ✕ 删除
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 删除确认弹框 - 修复11：使用项目一致弹框 */}
      {deleteConfirm && (
        <div className="confirm-bg" onClick={() => setDeleteConfirm(null)}>
          <div className="confirm-box" onClick={(e) => e.stopPropagation()}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>🗑️ 确认删除</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
              确定要删除订阅卡片「{deleteConfirm.emoji} {deleteConfirm.name}」吗？此操作不可撤销。
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn btn-g" onClick={() => setDeleteConfirm(null)}>取消</button>
              <button className="btn" style={{ background: 'var(--danger)', color: '#fff' }}
                onClick={() => deleteTask(deleteConfirm)}>
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Task Modal */}
      {editingTask && (
        <EditTaskModal
          task={editingTask}
          onClose={() => setEditingTask(null)}
          onSaved={() => { setEditingTask(null); loadSubConfig(); }}
        />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Settings Panel with 2 Tabs (信息源管理 | 添加订阅)
// ═══════════════════════════════════════════════════════════

type SettingsTab = 'feeds' | 'subscribe';

function SettingsPanel({
  config,
  setConfig,
  onSave,
  onRefreshTasks,
  onClose,
}: {
  config: SubConfig;
  setConfig: (c: SubConfig) => void;
  onSave: () => void;
  onRefreshTasks: () => void;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<SettingsTab>('subscribe');
  const toast = useStore((s) => s.toast);

  return (
    <>
      {/* Tab Bar */}
      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--line)', padding: '0 20px' }}>
        {[
          { key: 'feeds' as SettingsTab, label: '📡 信息源' },
          { key: 'subscribe' as SettingsTab, label: '📋 添加订阅' },
        ].map((t) => (
          <div key={t.key} onClick={() => setActiveTab(t.key)}
            style={{
              padding: '10px 16px', cursor: 'pointer', fontSize: 12, fontWeight: 600,
              color: activeTab === t.key ? 'var(--acc)' : 'var(--muted)',
              borderBottom: activeTab === t.key ? '2px solid var(--acc)' : '2px solid transparent',
              transition: 'all .15s',
            }}>
            {t.label}
          </div>
        ))}
      </div>

      {/* Tab Content */}
      <div style={{ padding: 20, overflowY: 'auto', maxHeight: 'calc(80vh - 120px)' }}>
        {activeTab === 'feeds' && (
          <FeedsTab config={config} setConfig={setConfig} onSave={onSave} />
        )}
        {activeTab === 'subscribe' && (
          <SubscribeTab config={config} onSave={onSave} onRefreshTasks={onRefreshTasks} />
        )}
      </div>
    </>
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
  const [checkingSingleUrl, setCheckingSingleUrl] = useState<string | null>(null);
  const [checkedCount, setCheckedCount] = useState(0);

  const allCats = [...DEFAULT_CATS];
  (config.categories || []).forEach((c) => {
    if (!allCats.includes(c.name)) allCats.push(c.name);
  });

  // 修复5：逐个检测+进度反馈；修复3：移除自动检测
  const checkFeeds = useCallback(async () => {
    const urls = (config.feeds || []).map(f => f.url).filter(Boolean);
    if (!urls.length) return;
    setFeedChecking(true);
    setCheckedCount(0);
    let okCount = 0;
    let done = 0;
    for (const url of urls) {
      try {
        const r = await api.checkFeeds([url]);
        if (r.ok && r.results?.[0]) {
          setFeedCheckResults(prev => ({ ...prev, [url]: r.results![0] }));
          if (r.results[0].status === 'ok') okCount++;
        }
      } catch { /* 单个失败不影响后续 */ }
      done++;
      setCheckedCount(done);
    }
    toast(`✅ 检测完成：${okCount}/${urls.length} 个源可用`, 'ok');
    setFeedChecking(false);
  }, [config.feeds, toast]);

  const checkSingleFeed = async (url: string) => {
    setCheckingSingleUrl(url);
    try {
      const r = await api.checkFeeds([url]);
      if (r.ok && r.results && r.results[0]) {
        setFeedCheckResults(prev => ({ ...prev, [url]: r.results![0] }));
      }
    } catch { /* ignore */ }
    setCheckingSingleUrl(null);
  };

  const addFeedWithCheck = async () => {
    if (!feedName || !feedUrl) { toast('请填写源名称和URL', 'err'); return; }
    try { new URL(feedUrl); } catch { toast('请输入有效的URL（以http://或https://开头）', 'err'); return; }
    if ((config.feeds || []).some(f => f.url === feedUrl)) { toast('该信息源已存在', 'err'); return; }
    try {
      const r = await api.checkFeeds([feedUrl]);
      if (r.ok && r.results && r.results[0]) {
        const result = r.results[0];
        setFeedCheckResults(prev => ({ ...prev, [feedUrl]: result }));
        if (result.status !== 'ok') {
          toast(`⚠️ 该RSS源不可用：${result.error || '未知错误'}，仍可添加但可能无法采集`, 'err');
        } else {
          toast(`✅ RSS源可用（${result.itemCount}条内容）`, 'ok');
        }
      }
    } catch { /* 验证失败仍允许添加 */ }
    const feeds = [...(config.feeds || [])];
    feeds.push({ name: feedName, url: feedUrl, category: feedCat });
    setConfig({ ...config, feeds });
    setFeedName('');
    setFeedUrl('');
  };

  const removeFeed = (i: number) => {
    const feeds = [...(config.feeds || [])];
    const removed = feeds[i];
    feeds.splice(i, 1);
    setConfig({ ...config, feeds });
    if (removed?.url) {
      setFeedCheckResults(prev => {
        const next = { ...prev };
        delete next[removed.url];
        return next;
      });
    }
    toast('源已移除，记得保存', 'ok');
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>📡 信息源管理 ({(config.feeds || []).length}个)</span>
        <button className="btn btn-g" onClick={checkFeeds} disabled={feedChecking}
          style={{ fontSize: 11, padding: '4px 12px', opacity: feedChecking ? 0.5 : 1 }}>
          {feedChecking ? `⟳ 检测中(${checkedCount}/${(config.feeds || []).length})` : '🔍 全部检测'}
        </button>
      </div>

      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8, padding: '6px 8px', background: 'var(--bg)', borderRadius: 6 }}>
        💡 RSS 源即新闻数据地址。添加后系统自动从该地址采集新闻。可在 rss.sh memo.app 等网站搜索更多 RSS 源。
      </div>

      {(config.feeds || []).map((f, i) => {
        const checkResult = feedCheckResults[f.url];
        const isChecking = checkingSingleUrl === f.url;
        let statusIcon = '⚪';
        let statusText = '未检测';
        let statusColor = 'var(--muted)';
        if (isChecking) {
          statusIcon = '⏳'; statusText = '检测中'; statusColor = '#f0b429';
        } else if (checkResult) {
          if (checkResult.status === 'ok') {
            statusIcon = '🟢';
            statusText = `可用(${checkResult.itemCount || 0}条${checkResult.latency_ms ? `, ${checkResult.latency_ms}ms` : ''})`;
            statusColor = '#4caf88';
          } else {
            statusIcon = '🔴';
            statusText = `不可用${checkResult.error ? `: ${checkResult.error.slice(0, 30)}` : ''}`;
            statusColor = '#ff5270';
          }
        }
        return (
          <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4, fontSize: 11, padding: '5px 8px', background: 'var(--bg)', borderRadius: 6 }}>
            <span style={{ fontWeight: 600, minWidth: 55, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
            <span style={{ color: 'var(--acc)', fontSize: 10, whiteSpace: 'nowrap', padding: '1px 4px', background: `${CAT_META[f.category]?.color || 'var(--acc)'}18`, borderRadius: 3 }}>{f.category}</span>
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 9, color: 'var(--muted)' }}>{f.url}</span>
            <span style={{ fontSize: 9, color: statusColor, whiteSpace: 'nowrap', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis' }} title={statusText}>
              {statusIcon} {statusText}
            </span>
            {/* 修复4：单源检测按钮明显化 */}
            <button
              onClick={() => checkSingleFeed(f.url)}
              disabled={isChecking}
              title="检测此信息源可用性"
              style={{
                fontSize: 10, padding: '2px 8px', borderRadius: 5,
                background: 'transparent', border: '1px solid var(--line)',
                color: 'var(--muted)', cursor: 'pointer',
                transition: 'border-color .15s, color .15s',
                display: 'flex', alignItems: 'center', gap: 3,
                opacity: isChecking ? 0.5 : 1,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--acc)'; e.currentTarget.style.color = 'var(--acc)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--line)'; e.currentTarget.style.color = 'var(--muted)'; }}
            >
              {isChecking ? '⟳' : '🔍'} 检测
            </button>
            <span style={{ cursor: 'pointer', color: '#ff5270', fontSize: 10 }} onClick={() => removeFeed(i)} title="删除此源">✕</span>
          </div>
        );
      })}

      {(config.feeds || []).length === 0 && (
        <div style={{ fontSize: 11, color: 'var(--muted)', padding: '12px 0', textAlign: 'center' }}>
          暂无信息源，请在下方添加
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <input placeholder="源名称" value={feedName} onChange={(e) => setFeedName(e.target.value)}
          style={{ width: 90, padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }} />
        <input placeholder="RSS URL" value={feedUrl} onChange={(e) => setFeedUrl(e.target.value)}
          style={{ flex: 1, padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }} />
        <select value={feedCat} onChange={(e) => setFeedCat(e.target.value)}
          style={{ padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }}>
          {allCats.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button className="btn btn-g" onClick={addFeedWithCheck} style={{ fontSize: 11, padding: '4px 12px' }}>验证并添加</button>
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
  const [taskKeywords, setTaskKeywords] = useState<string[]>([]);
  const [newKeyword, setNewKeyword] = useState('');
  const [taskNotiEnabled, setTaskNotiEnabled] = useState(false);
  const [taskNotiChannel, setTaskNotiChannel] = useState('feishu');
  const [taskNotiWebhook, setTaskNotiWebhook] = useState('');
  const [channelList, setChannelList] = useState<ChannelInfo[]>([]);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [feedStatusCache, setFeedStatusCache] = useState<Record<string, FeedCheckResult>>({});
  const [feedVerifying, setFeedVerifying] = useState(false);
  const [showFeeds, setShowFeeds] = useState(false);

  // 修复12：SubscribeTab 重复验证优化 - 防抖+只在数量变化时触发
  const feedsCountRef = useRef((config.feeds || []).length);
  useEffect(() => {
    const feedCount = (config.feeds || []).length;
    if (feedCount === 0) return;
    // 只在 feeds 数量变化时才重新验证，已有缓存时不重复请求
    if (feedCount === feedsCountRef.current && Object.keys(feedStatusCache).length > 0) return;
    feedsCountRef.current = feedCount;

    const timer = setTimeout(() => {
      const feeds = config.feeds || [];
      const urls = feeds.map(f => f.url).filter(Boolean);
      if (urls.length > 0) {
        setFeedVerifying(true);
        api.checkFeeds(urls).then(r => {
          if (r.ok && r.results) {
            const map: Record<string, FeedCheckResult> = {};
            r.results.forEach(res => { map[res.url] = res; });
            setFeedStatusCache(map);
          }
        }).catch(() => {}).finally(() => setFeedVerifying(false));
      }
    }, 800);
    return () => clearTimeout(timer);
  }, [(config.feeds || []).length]);

  useEffect(() => {
    api.notificationChannels().then(r => {
      if (r.channels) setChannelList(r.channels);
    }).catch(() => {});
  }, []);

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

  const addKeyword = () => {
    if (!newKeyword.trim()) return;
    if (!taskKeywords.includes(newKeyword.trim())) {
      setTaskKeywords([...taskKeywords, newKeyword.trim()]);
    }
    setNewKeyword('');
  };

  const removeKeyword = (i: number) => {
    setTaskKeywords(taskKeywords.filter((_, idx) => idx !== i));
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
        keywords: taskKeywords,
        notification: {
          enabled: taskNotiEnabled,
          channel: taskNotiChannel,
          webhook: taskNotiWebhook.trim(),
        },
      });
      if (r.ok) {
        toast(`✅ 订阅卡片「${taskEmoji} ${taskName}」已创建`, 'ok');
        setTaskName(''); setTaskEmoji('📰'); setTaskCats([]); setTaskFeedUrls([]);
        setTaskKeywords([]); setNewKeyword('');
        setTaskNotiEnabled(false); setTaskNotiChannel('feishu'); setTaskNotiWebhook('');
        onRefreshTasks();
      } else {
        toast(r.error || '创建失败', 'err');
      }
    } catch { toast('创建请求失败', 'err'); }
    setSubmitting(false);
  };

  const selectedChannel = channelList.find(ch => ch.id === taskNotiChannel) || channelList[0];

  const feedsByCategory: Record<string, FeedSource[]> = {};
  (config.feeds || []).forEach(f => {
    const cat = f.category || '未分类';
    if (!feedsByCategory[cat]) feedsByCategory[cat] = [];
    feedsByCategory[cat].push(f);
  });

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

      {/* Categories - 修复10：无源分类提醒 */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>采集分类（必选）</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {DEFAULT_CATS.map((cat) => {
            const meta = CAT_META[cat] || { icon: '📰', color: 'var(--acc)' };
            const on = taskCats.includes(cat);
            const catFeeds = (config.feeds || []).filter(f => f.category === cat);
            const availableCount = catFeeds.filter(f => {
              const s = feedStatusCache[f.url];
              return s?.status === 'ok';
            }).length;
            const noSource = catFeeds.length === 0 || availableCount === 0;
            return (
              <div key={cat} onClick={() => {
                if (!on && noSource) {
                  toast(`⚠️ 分类「${cat}」当前无可用RSS源，采集可能无法获取新闻`, 'err');
                }
                toggleCat(cat);
              }}
                style={{
                  cursor: 'pointer', padding: '5px 10px', borderRadius: 8,
                  border: `1px solid ${on ? meta.color : noSource ? '#ff527044' : 'var(--line)'}`,
                  background: on ? `${meta.color}18` : 'transparent',
                  display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
                  opacity: noSource && !on ? 0.6 : 1,
                }}>
                <span>{meta.icon}</span>
                <span>{cat}</span>
                {availableCount > 0
                  ? <span style={{ fontSize: 9, color: 'var(--ok)' }}>{availableCount}源</span>
                  : <span style={{ fontSize: 9, color: 'var(--danger)' }}>无源</span>
                }
                {on && <span style={{ color: 'var(--ok)' }}>✓</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Feed Selection - 修复9：信息源按钮明显化+▼符号 */}
      <div style={{ marginBottom: 12 }}>
        <div onClick={() => setShowFeeds(!showFeeds)}
          style={{
            fontSize: 12, fontWeight: 600, cursor: 'pointer',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '8px 12px', marginTop: 4,
            background: 'var(--panel2)',
            border: '1px solid var(--line)',
            borderRadius: 8,
            transition: 'border-color .15s, background .15s',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--acc)'; e.currentTarget.style.background = '#0d1f45'; }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--line)'; e.currentTarget.style.background = 'var(--panel2)'; }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span>📡</span>
            <span>信息源</span>
            {taskFeedUrls.length > 0
              ? <span style={{ fontSize: 10, background: 'var(--acc)', color: '#fff', borderRadius: 4, padding: '1px 6px' }}>已选{taskFeedUrls.length}个</span>
              : <span style={{ fontSize: 10, color: 'var(--muted)' }}>不选择则使用分类下所有源</span>
            }
          </span>
          <span style={{
            transition: 'transform .2s',
            transform: showFeeds ? 'rotate(180deg)' : 'rotate(0)',
            fontSize: 14, color: 'var(--acc)', fontWeight: 700,
          }}>
            ▼
          </span>
        </div>
        {showFeeds && (
          <div style={{ marginTop: 6 }}>
            {(config.feeds || []).length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--muted)', padding: '8px 0' }}>
                暂无信息源，请先在「信息源」标签页添加
              </div>
            ) : feedVerifying ? (
              <div style={{ fontSize: 11, color: 'var(--muted)', padding: '8px 0' }}>⏳ 验证中…</div>
            ) : (
              Object.entries(feedsByCategory).map(([cat, feeds]) => (
                <div key={cat} style={{ marginBottom: 6 }}>
                  <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3 }}>{CAT_META[cat]?.icon || '📰'} {cat}</div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {feeds.map((f, i) => {
                      const on = taskFeedUrls.includes(f.url);
                      const status = feedStatusCache[f.url];
                      const isOk = status?.status === 'ok';
                      const isErr = status?.status === 'error';
                      return (
                        <div key={i} onClick={() => toggleFeed(f.url)}
                          style={{
                            cursor: 'pointer', padding: '3px 8px', borderRadius: 6, fontSize: 11,
                            border: `1px solid ${on ? 'var(--acc)' : isErr ? '#ff527044' : 'var(--line)'}`,
                            background: on ? '#0d1f45' : 'transparent',
                            display: 'flex', alignItems: 'center', gap: 3,
                            opacity: isErr && !on ? 0.6 : 1,
                          }}>
                          <span>{isOk ? '🟢' : isErr ? '🔴' : '⚪'}</span>
                          <span>{f.name}</span>
                          {on && <span style={{ color: 'var(--ok)' }}>✓</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Keywords */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>🔑 关键词过滤（可选）</div>
        <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6 }}>
          不输入关键词则按分类采集全部新闻，输入关键词仅采集包含关键词的新闻
        </div>
        {taskKeywords.length > 0 && (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
            {taskKeywords.map((kw, i) => (
              <span key={i} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: '#a07aff18', color: '#a07aff', border: '1px solid #a07aff33' }}>
                {kw}
                <span style={{ cursor: 'pointer', marginLeft: 4, color: '#ff5270' }} onClick={() => removeKeyword(i)}>✕</span>
              </span>
            ))}
          </div>
        )}
        <div style={{ display: 'flex', gap: 6 }}>
          <input type="text" value={newKeyword} onChange={(e) => setNewKeyword(e.target.value)} placeholder="输入关键词后回车"
            onKeyDown={(e) => { if (e.key === 'Enter') { addKeyword(); } }}
            style={{ flex: 1, padding: '5px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }} />
          <button className="btn btn-g" onClick={addKeyword} style={{ fontSize: 11, padding: '4px 10px' }}>添加</button>
        </div>
      </div>

      {/* Notification Config */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>📢 推送配置（可选）</div>
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
// Edit Task Modal
// ═══════════════════════════════════════════════════════════

function EditTaskModal({
  task,
  onClose,
  onSaved,
}: {
  task: SubscriptionTask;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useStore((s) => s.toast);
  const config = useStore((s) => s.subConfig);

  const [name, setName] = useState(task.name);
  const [emoji, setEmoji] = useState(task.emoji);
  const [cats, setCats] = useState<string[]>(task.categories || []);
  const [keywords, setKeywords] = useState<string[]>(task.keywords || []);
  const [newKw, setNewKw] = useState('');
  const [notiEnabled, setNotiEnabled] = useState(task.notification?.enabled || false);
  const [notiChannel, setNotiChannel] = useState(task.notification?.channel || 'feishu');
  const [notiWebhook, setNotiWebhook] = useState(task.notification?.webhook || '');
  const [channelList, setChannelList] = useState<ChannelInfo[]>([]);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.notificationChannels().then(r => {
      if (r.channels) setChannelList(r.channels);
    }).catch(() => {});
  }, []);

  const toggleCat = (cat: string) => {
    setCats(prev => prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]);
  };

  const addKw = () => {
    if (!newKw.trim()) return;
    if (!keywords.includes(newKw.trim())) setKeywords([...keywords, newKw.trim()]);
    setNewKw('');
  };

  const removeKw = (i: number) => {
    setKeywords(keywords.filter((_, idx) => idx !== i));
  };

  const handleSave = async () => {
    if (!name.trim()) { toast('卡片名称必填', 'err'); return; }
    if (!cats.length) { toast('至少选择一个分类', 'err'); return; }
    setSaving(true);
    try {
      const r = await api.updateMorningTask(task.id, {
        name: name.trim(),
        emoji,
        categories: cats,
        keywords,
        notification: { enabled: notiEnabled, channel: notiChannel, webhook: notiWebhook.trim() },
      });
      if (r.ok) { toast('✅ 已保存', 'ok'); onSaved(); }
      else toast(r.error || '保存失败', 'err');
    } catch { toast('保存请求失败', 'err'); }
    setSaving(false);
  };

  const selectedChannel = channelList.find(ch => ch.id === notiChannel) || channelList[0];

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{ background: 'var(--panel)', borderRadius: 12, padding: 20, width: 420, maxWidth: '90vw', maxHeight: '80vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <span style={{ fontSize: 15, fontWeight: 700 }}>✏️ 编辑订阅</span>
          <span style={{ cursor: 'pointer', color: 'var(--muted)' }} onClick={onClose}>✕</span>
        </div>

        {/* Name + Emoji */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
          <div style={{ position: 'relative' }}>
            <span onClick={() => setShowEmojiPicker(!showEmojiPicker)}
              style={{ fontSize: 22, cursor: 'pointer', padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8 }}>
              {emoji}
            </span>
            {showEmojiPicker && (
              <div style={{ position: 'absolute', top: '100%', left: 0, zIndex: 10, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, padding: 8, display: 'flex', gap: 4, flexWrap: 'wrap', width: 200 }}>
                {cats.length === 1 && EMOJI_PRESETS[cats[0]]?.map(p => (
                  <span key={p.emoji} onClick={() => { setEmoji(p.emoji); setShowEmojiPicker(false); }}
                    style={{ fontSize: 18, cursor: 'pointer', padding: 2 }}>{p.emoji}</span>
                ))}
                {DEFAULT_EMOJIS.map(e => (
                  <span key={e} onClick={() => { setEmoji(e); setShowEmojiPicker(false); }}
                    style={{ fontSize: 18, cursor: 'pointer', padding: 2 }}>{e}</span>
                ))}
              </div>
            )}
          </div>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)}
            style={{ flex: 1, padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
        </div>

        {/* Categories */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>分类</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {DEFAULT_CATS.map(cat => {
              const meta = CAT_META[cat] || { icon: '📰', color: 'var(--acc)' };
              const on = cats.includes(cat);
              return (
                <div key={cat} onClick={() => toggleCat(cat)}
                  style={{
                    cursor: 'pointer', padding: '4px 8px', borderRadius: 6, fontSize: 11,
                    border: `1px solid ${on ? meta.color : 'var(--line)'}`,
                    background: on ? `${meta.color}18` : 'transparent',
                    display: 'flex', alignItems: 'center', gap: 3,
                  }}>
                  <span>{meta.icon}</span><span>{cat}</span>
                  {on && <span style={{ color: 'var(--ok)' }}>✓</span>}
                </div>
              );
            })}
          </div>
        </div>

        {/* Keywords */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>关键词</div>
          {keywords.length > 0 && (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
              {keywords.map((kw, i) => (
                <span key={i} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: '#a07aff18', color: '#a07aff', border: '1px solid #a07aff33' }}>
                  {kw}
                  <span style={{ cursor: 'pointer', marginLeft: 4, color: '#ff5270' }} onClick={() => removeKw(i)}>✕</span>
                </span>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 6 }}>
            <input type="text" value={newKw} onChange={(e) => setNewKw(e.target.value)} placeholder="输入关键词后回车"
              onKeyDown={(e) => { if (e.key === 'Enter') addKw(); }}
              style={{ flex: 1, padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }} />
            <button className="btn btn-g" onClick={addKw} style={{ fontSize: 11, padding: '4px 10px' }}>添加</button>
          </div>
        </div>

        {/* Notification */}
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>推送配置</div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6 }}>
            <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="checkbox" checked={notiEnabled} onChange={(e) => setNotiEnabled(e.target.checked)} />
              启用推送
            </label>
            {notiEnabled && (
              <select value={notiChannel}
                onChange={(e) => { setNotiChannel(e.target.value); setNotiWebhook(''); }}
                style={{ padding: '5px 8px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 11, outline: 'none' }}>
                {channelList.map(ch => (
                  <option key={ch.id} value={ch.id}>{ch.icon} {ch.label}</option>
                ))}
              </select>
            )}
          </div>
          {notiEnabled && (
            <input type="text" value={notiWebhook} onChange={(e) => setNotiWebhook(e.target.value)}
              placeholder={selectedChannel?.placeholder || 'Webhook URL'}
              style={{ width: '100%', padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 12, outline: 'none' }} />
          )}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn btn-g" onClick={onClose} style={{ fontSize: 12, padding: '6px 14px' }}>取消</button>
          <button className="tpl-go" onClick={handleSave} disabled={saving}
            style={{ fontSize: 12, padding: '6px 16px', opacity: saving ? 0.5 : 1 }}>
            {saving ? '⟳ 保存中…' : '💾 保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
