/**
 * AuditPanelEnhanced.tsx — 流程监察面板（重构版）
 *
 * 重构要点：
 * 1. 采用主 Tab 布局，将原来 6 个纵向板块精简为 3 个核心视图 + 1 个折叠说明
 * 2. "违规 & 通报" 合并为一个 Tab 内的子Tab切换，消除信息重叠
 * 3. 全面使用系统自带的 CSS class（.tab/.kpi/.chip/.btn 等），不再依赖 inline style
 * 4. 所有功能保持不变：任务卡片墙、违规记录、通报记录、归档回溯、详情滑出面板
 *
 * 接入方式不变：
 *   import AuditPanel from './components/AuditPanelEnhanced';
 */
import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useStore, timeAgo } from '../store';
import { api, type Task, type AuditViolation, type AuditNotification, type WatchedTask, type TaskActivityData } from '../api';

/* ═══════════════════════════════════════════════════════════════════════
   常量 & 样式（保持原有映射不变）
   ═══════════════════════════════════════════════════════════════════════ */

const DEPT_COLOR: Record<string, string> = {
  '皇上': '#ffd700', '太子': '#ff6b6b',
  '中书省': '#6a9eff', '中书': '#6a9eff',
  '门下省': '#a07aff', '门下': '#a07aff',
  '尚书省': '#2ecc8a', '尚书': '#2ecc8a',
  '礼部': '#ff9f43', '户部': '#54a0ff',
  '兵部': '#ee5a24', '刑部': '#ff5270',
  '工部': '#78e08f', '吏部': '#f8a5c2', '吏部_hr': '#f8a5c2',
  '太子调度': '#888888',
};

const STATE_S: Record<string, { bg: string; color: string; label: string }> = {
  Pending:  { bg: '#88888822', color: '#888888', label: '待处理' },
  Taizi:    { bg: '#ff6b6b22', color: '#ff6b6b', label: '太子分拣' },
  Zhongshu: { bg: '#6a9eff22', color: '#6a9eff', label: '中书起草' },
  Menxia:   { bg: '#a07aff22', color: '#a07aff', label: '门下审议' },
  Assigned: { bg: '#2ecc8a22', color: '#2ecc8a', label: '尚书派发' },
  Doing:    { bg: '#ff9f4322', color: '#ff9f43', label: '执行中' },
  Review:   { bg: '#54a0ff22', color: '#54a0ff', label: '汇总审查' },
  Next:     { bg: '#78e08f22', color: '#78e08f', label: '待执行' },
  Blocked:  { bg: '#ff527022', color: '#ff5270', label: '阻塞' },
  Done:     { bg: '#2ecc8a22', color: '#2ecc8a', label: '完成' },
  Cancelled:{ bg: '#88888822', color: '#888888', label: '取消' },
};

const VIOL_META: Record<string, { icon: string; color: string; bg: string }> = {
  '越权调用':     { icon: '🚫', color: '#ff5270', bg: '#ff527018' },
  '流程跳步':     { icon: '⚡', color: '#e8a040', bg: '#e8a04018' },
  '断链超时':     { icon: '🔗', color: '#6a9eff', bg: '#6a9eff18' },
  '直接执行越权': { icon: '⛔', color: '#ff2d55', bg: '#ff2d5518' },
  '极端停滞':     { icon: '⏰', color: '#ff0040', bg: '#ff004018' },
  '未完成回奏':   { icon: '🛑', color: '#ff6b35', bg: '#ff6b3518' },
  '会话未注册':   { icon: '🔑', color: '#a07aff', bg: '#a07aff18' },
  '会话通信过多': { icon: '🔄', color: '#e8a040', bg: '#e8a04018' },
  '会话可疑':     { icon: '⚠️', color: '#e8a040', bg: '#e8a04018' },
  '会话违规':     { icon: '🚨', color: '#ff5270', bg: '#ff527018' },
};

const NOTIF_META: Record<string, { icon: string; color: string }> = {
  '越权通报': { icon: '🚨', color: '#ff5270' },
  '跳步通报': { icon: '⚡', color: '#e8a040' },
  '断链唤醒': { icon: '🔔', color: '#e8a040' },
  '断链通知': { icon: '📡', color: '#6a9eff' },
  '会话警告': { icon: '🔑', color: '#a07aff' },
  '归档':     { icon: '📦', color: '#888888' },
  '巡检':     { icon: '🔍', color: '#4ecdc4' },
  '唤醒':     { icon: '🔔', color: '#e8a040' },
  '通知':     { icon: '📡', color: '#6a9eff' },
  '违规':     { icon: '🚨', color: '#ff5270' },
};

const NOTIF_CATS = [
  { key: 'all', label: '全部' },
  { key: '越权通报' }, { key: '跳步通报' }, { key: '断链唤醒' },
  { key: '断链通知' }, { key: '会话警告' }, { key: '归档' }, { key: '巡检' },
] as const;

/* ── 滚动条样式注入 ── */
const SCROLLBAR_CSS = `.ag-scroll::-webkit-scrollbar{height:5px}.ag-scroll::-webkit-scrollbar-track{background:transparent}.ag-scroll::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}.ag-scroll{scrollbar-width:thin;scrollbar-color:var(--line) transparent}`;

/* ── 面板内子 Tab 样式 ── */
const INNER_TAB_CSS = `
.audit-inner-tabs { display: flex; gap: 0; border-bottom: 1px solid var(--line); margin-bottom: 14px; }
.audit-inner-tab { font-size: 12px; padding: 8px 16px; border-radius: 8px 8px 0 0; cursor: pointer; color: var(--muted);
  border: 1px solid transparent; border-bottom: none; white-space: nowrap; position: relative; bottom: -1px;
  transition: all .15s; user-select: none; background: none; }
.audit-inner-tab:hover { color: var(--text); background: var(--panel); }
.audit-inner-tab.active { color: var(--text); background: var(--panel); border-color: var(--line); font-weight: 600; }
`;

/* ── 卡片悬浮动画 ── */
const CARD_CSS = `
.audit-task-card { transition: border-color .15s, transform .1s, box-shadow .15s; }
.audit-task-card:hover { border-color: var(--acc); transform: translateY(-2px); box-shadow: 0 4px 20px rgba(106,158,255,.1); }
.audit-task-card.archived-card { transition: border-color .15s, transform .1s, box-shadow .15s; }
.audit-task-card.archived-card:hover { border-color: #2e3d6a; transform: translateY(-2px); box-shadow: 0 4px 20px rgba(0,0,0,.15); }
.audit-viol-card { transition: border-color .15s, box-shadow .15s; cursor: pointer; }
.audit-viol-card:hover { border-color: #2e3d6a; box-shadow: 0 2px 12px rgba(0,0,0,.1); }
`;

/* ── 筛选 Pill 样式 ── */
const PILL_CSS = `
.audit-pill { font-size: 11px; padding: 3px 10px; border-radius: 999px; cursor: pointer; border: 1px solid var(--line);
  background: var(--panel); color: var(--muted); transition: all .12s; white-space: nowrap; }
.audit-pill:hover { border-color: var(--acc); color: var(--text); }
.audit-pill.active { border-color: var(--acc); color: var(--acc); background: #0a1228; }
`;

/* ═══════════════════════════════════════════════════════════════════════
   主组件
   ═══════════════════════════════════════════════════════════════════════ */

type MainTab = 'overview' | 'violNotif' | 'archive' | 'about';

export default function AuditPanelEnhanced() {
  const auditData = useStore((s) => s.auditData);
  const liveStatus = useStore((s) => s.liveStatus);
  const loadAudit = useStore((s) => s.loadAudit);
  const setModalTaskId = useStore((s) => s.setModalTaskId);

  // 主 Tab 状态
  const [mainTab, setMainTab] = useState<MainTab>('overview');

  useEffect(() => { loadAudit(); }, [loadAudit]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    timerRef.current = setInterval(loadAudit, 10000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [loadAudit]);

  // ── 数据提取 ──
  const lastCheck = auditData?.last_check || '';
  const violations: AuditViolation[] = auditData?.violations || [];
  const watchedTasks: WatchedTask[] = auditData?.watched_tasks || [];
  const watchedCount = auditData?.watched_count || 0;
  const checkCount = auditData?.check_count || 0;
  const totalViolations = auditData?.total_violations || 0;
  const notifications: AuditNotification[] = auditData?.notifications || [];
  const archivedViolations: AuditViolation[] = auditData?.archived_violations || [];
  const archivedNotifs: AuditNotification[] = auditData?.archived_notifications || [];

  const allTasks: Task[] = liveStatus?.tasks || [];
  const archivedTasks = useMemo(() => allTasks.filter(t => t.archived), [allTasks]);
  const taskMap = useMemo(() => {
    const m = new Map<string, Task>();
    for (const t of allTasks) m.set(t.id, t);
    return m;
  }, [allTasks]);

  // 监察运行状态
  const isRunning = !!lastCheck;
  const lastCheckAgo = timeAgo(lastCheck);
  const isStale = (() => {
    if (!lastCheck) return true;
    try {
      const d = new Date(lastCheck.includes('T') ? lastCheck : lastCheck.replace(' ', 'T') + 'Z');
      return Date.now() - d.getTime() > 3 * 60 * 1000;
    } catch { return true; }
  })();

  // ── 选中任务（详情面板） ──
  const [selected, setSelected] = useState<{ id: string; archived: boolean } | null>(null);
  const [activityData, setActivityData] = useState<TaskActivityData | null>(null);
  const [activityLoading, setActivityLoading] = useState(false);
  const [detailTab, setDetailTab] = useState<'flow' | 'violation' | 'notif' | 'progress'>('flow');

  const openDetail = useCallback((id: string, archived = false) => {
    setSelected({ id, archived });
    setDetailTab('flow');
    setActivityData(null);
    setActivityLoading(true);
    api.taskActivity(id).then(r => setActivityData(r)).catch(() => {}).finally(() => setActivityLoading(false));
  }, []);
  const closeDetail = useCallback(() => { setSelected(null); setActivityData(null); }, []);

  const selTask = selected ? taskMap.get(selected.id) : null;
  const selViolations = useMemo(() => {
    if (!selected) return [];
    const src = selected.archived ? archivedViolations : violations;
    return src.filter(v => v.task_id === selected.id);
  }, [selected, violations, archivedViolations]);
  const selNotifs = useMemo(() => {
    if (!selected) return [];
    const src = selected.archived ? archivedNotifs : notifications;
    return src.filter(n => n.task_id === selected.id || (n.task_ids || []).includes(selected.id));
  }, [selected, notifications, archivedNotifs]);

  // ── 概览 Tab：卡片筛选 ──
  const [cardFilter, setCardFilter] = useState<'all' | 'violated' | 'clean'>('all');
  const filteredWatched = useMemo(() => {
    if (cardFilter === 'all') return watchedTasks;
    const vIds = new Set(violations.filter(v => watchedTasks.some(w => w.task_id === v.task_id)).map(v => v.task_id));
    return watchedTasks.filter(w => cardFilter === 'violated' ? vIds.has(w.task_id) : !vIds.has(w.task_id));
  }, [watchedTasks, violations, cardFilter]);

  // ── 违规&通报 Tab ──
  const [violNotifTab, setViolNotifTab] = useState<'violation' | 'notif'>('violation');

  // 通知分类
  const [notifCat, setNotifCat] = useState('all');
  const recentNotifs = useMemo(() => {
    const base = notifications.slice(-60).reverse();
    return notifCat === 'all' ? base : base.filter(n => n.type === notifCat);
  }, [notifications, notifCat]);
  const notifCounts = useMemo(() => {
    const c: Record<string, number> = { all: notifications.length };
    for (const n of notifications) c[n.type] = (c[n.type] || 0) + 1;
    return c;
  }, [notifications]);

  // 违规分组
  const [showResolved, setShowResolved] = useState(false);
  const activeViols = violations.filter(v => watchedTasks.some(w => w.task_id === v.task_id));
  const resolvedViols = violations.filter(v => !watchedTasks.some(w => w.task_id === v.task_id));
  const displayViols = showResolved ? violations : activeViols;
  const violsByTask = useMemo(() => {
    const m = new Map<string, AuditViolation[]>();
    for (const v of displayViols.slice(-200).reverse()) {
      if (!m.has(v.task_id)) m.set(v.task_id, []);
      m.get(v.task_id)!.push(v);
    }
    return m;
  }, [displayViols]);

  // ── 归档 Tab ──
  const [archiveCollapsed, setArchiveCollapsed] = useState(false);

  // ── 说明 Tab ──
  const [aboutCollapsed, setAboutCollapsed] = useState(true);

  // ── KPI 数据 ──
  const kpis = [
    { icon: isRunning && !isStale ? '🟢' : isRunning && isStale ? '🟡' : '🔴',
      label: '监察状态', value: isRunning && !isStale ? '运行中' : isRunning && isStale ? '可能停止' : '未启动',
      sub: lastCheckAgo ? `最后检查: ${lastCheckAgo}` : '暂无数据',
      badge: isRunning && !isStale ? 'ok' : isRunning && isStale ? 'warn' : 'err' },
    { icon: '📊', label: '累计检查', value: `${checkCount} 次`, sub: `发现 ${totalViolations} 项违规`, badge: '' },
    { icon: '👁️', label: '正在监察', value: `${watchedCount} 个任务`, sub: watchedCount > 0 ? '实时监控旨意任务' : '当前无活跃旨意', badge: '' },
    { icon: '📢', label: '通报记录', value: `${notifications.length} 条`, sub: notifications.length > 0 ? '含越权通报+断链唤醒' : '暂无通报', badge: '' },
  ];

  // ── 主 Tab 定义 ──
  const mainTabs: { key: MainTab; label: string; icon: string; badge?: number }[] = [
    { key: 'overview', label: '监察总览', icon: '👁️' },
    { key: 'violNotif', label: '违规 & 通报', icon: '🚨', badge: violations.length || undefined },
    { key: 'archive', label: '归档回溯', icon: '📦', badge: archivedTasks.length || undefined },
    { key: 'about', label: '监察说明', icon: '📋' },
  ];

  return (
    <div>
      <style>{SCROLLBAR_CSS}{INNER_TAB_CSS}{CARD_CSS}{PILL_CSS}</style>

      {/* ═══ Header ═══ */}
      <div className="hdr" style={{ marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800 }}>🛡️ 流程监察</div>
          <div className="sub-text">监督三省六部任务流转完整性，检测越权、跳步、断链</div>
        </div>
        <div className="hdr-r">
          <button className="btn btn-g" onClick={loadAudit}>⟳ 刷新</button>
        </div>
      </div>

      {/* ═══ KPI 指标行 ═══ */}
      <div className="off-kpi" style={{ marginBottom: 16 }}>
        {kpis.map((k, i) => (
          <div key={i} className="kpi">
            <div className="kpi-v" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span>{k.icon}</span>
              <span style={{ fontSize: 16 }}>{k.value}</span>
            </div>
            <div className="kpi-l" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span>{k.label}</span>
              {k.badge && <span className={`chip ${k.badge}`}>{k.sub}</span>}
              {!k.badge && <span style={{ fontSize: 10, color: 'var(--muted)', opacity: 0.7 }}>{k.sub}</span>}
            </div>
          </div>
        ))}
      </div>

      {/* ═══ 主 Tab 导航 ═══ */}
      <div className="tabs">
        {mainTabs.map(t => (
          <div key={t.key}
            className={`tab ${mainTab === t.key ? 'active' : ''}`}
            onClick={() => setMainTab(t.key)}>
            {t.icon} {t.label}
            {t.badge !== undefined && t.badge > 0 && <span className="tbadge">{t.badge}</span>}
          </div>
        ))}
      </div>

      {/* ═══ Tab: 监察总览 ═══ */}
      {mainTab === 'overview' && (
        <div>
          {/* 任务卡片墙 */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>正在监察的任务 ({watchedCount})</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {([['all', '全部'], ['violated', '有违规'], ['clean', '正常']] as const).map(([k, l]) => (
                <span key={k} className={`audit-pill ${cardFilter === k ? 'active' : ''}`}
                  onClick={() => setCardFilter(k)}>{l}</span>
              ))}
            </div>
          </div>

          {filteredWatched.length === 0 ? (
            <div className="mb-empty">当前没有活跃旨意任务，监察处于待命状态</div>
          ) : (
            <div className="ag-scroll" style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 6 }}>
              {filteredWatched.map(w => {
                const vCount = violations.filter(v => v.task_id === w.task_id).length;
                const task = taskMap.get(w.task_id);
                return (
                  <div key={w.task_id} onClick={() => openDetail(w.task_id)}
                    className={`edict-card audit-task-card ${vCount > 0 ? '' : ''}`}
                    style={{
                      minWidth: 300, maxWidth: 300, flexShrink: 0,
                      borderLeft: `4px solid ${vCount > 0 ? 'var(--danger)' : 'var(--ok)'}`,
                    }}>
                    {/* 任务 ID + 违规徽标 */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span className="ec-id">{w.task_id}</span>
                      {vCount > 0 && <span className="chip err">⚠ {vCount} 违规</span>}
                      {vCount === 0 && <span className="chip ok">正常</span>}
                    </div>

                    {/* 标题 */}
                    <div className="ec-title" style={{ fontSize: 14, marginBottom: 8 }}>{w.title}</div>

                    {/* 状态 + 部门 */}
                    <div className="ec-meta">
                      {(() => { const s = STATE_S[w.state]; return s ? (
                        <span className={`tag st-${w.state}`}>{s.label}</span>
                      ) : null; })()}
                      <span className="tag" style={{
                        borderColor: (DEPT_COLOR[w.org] || '#888') + '44',
                        color: DEPT_COLOR[w.org] || '#888',
                        background: (DEPT_COLOR[w.org] || '#888') + '18',
                      }}>{w.org || '—'}</span>
                    </div>

                    {/* 进展摘要 */}
                    {(task?.now && task.now !== '-') && (
                      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, lineHeight: 1.5,
                        overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box',
                        WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                        {task.now}
                      </div>
                    )}

                    {/* 底部信息 */}
                    <div className="ec-footer">
                      <span className="hb">流转 {w.flow_count} 步</span>
                      <span className="hb">🔑 {w.session_key_count ?? (w.session_keys ? Object.keys(w.session_keys).length : 0)} 会话</span>
                      <span className="hb" style={{ marginLeft: 'auto' }}>
                        {task?.updatedAt ? timeAgo(task.updatedAt) : '—'}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* 违规/通报快速摘要（点击跳转到违规通报Tab） */}
          {(activeViols.length > 0 || notifications.length > 0) && (
            <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {/* 违规快速预览 */}
              <div style={{ padding: 14, borderRadius: 12, background: 'var(--panel)', border: '1px solid var(--line)',
                cursor: 'pointer' }} onClick={() => { setMainTab('violNotif'); setViolNotifTab('violation'); }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--danger)' }}>🚨 活跃违规 ({activeViols.length})</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>查看详情 →</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 120, overflowY: 'auto' }}>
                  {violsByTask.size === 0 ? (
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>暂无活跃违规</div>
                  ) : (
                    Array.from(violsByTask.entries()).slice(0, 3).map(([tid, vs]) => {
                      const task = taskMap.get(tid);
                      return (
                        <div key={tid} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, padding: '4px 0' }}>
                          <span style={{ color: 'var(--acc)', fontWeight: 600, whiteSpace: 'nowrap' }}>{tid}</span>
                          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {task?.title || vs[0]?.title || tid}
                          </span>
                          <span className="chip err" style={{ flexShrink: 0 }}>{vs.length}条</span>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {/* 通报快速预览 */}
              <div style={{ padding: 14, borderRadius: 12, background: 'var(--panel)', border: '1px solid var(--line)',
                cursor: 'pointer' }} onClick={() => { setMainTab('violNotif'); setViolNotifTab('notif'); }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--warn)' }}>📢 最近通报 ({notifications.length})</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>查看详情 →</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 120, overflowY: 'auto' }}>
                  {notifications.length === 0 ? (
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>暂无通报记录</div>
                  ) : (
                    notifications.slice(-3).reverse().map((n, i) => {
                      const meta = NOTIF_META[n.type] || { icon: '📢', color: '#6a9eff' };
                      return (
                        <div key={`${n.sent_at}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '4px 0' }}>
                          <span>{meta.icon}</span>
                          <span style={{ fontWeight: 600, color: meta.color }}>{n.type}</span>
                          {n.to && <span style={{ color: 'var(--text)' }}>→ {n.to}</span>}
                          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--muted)' }}>
                            {n.summary || n.detail?.substring(0, 50) || ''}
                          </span>
                          <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{timeAgo(n.sent_at)}</span>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ Tab: 违规 & 通报 ═══ */}
      {mainTab === 'violNotif' && (
        <div>
          {/* 子 Tab */}
          <div className="audit-inner-tabs">
            <button className={`audit-inner-tab ${violNotifTab === 'violation' ? 'active' : ''}`}
              onClick={() => setViolNotifTab('violation')}>
              🚨 违规记录 ({activeViols.length}条活跃{resolvedViols.length > 0 ? `，${resolvedViols.length}条已解决` : ''})
            </button>
            <button className={`audit-inner-tab ${violNotifTab === 'notif' ? 'active' : ''}`}
              onClick={() => setViolNotifTab('notif')}>
              📢 通报记录 ({notifications.length})
            </button>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
              {violNotifTab === 'violation' && (
                <span className={`audit-pill ${showResolved ? 'active' : ''}`}
                  onClick={() => setShowResolved(!showResolved)}>
                  {showResolved ? '✅ 含已解决' : '🔍 仅活跃'}
                </span>
              )}
            </div>
          </div>

          {/* 违规列表 */}
          {violNotifTab === 'violation' && (
            displayViols.length === 0 ? (
              <div className="mb-empty">{isRunning ? '✅ 所有任务流程正常，暂无违规' : '暂无监察数据'}</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {Array.from(violsByTask.entries()).map(([tid, vs]) => {
                  const isResolved = !watchedTasks.some(w => w.task_id === tid);
                  const task = taskMap.get(tid);
                  return (
                    <div key={tid} onClick={() => openDetail(tid)}
                      className={`edict-card audit-viol-card ${isResolved ? 'archived' : ''}`}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span className="ec-id">{tid}</span>
                        <span className="ec-title" style={{ flex: 1, fontSize: 13, marginBottom: 0 }}>
                          {task?.title || vs[0]?.title || tid}
                        </span>
                        {isResolved && <span className="chip ok">✓ 已解决</span>}
                        <span className="chip err">{vs.length} 条违规</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )
          )}

          {/* 通报列表 */}
          {violNotifTab === 'notif' && (
            <>
              {/* 分类筛选 */}
              <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                {NOTIF_CATS.map(c => {
                  const cnt = notifCounts[c.key] || 0;
                  if (c.key !== 'all' && cnt === 0) return null;
                  const meta = NOTIF_META[c.key as string];
                  return (
                    <span key={c.key}
                      className={`audit-pill ${notifCat === c.key ? 'active' : ''}`}
                      onClick={() => setNotifCat(c.key)}>
                      {meta?.icon || '📋'} {c.key === 'all' ? '全部' : c.key} {cnt > 0 ? `(${cnt})` : ''}
                    </span>
                  );
                })}
              </div>
              {recentNotifs.length === 0 ? (
                <div className="mb-empty">{isRunning ? '✅ 暂无通报，所有流程正常' : '暂无监察数据'}</div>
              ) : (
                <div className="ag-scroll" style={{ maxHeight: 520, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, paddingRight: 4 }}>
                  {recentNotifs.map((n, i) => <NotifCard key={`${n.sent_at}-${i}`} n={n} onClick={() => {
                    const tid = n.task_id || (n.task_ids || [])[0];
                    if (tid) openDetail(tid);
                  }} />)}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ═══ Tab: 归档回溯 ═══ */}
      {mainTab === 'archive' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>归档任务 ({archivedTasks.length})</div>
            <span className="audit-pill" onClick={() => setArchiveCollapsed(!archiveCollapsed)}>
              {archiveCollapsed ? '▸ 展开' : '▾ 折叠'}
            </span>
          </div>
          {!archiveCollapsed && (archivedTasks.length === 0 ? (
            <div className="mb-empty">暂无归档任务</div>
          ) : (
            <div className="ag-scroll" style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 6 }}>
              {archivedTasks.map(t => {
                const st = STATE_S[t.state] || STATE_S.Done;
                const avCount = archivedViolations.filter(v => v.task_id === t.id).length;
                const flowLog = t.flow_log || [];
                const lastFlow = flowLog[flowLog.length - 1];
                return (
                  <div key={t.id} onClick={() => openDetail(t.id, true)}
                    className="edict-card archived audit-task-card archived-card"
                    style={{
                      minWidth: 300, maxWidth: 300, flexShrink: 0,
                      borderLeft: `4px solid ${t.state === 'Cancelled' ? '#888' : 'var(--ok)'}`,
                    }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 10, color: 'var(--muted)', fontWeight: 700 }}>{t.id}</span>
                      <span className={`tag st-${t.state}`}>{st.label}</span>
                      {avCount > 0 && <span className="chip err">⚠ {avCount} 违规</span>}
                    </div>
                    <div className="ec-title" style={{ fontSize: 14, marginBottom: 8 }}>{t.title}</div>

                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, lineHeight: 1.5 }}>
                      流转 {flowLog.length} 步
                      {avCount > 0 && <span style={{ color: 'var(--danger)', marginLeft: 8 }}>· {avCount} 条违规</span>}
                    </div>
                    {t.output && (
                      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, lineHeight: 1.5,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        📎 {t.output}
                      </div>
                    )}

                    {lastFlow && (
                      <div className="ec-footer" style={{ borderTop: '1px solid var(--line)', marginTop: 8 }}>
                        <span>最后: {lastFlow.from} → {lastFlow.to}</span>
                        <span>{timeAgo(lastFlow.at)}</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

      {/* ═══ Tab: 监察说明 ═══ */}
      {mainTab === 'about' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>📋 监察规则说明</div>
            <span className="audit-pill" onClick={() => setAboutCollapsed(!aboutCollapsed)}>
              {aboutCollapsed ? '▸ 展开' : '▾ 折叠'}
            </span>
          </div>
          {!aboutCollapsed && (
            <div className="sub-config" style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.8 }}>
              <div><b>监察范围：</b>仅监察 JJC- 开头的旨意任务，不监察对话</div>
              <div><b>完整流程：</b>皇上 → 太子 → 中书省 → 门下省 → 中书省 → 尚书省 → 六部 → 尚书省 → 中书省 → 太子 → 皇上</div>
              <div><b>越权调用：</b>from→to 不在合法流转对表内，监察会通过会话通知太子</div>
              <div><b>流程跳步：</b>缺少必要环节，仅记录不通知</div>
              <div><b>断链超时：</b>某部门超时未回应，监察自动唤醒 + 通知上级</div>
              <div><b>自动归档：</b>任务完成超过 5 分钟自动归档，可在「归档回溯」Tab 中查看日志</div>
              <div><b>运行方式：</b>pipeline_watchdog.py 每 60 秒由 run_loop.sh 调用一次</div>
            </div>
          )}
        </div>
      )}

      {/* ═══ 详情面板（滑出覆盖层）═══ */}
      {selected && selTask && (
        <div onClick={closeDetail} className="modal-bg" style={{ padding: 0, alignItems: 'stretch', justifyContent: 'flex-end' }}>
          <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
          <div onClick={e => e.stopPropagation()} style={{
            width: 560, maxWidth: '90vw', background: 'var(--panel)', borderLeft: '1px solid var(--line)',
            overflowY: 'auto', animation: 'slideIn 0.25s ease-out', display: 'flex', flexDirection: 'column',
            boxShadow: '-4px 0 24px rgba(0,0,0,0.3)',
          }}>
            {/* 面板头部 */}
            <div style={{ position: 'sticky', top: 0, zIndex: 10, background: 'var(--panel)',
              borderBottom: '1px solid var(--line)', padding: '16px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span className="modal-id">{selected.id}</span>
                    {(() => { const s = STATE_S[selTask.state]; return s ? (
                      <span className={`tag st-${selTask.state}`}>{s.label}</span>
                    ) : null; })()}
                    {selTask.org && (
                      <span className="tag" style={{
                        borderColor: (DEPT_COLOR[selTask.org] || '#888') + '44',
                        color: DEPT_COLOR[selTask.org] || '#888',
                        background: (DEPT_COLOR[selTask.org] || '#888') + '18',
                      }}>{selTask.org}</span>
                    )}
                  </div>
                  <div className="modal-title" style={{ fontSize: 18, marginBottom: 0 }}>{selTask.title}</div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={() => { closeDetail(); setModalTaskId(selected.id); }} className="btn btn-g">
                    📋 看板
                  </button>
                  <button onClick={closeDetail} className="modal-close" style={{ position: 'static' }}>✕</button>
                </div>
              </div>

              {/* 标签切换 */}
              <div className="audit-inner-tabs" style={{ marginTop: 12, marginBottom: 0 }}>
                {([
                  ['flow', '📜 流转记录', selTask.flow_log?.length || 0],
                  ['violation', '🚨 违规记录', selViolations.length],
                  ['notif', '📢 通报记录', selNotifs.length],
                  ['progress', '📊 进展日志', activityData?.activity?.length || 0],
                ] as const).map(([k, label, cnt]) => (
                  <button key={k} className={`audit-inner-tab ${detailTab === k ? 'active' : ''}`}
                    onClick={() => setDetailTab(k)}>
                    {label}{cnt > 0 && <span className="tbadge">{cnt}</span>}
                  </button>
                ))}
              </div>
            </div>

            {/* 标签内容 */}
            <div className="modal-body" style={{ padding: '16px 20px' }}>
              {detailTab === 'flow' && <FlowTimelinePanel task={selTask} />}
              {detailTab === 'violation' && <ViolationPanel violations={selViolations} />}
              {detailTab === 'notif' && <NotifPanel notifs={selNotifs} />}
              {detailTab === 'progress' && <ProgressPanel activity={activityData} loading={activityLoading}
                progressLog={selTask.activity || []} taskNow={selTask.now} taskTodos={selTask.todos} />}
            </div>
          </div>
        </div>
      )}

      {/* 详情面板 — 无任务数据时的兜底 */}
      {selected && !selTask && (
        <div onClick={closeDetail} className="modal-bg" style={{ padding: 0, alignItems: 'stretch', justifyContent: 'flex-end' }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: 560, maxWidth: '90vw', background: 'var(--panel)', borderLeft: '1px solid var(--line)',
            overflowY: 'auto', padding: 20, animation: 'slideIn 0.25s ease-out',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="modal-id">{selected.id}</span>
                {selected.archived && <span className="chip" style={{ opacity: 0.7 }}>(归档任务)</span>}
              </div>
              <button onClick={closeDetail} className="modal-close" style={{ position: 'static' }}>✕</button>
            </div>
            {selected.archived ? (
              <ArchiveDetailFallback taskId={selected.id} archivedViols={archivedViolations}
                archivedNotifs={archivedNotifs} openDetail={openDetail} closeDetail={closeDetail} />
            ) : (
              <div className="mb-empty">任务数据未找到，请刷新后重试</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   子组件（保持原有功能不变）
   ═══════════════════════════════════════════════════════════════════════ */

/* ── 通知卡片 ── */
function NotifCard({ n, onClick }: { n: AuditNotification; onClick?: () => void }) {
  const meta = NOTIF_META[n.type] || { icon: '📢', color: '#6a9eff' };
  const sent = timeAgo(n.sent_at || (n as any).at || '');
  const [expanded, setExpanded] = useState(false);
  const isFailed = n.status === 'failed';
  return (
    <div onClick={() => { if (n.detail) setExpanded(!expanded); if (onClick) onClick(); }}
      className="la-entry"
      style={{
        borderLeft: `3px solid ${meta.color}`,
        background: isFailed ? '#ff527008' : undefined,
        cursor: 'pointer',
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13 }}>{meta.icon}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: meta.color, padding: '1px 6px', borderRadius: 4,
          background: `${meta.color}15` }}>{n.type}</span>
        {n.to && <span style={{ fontSize: 12, fontWeight: 600 }}>→ {n.to}</span>}
        {(n.task_id || n.task_ids) && (
          <span className="ec-id" style={{ fontSize: 10 }}>
            {n.task_ids ? n.task_ids.join(', ') : n.task_id}
          </span>
        )}
        <span style={{ fontSize: 11, color: 'var(--muted)', flex: 1, overflow: 'hidden',
          textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
          {n.summary || n.detail?.substring(0, 80) || ''}
        </span>
        <span style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{isFailed ? '❌' : '✅'}</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>{sent}</span>
      </div>
      {expanded && n.detail && (
        <div style={{ marginTop: 6, fontSize: 11, color: 'var(--muted)', padding: '6px 10px', borderRadius: 4,
          background: 'var(--panel2)', wordBreak: 'break-all', lineHeight: 1.6 }}>{n.detail}</div>
      )}
    </div>
  );
}

/* ── 流转时间线面板 ── */
function FlowTimelinePanel({ task }: { task: Task }) {
  const flowLog = task.flow_log || [];
  if (flowLog.length === 0) return <div className="mb-empty">暂无流转记录</div>;
  return (
    <div>
      <div className="fl-timeline">
        {flowLog.map((f, i) => {
          const fromColor = DEPT_COLOR[f.from] || '#888';
          const toColor = DEPT_COLOR[f.to] || '#888';
          const t = (() => {
            try { const d = new Date(f.at.includes('T') ? f.at : f.at.replace(' ', 'T'));
              return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            } catch { return ''; }
          })();
          const dateStr = (() => {
            try { const d = new Date(f.at.includes('T') ? f.at : f.at.replace(' ', 'T'));
              return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
            } catch { return ''; }
          })();
          return (
            <div key={i} className="fl-item">
              <div className="fl-time">
                <div>{t}</div>
                {i === 0 && <div style={{ opacity: 0.6 }}>{dateStr}</div>}
              </div>
              <div className="fl-dot" style={{ background: toColor, boxShadow: `0 0 6px ${toColor}44` }} />
              <div className="fl-content">
                <div className="fl-who">
                  <span className="from" style={{ color: fromColor }}>{f.from}</span>
                  <span style={{ color: 'var(--muted)', margin: '0 4px' }}>→</span>
                  <span className="to" style={{ color: toColor }}>{f.to}</span>
                </div>
                {f.remark && <div className="fl-rem">{f.remark.replace(/^🧭\s*/, '')}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── 违规详情面板 ── */
function ViolationPanel({ violations }: { violations: AuditViolation[] }) {
  if (violations.length === 0) return <div className="mb-empty">✅ 该任务无违规记录</div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {violations.map((v, i) => {
        const meta = VIOL_META[v.type] || { icon: '⚠️', color: '#e8a040', bg: '#e8a04018' };
        return (
          <div key={`${v.detected_at}-${i}`} style={{ padding: '10px 12px', borderRadius: 6,
            background: meta.bg, border: `1px solid ${meta.color}22`, borderLeft: `3px solid ${meta.color}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span style={{ fontSize: 13 }}>{meta.icon}</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: meta.color, padding: '1px 6px', borderRadius: 3,
                background: `${meta.color}18` }}>{v.type}</span>
              <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>{timeAgo(v.detected_at)}</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>{v.detail}</div>
          </div>
        );
      })}
    </div>
  );
}

/* ── 通报详情面板 ── */
function NotifPanel({ notifs }: { notifs: AuditNotification[] }) {
  if (notifs.length === 0) return <div className="mb-empty">该任务无通报记录</div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {notifs.map((n, i) => <NotifCard key={`${n.sent_at}-${i}`} n={n} />)}
    </div>
  );
}

/* ── 进展日志面板 ── */
function ProgressPanel({ activity, loading, progressLog, taskNow, taskTodos }: {
  activity: TaskActivityData | null; loading: boolean;
  progressLog: any[]; taskNow: string; taskTodos: any[];
}) {
  if (loading) return <div className="mb-empty">加载中...</div>;

  const entries = activity?.activity || progressLog || [];
  if (entries.length === 0 && !taskNow) return <div className="mb-empty">暂无进展记录</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* 阶段耗时 */}
      {activity?.phaseDurations && activity.phaseDurations.length > 0 && (
        <div className="sched-section" style={{ marginBottom: 0 }}>
          <div className="sched-head">
            <span className="sched-title">⏱️ 阶段耗时</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {activity.phaseDurations.map((p, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                <span style={{ color: 'var(--text)' }}>{p.phase}</span>
                <span style={{ color: p.ongoing ? '#ff9f43' : 'var(--muted)', fontWeight: p.ongoing ? 600 : 400 }}>
                  {p.ongoing ? '⏳ ' : ''}{p.durationText}</span>
              </div>
            ))}
          </div>
          {activity.totalDuration && (
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8, borderTop: '1px solid var(--line)', paddingTop: 6 }}>
              总耗时: {activity.totalDuration}
            </div>
          )}
        </div>
      )}

      {/* 当前进度 */}
      {taskNow && taskNow !== '-' && (
        <div className="cur-stage" style={{ marginBottom: 0 }}>
          <span className="cs-icon">📍</span>
          <div className="cs-info">
            <div className="cs-action">当前进展</div>
            <div style={{ fontSize: 12, color: 'var(--text)', lineHeight: 1.6, marginTop: 2 }}>{taskNow}</div>
          </div>
        </div>
      )}

      {/* Todo 列表 */}
      {taskTodos && taskTodos.length > 0 && (
        <div className="todo-section" style={{ marginBottom: 0 }}>
          <div className="todo-header">
            <span style={{ fontSize: 12, fontWeight: 700 }}>📝 任务清单</span>
          </div>
          <div className="todo-list">
            {taskTodos.map((todo: any, i: number) => (
              <div key={i} className={`todo-item ${todo.status === 'completed' ? 'done' : ''}`}>
                <div className="t-row">
                  <span className="t-icon">
                    {todo.status === 'completed' ? '✅' : todo.status === 'in-progress' ? '🔄' : '⬜'}
                  </span>
                  <span className="t-title">{todo.title}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 进展日志列表 */}
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>📋 进展日志 ({entries.length})</div>
      <div className="la-log" style={{ maxHeight: 300 }}>
        {entries.slice(-50).reverse().map((e: any, i: number) => {
          const at = e.at ? timeAgo(typeof e.at === 'number' ? new Date(e.at * 1000).toISOString() : e.at) : '';
          const agent = e.agent || e.agentLabel || '';
          const text = e.text || e.remark || '';
          const tool = e.tool || e.tools?.[0]?.name || '';
          return (
            <div key={i} className="la-entry">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                {agent && (
                  <span style={{ fontSize: 11, fontWeight: 600, padding: '1px 6px', borderRadius: 3,
                    background: (DEPT_COLOR[agent] || '#888') + '18', color: DEPT_COLOR[agent] || '#888' }}>{agent}</span>
                )}
                {tool && <span style={{ fontSize: 10, color: '#a07aff' }}>🔧 {tool}</span>}
                <span className="la-time">{at}</span>
              </div>
              {text && <div className="la-body" style={{ fontSize: 11, color: 'var(--text)', lineHeight: 1.5, marginTop: 2 }}>{text}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── 归档详情面板（任务数据不在 liveStatus 中时的兜底）── */
function ArchiveDetailFallback({ taskId, archivedViols, archivedNotifs, openDetail, closeDetail }: {
  taskId: string; archivedViols: AuditViolation[]; archivedNotifs: AuditNotification[];
  openDetail: (id: string, archived?: boolean) => void; closeDetail: () => void;
}) {
  const [tab, setTab] = useState<'violation' | 'notif'>('violation');
  const viols = archivedViols.filter(v => v.task_id === taskId);
  const notifs = archivedNotifs.filter(n => n.task_id === taskId || (n.task_ids || []).includes(taskId));

  return (
    <div>
      <div className="audit-inner-tabs" style={{ marginBottom: 16 }}>
        <button className={`audit-inner-tab ${tab === 'violation' ? 'active' : ''}`}
          onClick={() => setTab('violation')}>🚨 违规 ({viols.length})</button>
        <button className={`audit-inner-tab ${tab === 'notif' ? 'active' : ''}`}
          onClick={() => setTab('notif')}>📢 通报 ({notifs.length})</button>
      </div>
      {tab === 'violation' && <ViolationPanel violations={viols} />}
      {tab === 'notif' && <NotifPanel notifs={notifs} />}
    </div>
  );
}
