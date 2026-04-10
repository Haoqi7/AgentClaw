/**
 * AuditPanelEnhanced.tsx — 流程监察面板（V2 重构版）
 *
 * 重构要点：
 * 1. 主界面精简：KPI + 任务卡片墙（每卡片内嵌最新3通报+3违规）+ 归档回溯 + 监察说明
 * 2. 移除独立的「违规 & 通报」Tab，违规/通报信息直接嵌入任务卡片底部
 * 3. 任务详情改为居中弹窗（非侧边栏滑出），点击遮罩或×号关闭
 * 4. 弹窗内含子Tab：流转记录 / 违规记录 / 通报记录（含头部筛选） / 进展日志
 * 5. 任务完成后自动归档到「归档任务回溯」
 * 6. 保持原有代码风格与设计系统（CSS class + CSS variables）
 *
 * 接入方式不变：
 *   import AuditPanel from './components/AuditPanelEnhanced';
 */
import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useStore, timeAgo } from '../store';
import { api, type Task, type AuditViolation, type AuditNotification, type WatchedTask, type TaskActivityData } from '../api';

/* ═══════════════════════════════════════════════════════════════════════
   常量 & 样式
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
const SCROLLBAR_CSS = `.ag-scroll::-webkit-scrollbar{height:5px;width:5px}.ag-scroll::-webkit-scrollbar-track{background:transparent}.ag-scroll::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}.ag-scroll{scrollbar-width:thin;scrollbar-color:var(--line) transparent}`;

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
.audit-task-card { transition: border-color .15s, transform .1s, box-shadow .15s; cursor: pointer; }
.audit-task-card:hover { border-color: var(--acc); transform: translateY(-2px); box-shadow: 0 4px 20px rgba(106,158,255,.1); }
.audit-task-card.archived-card:hover { border-color: #2e3d6a; box-shadow: 0 4px 20px rgba(0,0,0,.15); }
`;

/* ── 筛选 Pill 样式 ── */
const PILL_CSS = `
.audit-pill { font-size: 11px; padding: 3px 10px; border-radius: 999px; cursor: pointer; border: 1px solid var(--line);
  background: var(--panel); color: var(--muted); transition: all .12s; white-space: nowrap; }
.audit-pill:hover { border-color: var(--acc); color: var(--text); }
.audit-pill.active { border-color: var(--acc); color: var(--acc); background: #0a1228; }
`;

/* ── 弹窗动画样式 ── */
const MODAL_CSS = `
@keyframes auditModalIn {
  from { opacity: 0; transform: scale(0.92) translateY(20px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
@keyframes auditModalBgIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
.audit-modal-bg { animation: auditModalBgIn 0.2s ease-out; }
.audit-modal-panel { animation: auditModalIn 0.25s ease-out; }
`;

/* ── 总览增强区域样式 ── */
const OVERVIEW_CSS = `
.ov-section { background: var(--panel2, rgba(255,255,255,0.03)); border: 1px solid var(--line);
  border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; }
.ov-section-title { font-size: 11px; font-weight: 700; color: var(--muted); margin-bottom: 8px;
  text-transform: uppercase; letter-spacing: 0.5px; display: flex; align-items: center; gap: 6; }

/* 违规类型分布 */
.ov-viol-dist { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.ov-viol-chip { display: flex; align-items: center; gap: 4px; font-size: 10px; padding: 2px 8px;
  border-radius: 4px; background: var(--panel); border: 1px solid var(--line); white-space: nowrap; }
.ov-viol-chip-count { font-weight: 700; }
.ov-viol-bar { width: 24px; height: 4px; border-radius: 2px; background: var(--line); overflow: hidden; display: inline-block; vertical-align: middle; }
.ov-viol-bar-fill { height: 100%; border-radius: 2px; transition: width .3s; }

/* 部门分布 */
.ov-dept-cloud { display: flex; flex-wrap: wrap; gap: 6px; }
.ov-dept-tag { font-size: 10px; padding: 2px 8px; border-radius: 4px; font-weight: 600;
  border: 1px solid; white-space: nowrap; transition: all .15s; cursor: default; }
.ov-dept-tag:hover { transform: scale(1.05); }

/* 全局动态流 */
.ov-feed-list { display: flex; flex-direction: column; gap: 4px; max-height: 160px; overflow-y: auto; }
.ov-feed-item { display: flex; align-items: center; gap: 6px; font-size: 11px; padding: 4px 8px;
  border-radius: 6px; background: rgba(255,255,255,0.02); transition: background .12s; cursor: pointer; }
.ov-feed-item:hover { background: rgba(255,255,255,0.05); }
.ov-feed-icon { flex-shrink: 0; font-size: 12px; }
.ov-feed-type { font-weight: 600; white-space: nowrap; font-size: 10px; padding: 1px 5px; border-radius: 3px; }
.ov-feed-summary { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.ov-feed-time { color: var(--muted); white-space: nowrap; font-size: 10px; }
.ov-feed-task { font-size: 10px; color: var(--acc); white-space: nowrap; max-width: 80px; overflow: hidden; text-overflow: ellipsis; }
.ov-empty-feed { font-size: 11px; color: var(--muted); text-align: center; padding: 12px 0; }
`;

/* ── 卡片内嵌记录行样式 ── */
const INLINE_ROW_CSS = `
.audit-inline-row { display: flex; align-items: center; gap: 6px; font-size: 11px; padding: 3px 0;
  border-bottom: 1px solid var(--line); line-height: 1.4; }
.audit-inline-row:last-child { border-bottom: none; }
.audit-inline-row .ir-icon { flex-shrink: 0; font-size: 11px; }
.audit-inline-row .ir-type { font-weight: 600; white-space: nowrap; }
.audit-inline-row .ir-summary { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.audit-inline-row .ir-time { color: var(--muted); white-space: nowrap; font-size: 10px; }
`;

/* ═══════════════════════════════════════════════════════════════════════
   主组件
   ═══════════════════════════════════════════════════════════════════════ */

type MainTab = 'overview' | 'archive' | 'about';

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

  // ── 选中任务（弹窗详情） ──
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

  // ── 弹窗内通报记录的分类筛选 ──
  const [modalNotifCat, setModalNotifCat] = useState('all');
  const selFilteredNotifs = useMemo(() => {
    if (!selected) return [];
    if (modalNotifCat === 'all') return selNotifs;
    return selNotifs.filter(n => n.type === modalNotifCat);
  }, [selected, selNotifs, modalNotifCat]);
  const selNotifCounts = useMemo(() => {
    const c: Record<string, number> = { all: selNotifs.length };
    for (const n of selNotifs) c[n.type] = (c[n.type] || 0) + 1;
    return c;
  }, [selNotifs]);

  // ── 概览 Tab：卡片筛选 ──
  const [cardFilter, setCardFilter] = useState<'all' | 'violated' | 'clean'>('all');
  const filteredWatched = useMemo(() => {
    if (cardFilter === 'all') return watchedTasks;
    const vIds = new Set(violations.filter(v => watchedTasks.some(w => w.task_id === v.task_id)).map(v => v.task_id));
    return watchedTasks.filter(w => cardFilter === 'violated' ? vIds.has(w.task_id) : !vIds.has(w.task_id));
  }, [watchedTasks, violations, cardFilter]);

  // ── 归档 Tab ──
  const [archiveCollapsed, setArchiveCollapsed] = useState(false);

  // ── 说明 Tab ──
  const [aboutCollapsed, setAboutCollapsed] = useState(true);

  // ── 预计算：全局最新违规/通报（用于 KPI 统计） ──
  const activeViolsCount = violations.filter(v => watchedTasks.some(w => w.task_id === v.task_id)).length;

  // ── 总览增强数据 ──

  // 违规类型分布（仅活跃任务）
  const violTypeDist = useMemo(() => {
    const wIds = new Set(watchedTasks.map(w => w.task_id));
    const m: Record<string, number> = {};
    for (const v of violations) { if (wIds.has(v.task_id)) m[v.type] = (m[v.type] || 0) + 1; }
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  }, [violations, watchedTasks]);
  const violTypeMax = Math.max(...violTypeDist.map(([, c]) => c), 1);

  // 部门分布（当前负责部门）
  const deptDist = useMemo(() => {
    const m: Record<string, number> = {};
    for (const w of watchedTasks) { if (w.org) m[w.org] = (m[w.org] || 0) + 1; }
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  }, [watchedTasks]);

  // 全局最新动态（违规+通报合并按时间排序，取最近6条）
  const globalFeed = useMemo(() => {
    const items: { kind: 'viol' | 'notif'; icon: string; type: string; color: string; summary: string; time: string; taskId: string }[] = [];
    const wIds = new Set(watchedTasks.map(w => w.task_id));
    for (const v of violations) {
      if (wIds.has(v.task_id)) {
        const meta = VIOL_META[v.type] || { icon: '⚠️', color: '#e8a040' };
        items.push({ kind: 'viol', icon: meta.icon, type: v.type, color: meta.color,
          summary: v.detail?.substring(0, 50) || v.title, time: v.detected_at, taskId: v.task_id });
      }
    }
    for (const n of notifications) {
      const tid = n.task_id || (n.task_ids || [])[0] || '';
      if (wIds.has(tid)) {
        const meta = NOTIF_META[n.type] || { icon: '📢', color: '#6a9eff' };
        items.push({ kind: 'notif', icon: meta.icon, type: n.type, color: meta.color,
          summary: n.summary || n.detail?.substring(0, 50) || '', time: n.sent_at || '', taskId: tid });
      }
    }
    items.sort((a, b) => {
      try {
        const da = new Date(a.time.includes('T') ? a.time : a.time.replace(' ', 'T')).getTime();
        const db = new Date(b.time.includes('T') ? b.time : b.time.replace(' ', 'T')).getTime();
        return db - da;
      } catch { return 0; }
    });
    return items.slice(0, 6);
  }, [violations, notifications, watchedTasks]);

  // ── KPI 数据（移除了"通报记录"KPI，改为"活跃违规"） ──
  const kpis = [
    { icon: isRunning && !isStale ? '🟢' : isRunning && isStale ? '🟡' : '🔴',
      label: '监察状态', value: isRunning && !isStale ? '运行中' : isRunning && isStale ? '可能停止' : '未启动',
      sub: lastCheckAgo ? `最后检查: ${lastCheckAgo}` : '暂无数据',
      badge: isRunning && !isStale ? 'ok' : isRunning && isStale ? 'warn' : 'err' },
    { icon: '📊', label: '累计检查', value: `${checkCount} 次`, sub: `发现 ${totalViolations} 项违规`, badge: '' },
    { icon: '👁️', label: '正在监察', value: `${watchedCount} 个任务`, sub: watchedCount > 0 ? '实时监控旨意任务' : '当前无活跃旨意', badge: '' },
    { icon: '🚨', label: '活跃违规', value: `${activeViolsCount} 项`, sub: notifications.length > 0 ? `通报 ${notifications.length} 条` : '暂无违规', badge: '' },
  ];

  // ── 主 Tab 定义（移除"违规 & 通报"Tab） ──
  const mainTabs: { key: MainTab; label: string; icon: string; badge?: number }[] = [
    { key: 'overview', label: '监察总览', icon: '👁️' },
    { key: 'archive', label: '归档任务回溯', icon: '📦', badge: archivedTasks.length || undefined },
    { key: 'about', label: '监察说明', icon: '📋' },
  ];

  // ── 辅助函数：获取某任务最新的3条违规 ──
  const getTaskRecentViols = useCallback((taskId: string) => {
    return violations.filter(v => v.task_id === taskId).slice(-3).reverse();
  }, [violations]);

  // ── 辅助函数：获取某任务最新的3条通报 ──
  const getTaskRecentNotifs = useCallback((taskId: string) => {
    return notifications.filter(n => n.task_id === taskId || (n.task_ids || []).includes(taskId)).slice(-3).reverse();
  }, [notifications]);

  return (
    <div>
      <style>{SCROLLBAR_CSS}{INNER_TAB_CSS}{CARD_CSS}{PILL_CSS}{MODAL_CSS}{INLINE_ROW_CSS}{OVERVIEW_CSS}</style>

      {/* ═══ Header ═══ */}
      <div className="hdr" style={{ marginBottom: 14 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 20, fontWeight: 800 }}>🛡️ 流程监察</span>
            <span className="chip" style={{ fontSize: 10, opacity: 0.8, borderColor: 'var(--acc)', color: 'var(--acc)', background: 'var(--acc)' + '18' }}>v1</span>
          </div>
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
          {/* ══ 概览增强区域：信息摘要面板（两列布局） ══ */}
          {watchedCount > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12, marginBottom: 14 }}>

              {/* ── 左列：部门分布 + 违规类型分布 ── */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {/* 部门分布 */}
                {deptDist.length > 0 && (
                  <div className="ov-section">
                    <div className="ov-section-title">🏛️ 部门分布</div>
                    <div className="ov-dept-cloud">
                      {deptDist.map(([dept, count]) => {
                        const c = DEPT_COLOR[dept] || '#888';
                        return (
                          <span key={dept} className="ov-dept-tag"
                            style={{ color: c, borderColor: c + '44', background: c + '15' }}>
                            {dept} <span style={{ opacity: 0.7, fontWeight: 400, marginLeft: 2 }}>x{count}</span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* 违规类型分布 */}
                {violTypeDist.length > 0 && (
                  <div className="ov-section">
                    <div className="ov-section-title">🚨 违规类型分布 (共 {activeViolsCount})</div>
                    <div className="ov-viol-dist">
                      {violTypeDist.map(([type, count]) => {
                        const meta = VIOL_META[type] || { icon: '⚠️', color: '#e8a040' };
                        const pct = (count / violTypeMax) * 100;
                        return (
                          <div key={type} className="ov-viol-chip">
                            <span>{meta.icon}</span>
                            <span className="ov-viol-chip-count" style={{ color: meta.color }}>{count}</span>
                            <span style={{ color: 'var(--muted)' }}>{type}</span>
                            <span className="ov-viol-bar"><span className="ov-viol-bar-fill" style={{ width: `${pct}%`, background: meta.color }} /></span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              {/* ── 右列：全局最新动态流 ── */}
              <div className="ov-section" style={{ display: 'flex', flexDirection: 'column' }}>
                <div className="ov-section-title" style={{ marginBottom: globalFeed.length > 0 ? 6 : 0 }}>
                  📡 最新动态 <span style={{ fontWeight: 400, opacity: 0.6 }}>({globalFeed.length} 条)</span>
                </div>
                {globalFeed.length > 0 ? (
                  <div className="ov-feed-list ag-scroll">
                    {globalFeed.map((item, i) => (
                      <div key={`${item.kind}-${i}`} className="ov-feed-item"
                        onClick={() => openDetail(item.taskId)}>
                        <span className="ov-feed-icon">{item.icon}</span>
                        <span className="ov-feed-type" style={{ color: item.color, background: item.color + '15' }}>
                          {item.type}
                        </span>
                        <span className="ov-feed-summary">{item.summary}</span>
                        <span className="ov-feed-task" title={item.taskId}>{item.taskId}</span>
                        <span className="ov-feed-time">{timeAgo(item.time)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="ov-empty-feed">暂无最新动态，一切正常</div>
                )}
              </div>
            </div>
          )}

          {/* ══ 任务卡片区域标题 + 筛选 ══ */}
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
            /* ── 任务卡片网格（一行展示三个任务） ── */
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 14, paddingBottom: 8 }}>
              {filteredWatched.map(w => {
                const vCount = violations.filter(v => v.task_id === w.task_id).length;
                const task = taskMap.get(w.task_id);
                const recentViols = getTaskRecentViols(w.task_id);
                const recentNotifs = getTaskRecentNotifs(w.task_id);
                return (
                  <div key={w.task_id} onClick={() => openDetail(w.task_id)}
                    className={`edict-card audit-task-card`}
                    style={{
                      borderLeft: `4px solid ${vCount > 0 ? 'var(--danger)' : 'var(--ok)'}`,
                    }}>
                    {/* 任务 ID + 状态 */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span className="ec-id">{w.task_id}</span>
                      {(() => { const s = STATE_S[w.state]; return s ? (
                        <span className={`tag st-${w.state}`}>{s.label}</span>
                      ) : null; })()}
                      <span className="tag" style={{
                        borderColor: (DEPT_COLOR[w.org] || '#888') + '44',
                        color: DEPT_COLOR[w.org] || '#888',
                        background: (DEPT_COLOR[w.org] || '#888') + '18',
                      }}>{w.org || '—'}</span>
                    </div>

                    {/* 标题 */}
                    <div className="ec-title" style={{ fontSize: 14, marginBottom: 8 }}>{w.title}</div>

                    {/* 进展摘要 */}
                    {(task?.now && task.now !== '-') && (
                      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, lineHeight: 1.5,
                        overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box',
                        WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                        {task.now}
                      </div>
                    )}

                    {/* 底部元信息 */}
                    <div className="ec-footer" style={{ marginBottom: 8, paddingBottom: 0, borderBottom: 'none' }}>
                      <span className="hb">流转 {w.flow_count} 步</span>
                      <span className="hb">🔑 {w.session_key_count ?? (w.session_keys ? Object.keys(w.session_keys).length : 0)} 会话</span>
                      {vCount > 0 && <span className="hb" style={{ color: 'var(--danger)' }}>⚠ {vCount} 违规</span>}
                      <span className="hb" style={{ marginLeft: 'auto' }}>
                        {task?.updatedAt ? timeAgo(task.updatedAt) : '—'}
                      </span>
                    </div>

                    {/* ── 卡片内嵌：最新3条通报 + 3条违规（共6行） ── */}
                    {(recentNotifs.length > 0 || recentViols.length > 0) && (
                      <div style={{ borderTop: '1px solid var(--line)', marginTop: 0, paddingTop: 6 }}>
                        {/* 最新通报记录行 */}
                        {recentNotifs.slice(0, 3).map((n, i) => {
                          const meta = NOTIF_META[n.type] || { icon: '📢', color: '#6a9eff' };
                          return (
                            <div key={`notif-${i}`} className="audit-inline-row">
                              <span className="ir-icon">{meta.icon}</span>
                              <span className="ir-type" style={{ color: meta.color }}>{n.type}</span>
                              <span className="ir-summary">{n.summary || n.detail?.substring(0, 40) || ''}</span>
                              <span className="ir-time">{timeAgo(n.sent_at)}</span>
                            </div>
                          );
                        })}
                        {/* 最新违规记录行 */}
                        {recentViols.slice(0, 3).map((v, i) => {
                          const meta = VIOL_META[v.type] || { icon: '⚠️', color: '#e8a040', bg: '#e8a04018' };
                          return (
                            <div key={`viol-${i}`} className="audit-inline-row">
                              <span className="ir-icon">{meta.icon}</span>
                              <span className="ir-type" style={{ color: meta.color }}>{v.type}</span>
                              <span className="ir-summary">{v.detail?.substring(0, 40) || v.title}</span>
                              <span className="ir-time">{timeAgo(v.detected_at)}</span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ═══ Tab: 归档任务回溯 ═══ */}
      {mainTab === 'archive' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>归档任务回溯 ({archivedTasks.length})</div>
            <span className="audit-pill" onClick={() => setArchiveCollapsed(!archiveCollapsed)}>
              {archiveCollapsed ? '▸ 展开' : '▾ 折叠'}
            </span>
          </div>
          {!archiveCollapsed && (archivedTasks.length === 0 ? (
            <div className="mb-empty">暂无归档任务，任务完成后将自动归档于此</div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 14, paddingBottom: 8 }}>
              {archivedTasks.map(t => {
                const st = STATE_S[t.state] || STATE_S.Done;
                const avCount = archivedViolations.filter(v => v.task_id === t.id).length;
                const flowLog = t.flow_log || [];
                const lastFlow = flowLog[flowLog.length - 1];
                // 归档任务的最新违规/通报
                const archViols = archivedViolations.filter(v => v.task_id === t.id).slice(-3).reverse();
                const archNotifs = archivedNotifs.filter(n => n.task_id === t.id || (n.task_ids || []).includes(t.id)).slice(-3).reverse();
                return (
                  <div key={t.id} onClick={() => openDetail(t.id, true)}
                    className="edict-card archived audit-task-card archived-card"
                    style={{
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

                    {/* ── 归档卡片内嵌：最新3条通报 + 3条违规 ── */}
                    {(archNotifs.length > 0 || archViols.length > 0) && (
                      <div style={{ borderTop: '1px solid var(--line)', marginTop: 0, paddingTop: 6 }}>
                        {archNotifs.slice(0, 3).map((n, i) => {
                          const meta = NOTIF_META[n.type] || { icon: '📢', color: '#6a9eff' };
                          return (
                            <div key={`notif-${i}`} className="audit-inline-row">
                              <span className="ir-icon">{meta.icon}</span>
                              <span className="ir-type" style={{ color: meta.color }}>{n.type}</span>
                              <span className="ir-summary">{n.summary || n.detail?.substring(0, 40) || ''}</span>
                              <span className="ir-time">{timeAgo(n.sent_at)}</span>
                            </div>
                          );
                        })}
                        {archViols.slice(0, 3).map((v, i) => {
                          const meta = VIOL_META[v.type] || { icon: '⚠️', color: '#e8a040', bg: '#e8a04018' };
                          return (
                            <div key={`viol-${i}`} className="audit-inline-row">
                              <span className="ir-icon">{meta.icon}</span>
                              <span className="ir-type" style={{ color: meta.color }}>{v.type}</span>
                              <span className="ir-summary">{v.detail?.substring(0, 40) || v.title}</span>
                              <span className="ir-time">{timeAgo(v.detected_at)}</span>
                            </div>
                          );
                        })}
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
              <div><b>自动归档：</b>任务完成超过 5 分钟自动归档，可在「归档任务回溯」Tab 中查看日志</div>
              <div><b>运行方式：</b>pipeline_watchdog.py 每 60 秒由 run_loop.sh 调用一次</div>
            </div>
          )}
        </div>
      )}

      {/* ═══ 任务详情弹窗（居中弹出，非侧边栏）═══ */}
      {selected && selTask && (
        <div onClick={closeDetail} className="modal-bg audit-modal-bg"
          style={{ alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div onClick={e => e.stopPropagation()} className="audit-modal-panel"
            style={{
              width: 720, maxWidth: '95vw', maxHeight: '85vh',
              background: 'var(--panel)', borderRadius: 16,
              border: '1px solid var(--line)',
              overflow: 'hidden', display: 'flex', flexDirection: 'column',
              boxShadow: '0 8px 48px rgba(0,0,0,0.4)',
            }}>
            {/* ── 弹窗头部 ── */}
            <div style={{ position: 'sticky', top: 0, zIndex: 10, background: 'var(--panel)',
              borderBottom: '1px solid var(--line)', padding: '16px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
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
                <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                  <button onClick={() => { closeDetail(); setModalTaskId(selected.id); }} className="btn btn-g">
                    📋 看板
                  </button>
                  <button onClick={closeDetail} className="modal-close" style={{ position: 'static' }}>✕</button>
                </div>
              </div>

              {/* 子 Tab 切换 */}
              <div className="audit-inner-tabs" style={{ marginTop: 12, marginBottom: 0 }}>
                {([
                  ['flow', '📜 流转记录', selTask.flow_log?.length || 0],
                  ['violation', '🚨 违规记录', selViolations.length],
                  ['notif', '📢 通报记录', selNotifs.length],
                  ['progress', '📊 进展日志', activityData?.activity?.length || 0],
                ] as const).map(([k, label, cnt]) => (
                  <button key={k} className={`audit-inner-tab ${detailTab === k ? 'active' : ''}`}
                    onClick={() => { setDetailTab(k); setModalNotifCat('all'); }}>
                    {label}{cnt > 0 && <span className="tbadge">{cnt}</span>}
                  </button>
                ))}
              </div>
            </div>

            {/* ── 弹窗内容区 ── */}
            <div className="modal-body ag-scroll" style={{ padding: '16px 20px', overflowY: 'auto', flex: 1 }}>
              {detailTab === 'flow' && <FlowTimelinePanel task={selTask} />}
              {detailTab === 'violation' && <ViolationPanel violations={selViolations} />}
              {detailTab === 'notif' && (
                <div>
                  {/* 通报记录头部筛选（与主界面风格一致） */}
                  <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                    {NOTIF_CATS.map(c => {
                      const cnt = selNotifCounts[c.key] || 0;
                      if (c.key !== 'all' && cnt === 0) return null;
                      const meta = NOTIF_META[c.key as string];
                      return (
                        <span key={c.key}
                          className={`audit-pill ${modalNotifCat === c.key ? 'active' : ''}`}
                          onClick={() => setModalNotifCat(c.key)}>
                          {meta?.icon || '📋'} {c.key === 'all' ? '全部' : c.key} {cnt > 0 ? `(${cnt})` : ''}
                        </span>
                      );
                    })}
                  </div>
                  <NotifPanel notifs={selFilteredNotifs} />
                </div>
              )}
              {detailTab === 'progress' && <ProgressPanel activity={activityData} loading={activityLoading}
                progressLog={selTask.activity || []} taskNow={selTask.now} taskTodos={selTask.todos} />}
            </div>
          </div>
        </div>
      )}

      {/* ═══ 弹窗 — 无任务数据时的兜底（归档任务）═══ */}
      {selected && !selTask && (
        <div onClick={closeDetail} className="modal-bg audit-modal-bg"
          style={{ alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div onClick={e => e.stopPropagation()} className="audit-modal-panel"
            style={{
              width: 720, maxWidth: '95vw', maxHeight: '85vh',
              background: 'var(--panel)', borderRadius: 16,
              border: '1px solid var(--line)',
              overflow: 'hidden', display: 'flex', flexDirection: 'column',
              boxShadow: '0 8px 48px rgba(0,0,0,0.4)',
              padding: 20,
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
                archivedNotifs={archivedNotifs} />
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
   子组件
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
function ArchiveDetailFallback({ taskId, archivedViols, archivedNotifs }: {
  taskId: string; archivedViols: AuditViolation[]; archivedNotifs: AuditNotification[];
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
