/**
 * AuditPanelEnhanced.tsx — 流程监察面板（增强版）
 *
 * 相比原版 AuditPanel.tsx 的改进：
 * 1. 任务卡片墙：横向滚动固定大小卡片，展示任务名称、流转步数、违规标记
 * 2. 详情滑出面板：点击卡片展开流转时间线、违规记录、通报记录、进展日志
 * 3. 归档任务回溯：归档任务可查看完整流转日志和违规/通报历史
 *
 * 接入方式（仅修改 App.tsx 一行导入即可替换原面板）：
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
const SCROLLBAR_CSS = `.ag-scroll::-webkit-scrollbar{height:5px}.ag-scroll::-webkit-scrollbar-track{background:transparent}.ag-scroll::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}.ag-scroll{scrollbar-width:thin;scrollbar-color:var(--line) transparent}`;

/* ═══════════════════════════════════════════════════════════════════════
   主组件
   ═══════════════════════════════════════════════════════════════════════ */

export default function AuditPanelEnhanced() {
  const auditData = useStore((s) => s.auditData);
  const liveStatus = useStore((s) => s.liveStatus);
  const loadAudit = useStore((s) => s.loadAudit);
  const setModalTaskId = useStore((s) => s.setModalTaskId);

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

  // ── 卡片筛选 ──
  const [cardFilter, setCardFilter] = useState<'all' | 'violated' | 'clean'>('all');
  const filteredWatched = useMemo(() => {
    if (cardFilter === 'all') return watchedTasks;
    const vIds = new Set(violations.filter(v => watchedTasks.some(w => w.task_id === v.task_id)).map(v => v.task_id));
    return watchedTasks.filter(w => cardFilter === 'violated' ? vIds.has(w.task_id) : !vIds.has(w.task_id));
  }, [watchedTasks, violations, cardFilter]);

  // ── 通知分类 ──
  const [notifCat, setNotifCat] = useState('all');
  const [notifCollapsed, setNotifCollapsed] = useState(false);
  const recentNotifs = useMemo(() => {
    const base = notifications.slice(-60).reverse();
    return notifCat === 'all' ? base : base.filter(n => n.type === notifCat);
  }, [notifications, notifCat]);
  const notifCounts = useMemo(() => {
    const c: Record<string, number> = { all: notifications.length };
    for (const n of notifications) c[n.type] = (c[n.type] || 0) + 1;
    return c;
  }, [notifications]);

  // ── 违规分组 ──
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

  // ── 归档区域折叠 ──
  const [archiveCollapsed, setArchiveCollapsed] = useState(false);

  return (
    <div>
      <style>{SCROLLBAR_CSS}</style>

      {/* ═══ Header ═══ */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>🛡️ 流程监察</div>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>监督三省六部任务流转完整性，检测越权、跳步、断链</div>
        </div>
        <button className="btn btn-g" onClick={loadAudit} style={{ fontSize: 12, padding: '6px 14px' }}>⟳ 刷新</button>
      </div>

      {/* ═══ 状态卡片 ═══ */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { icon: isRunning && !isStale ? '🟢' : isRunning && isStale ? '🟡' : '🔴',
            label: '监察状态', value: isRunning && !isStale ? '运行中' : isRunning && isStale ? '可能停止' : '未启动',
            sub: lastCheckAgo ? `最后检查: ${lastCheckAgo}` : '暂无数据' },
          { icon: '📊', label: '累计检查', value: `${checkCount} 次`, sub: `发现 ${totalViolations} 项违规` },
          { icon: '👁️', label: '正在监察', value: `${watchedCount} 个任务`, sub: watchedCount > 0 ? '实时监控旨意任务' : '当前无活跃旨意' },
          { icon: '📢', label: '通报记录', value: `${notifications.length} 条`, sub: notifications.length > 0 ? '含越权通报+断链唤醒' : '暂无通报' },
        ].map((s, i) => (
          <div key={i} style={{ padding: 14, borderRadius: 10, background: 'var(--panel2)', border: '1px solid var(--line)' }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>{s.icon} {s.label}</div>
            <div style={{ fontSize: 18, fontWeight: 800 }}>{s.value}</div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* ═══ 任务卡片墙（横向滚动）═══ */}
      <Section title={`👁️ 正在监察的任务 (${watchedCount})`}
        extra={
          <div style={{ display: 'flex', gap: 6 }}>
            {([['all', '全部'], ['violated', '有违规'], ['clean', '正常']] as const).map(([k, l]) => (
              <button key={k} onClick={() => setCardFilter(k)} style={{
                fontSize: 11, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
                background: cardFilter === k ? '#6a9eff22' : 'var(--panel2)',
                color: cardFilter === k ? '#6a9eff' : 'var(--muted)',
                border: `1px solid ${cardFilter === k ? '#6a9eff44' : 'var(--line)'}`,
                fontWeight: cardFilter === k ? 600 : 400,
              }}>{l}</button>
            ))}
          </div>
        }
      >
        {filteredWatched.length === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>当前没有活跃旨意任务，监察处于待命状态</div>
        ) : (
          <div className="ag-scroll" style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 6 }}>
            {filteredWatched.map(w => {
              const vCount = violations.filter(v => v.task_id === w.task_id).length;
              const task = taskMap.get(w.task_id);
              return (
                <div key={w.task_id} onClick={() => openDetail(w.task_id)}
                  style={{
                    minWidth: 300, maxWidth: 300, flexShrink: 0,
                    padding: 14, borderRadius: 10, cursor: 'pointer',
                    background: 'var(--panel2)', border: `1px solid ${vCount > 0 ? '#ff527044' : 'var(--line)'}`,
                    borderLeft: `4px solid ${vCount > 0 ? '#ff5270' : '#2ecc8a'}`,
                    transition: 'box-shadow 0.2s',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = '0 4px 20px rgba(0,0,0,0.15)'; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = 'none'; }}
                >
                  {/* 任务 ID + 标题 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                      background: '#6a9eff22', color: '#6a9eff', whiteSpace: 'nowrap' }}>{w.task_id}</span>
                    {vCount > 0 && (
                      <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3,
                        background: '#ff527022', color: '#ff5270', fontWeight: 600 }}>⚠ {vCount}违规</span>
                    )}
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, lineHeight: 1.4,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{w.title}</div>

                  {/* 状态 + 部门 */}
                  <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                    {(() => { const s = STATE_S[w.state]; return s ? (
                      <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                        background: s.bg, color: s.color }}>{s.label}</span>
                    ) : null; })()}
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4,
                      background: (DEPT_COLOR[w.org] || '#888') + '18',
                      color: DEPT_COLOR[w.org] || '#888' }}>{w.org || '—'}</span>
                  </div>

                  {/* 进展摘要 */}
                  {(task?.now && task.now !== '-') && (
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, lineHeight: 1.5,
                      overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                      {task.now}
                    </div>
                  )}

                  {/* 底部信息 */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    fontSize: 11, color: 'var(--muted)', borderTop: '1px solid var(--line)', paddingTop: 8, marginTop: 'auto' }}>
                    <span>流转 {w.flow_count} 步</span>
                    <span>🔑 {w.session_key_count ?? (w.session_keys ? Object.keys(w.session_keys).length : 0)} 会话</span>
                    <span>{task?.updatedAt ? timeAgo(task.updatedAt) : '—'}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {/* ═══ 通报记录 ═══ */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, borderBottom: '1px solid var(--line)', paddingBottom: 6,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>📢 通报记录 ({recentNotifs.length})</span>
          <button onClick={() => setNotifCollapsed(!notifCollapsed)} style={{
            fontSize: 11, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
            background: 'var(--panel2)', color: 'var(--text)', border: '1px solid var(--line)',
          }}>{notifCollapsed ? '▸ 展开' : '▾ 折叠'}</button>
        </div>
        {!notifCollapsed && (<>
          <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
            {NOTIF_CATS.map(c => {
              const cnt = notifCounts[c.key] || 0;
              if (c.key !== 'all' && cnt === 0) return null;
              const meta = NOTIF_META[c.key as string];
              return (
                <button key={c.key} onClick={() => setNotifCat(c.key)} style={{
                  fontSize: 11, padding: '4px 10px', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap',
                  background: notifCat === c.key ? (meta?.color || '#6a9eff') + '22' : 'var(--panel2)',
                  color: notifCat === c.key ? (meta?.color || '#6a9eff') : 'var(--muted)',
                  border: `1px solid ${notifCat === c.key ? (meta?.color || '#6a9eff') + '44' : 'var(--line)'}`,
                  fontWeight: notifCat === c.key ? 600 : 400,
                }}>{meta?.icon || '📋'} {c.key === 'all' ? '全部' : c.key} {cnt > 0 ? `(${cnt})` : ''}</button>
              );
            })}
          </div>
          {recentNotifs.length === 0 ? (
            <div className="mb-empty" style={{ padding: 20 }}>{isRunning ? '✅ 暂无通报，所有流程正常' : '暂无监察数据'}</div>
          ) : (
            <div className="ag-scroll" style={{ maxHeight: 440, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, paddingRight: 4 }}>
              {recentNotifs.map((n, i) => <NotifCard key={`${n.sent_at}-${i}`} n={n} onClick={() => {
                const tid = n.task_id || (n.task_ids || [])[0];
                if (tid) openDetail(tid);
              }} />)}
            </div>
          )}
        </>)}
      </div>

      {/* ═══ 违规记录（按任务分组）═══ */}
      <Section title={`🚨 违规记录 (${activeViols.length}条活跃${resolvedViols.length > 0 ? `，${resolvedViols.length}条已解决` : ''}，${violsByTask.size}个任务)`}
        extra={
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button className={`btn ${showResolved ? 'btn-g' : ''}`} onClick={() => setShowResolved(!showResolved)} style={{
              fontSize: 11, padding: '4px 12px', borderRadius: 4,
              border: showResolved ? '1px solid var(--line)' : '1px solid transparent',
              opacity: showResolved ? 1 : 0.6, cursor: 'pointer',
            }}>{showResolved ? '✅ 显示全部（含已解决）' : '🔍 仅显示活跃违规'}</button>
          </div>
        }
      >
        {displayViols.length === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>{isRunning ? '✅ 所有任务流程正常，暂无违规' : '暂无监察数据'}</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {Array.from(violsByTask.entries()).map(([tid, vs]) => {
              const isResolved = !watchedTasks.some(w => w.task_id === tid);
              const task = taskMap.get(tid);
              return (
                <div key={tid} onClick={() => openDetail(tid)}
                  style={{ borderRadius: 10, background: isResolved ? 'var(--panel1)' : 'var(--panel2)',
                    border: '1px solid var(--line)', overflow: 'hidden', opacity: isResolved ? 0.55 : 1,
                    cursor: 'pointer', transition: 'box-shadow 0.2s',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = '0 2px 12px rgba(0,0,0,0.1)'; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = 'none'; }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px' }}>
                    <span style={{ fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
                      background: '#6a9eff18', color: '#6a9eff' }}>{tid}</span>
                    <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {task?.title || vs[0]?.title || tid}</span>
                    {isResolved && <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4,
                      background: '#2ecc8a18', color: '#2ecc8a' }}>✓ 已解决</span>}
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4,
                      background: vs.length > 0 ? '#ff527018' : '#2ecc8a18',
                      color: vs.length > 0 ? '#ff5270' : '#2ecc8a' }}>{vs.length} 条违规</span>
                  </div>
                  <div style={{ maxHeight: 0, overflow: 'hidden' }} />
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {/* ═══ 归档任务回溯 ═══ */}
      <Section title={`📦 归档任务回溯 (${archivedTasks.length})`}
        extra={
          <button onClick={() => setArchiveCollapsed(!archiveCollapsed)} style={{
            fontSize: 11, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
            background: 'var(--panel2)', color: 'var(--text)', border: '1px solid var(--line)',
          }}>{archiveCollapsed ? '▸ 展开' : '▾ 折叠'}</button>
        }
      >
        {!archiveCollapsed && (archivedTasks.length === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>暂无归档任务</div>
        ) : (
          <div className="ag-scroll" style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 6 }}>
            {archivedTasks.map(t => {
              const st = STATE_S[t.state] || STATE_S.Done;
              const avCount = archivedViolations.filter(v => v.task_id === t.id).length;
              const flowLog = t.flow_log || [];
              const lastFlow = flowLog[flowLog.length - 1];
              return (
                <div key={t.id} onClick={() => openDetail(t.id, true)}
                  style={{
                    minWidth: 300, maxWidth: 300, flexShrink: 0,
                    padding: 14, borderRadius: 10, cursor: 'pointer',
                    background: 'var(--panel2)', border: `1px solid ${avCount > 0 ? '#ff527044' : 'var(--line)'}`,
                    borderLeft: `4px solid ${t.state === 'Cancelled' ? '#888' : '#2ecc8a'}`,
                    transition: 'box-shadow 0.2s',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = '0 4px 20px rgba(0,0,0,0.15)'; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = 'none'; }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                      background: '#88888822', color: '#888', whiteSpace: 'nowrap' }}>{t.id}</span>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                      background: st.bg, color: st.color }}>{st.label}</span>
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, lineHeight: 1.4,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title}</div>

                  {/* 流转摘要 */}
                  <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, lineHeight: 1.5 }}>
                    流转 {flowLog.length} 步
                    {avCount > 0 && <span style={{ color: '#ff5270', marginLeft: 8 }}>· {avCount} 条违规</span>}
                  </div>
                  {t.output && (
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, lineHeight: 1.5,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      📎 {t.output}
                    </div>
                  )}

                  {/* 最后流转 */}
                  {lastFlow && (
                    <div style={{ fontSize: 11, color: 'var(--muted)', borderTop: '1px solid var(--line)', paddingTop: 8 }}>
                      最后: {lastFlow.from} → {lastFlow.to} {timeAgo(lastFlow.at)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </Section>

      {/* ═══ 说明 ═══ */}
      <div style={{ marginTop: 20, padding: 16, borderRadius: 10, background: 'var(--panel2)',
        border: '1px solid var(--line)', fontSize: 12, color: 'var(--muted)', lineHeight: 1.8 }}>
        <div style={{ fontWeight: 700, marginBottom: 8, color: 'var(--text)' }}>📋 监察说明</div>
        <div><b>监察范围：</b>仅监察 JJC- 开头的旨意任务，不监察对话</div>
        <div><b>完整流程：</b>皇上 → 太子 → 中书省 → 门下省 → 中书省 → 尚书省 → 六部 → 尚书省 → 中书省 → 太子 → 皇上</div>
        <div><b>越权调用：</b>from→to 不在合法流转对表内，监察会通过会话通知太子</div>
        <div><b>流程跳步：</b>缺少必要环节，仅记录不通知</div>
        <div><b>断链超时：</b>某部门超时未回应，监察自动唤醒 + 通知上级</div>
        <div><b>自动归档：</b>任务完成超过 5 分钟自动归档，可在上方「归档任务回溯」中查看日志</div>
        <div><b>运行方式：</b>pipeline_watchdog.py 每 60 秒由 run_loop.sh 调用一次</div>
      </div>

      {/* ═══ 详情面板（滑出覆盖层）═══ */}
      {selected && selTask && (
        <div onClick={closeDetail} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 900, animation: 'fadeIn 0.2s',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, width: 540, maxWidth: '90vw',
            background: 'var(--bg, #1a1a2e)', borderLeft: '1px solid var(--line)',
            overflowY: 'auto', animation: 'slideIn 0.25s ease-out',
            boxShadow: '-4px 0 24px rgba(0,0,0,0.3)',
          }}>
            <style>{`@keyframes fadeIn{from{opacity:0}to{opacity:1}}@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>

            {/* 面板头部 */}
            <div style={{ position: 'sticky', top: 0, zIndex: 10, background: 'var(--bg, #1a1a2e)',
              borderBottom: '1px solid var(--line)', padding: '16px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
                      background: '#6a9eff22', color: '#6a9eff' }}>{selected.id}</span>
                    {(() => { const s = STATE_S[selTask.state]; return s ? (
                      <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                        background: s.bg, color: s.color }}>{s.label}</span>
                    ) : null; })()}
                    {selTask.org && (
                      <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4,
                        background: (DEPT_COLOR[selTask.org] || '#888') + '18',
                        color: DEPT_COLOR[selTask.org] || '#888' }}>{selTask.org}</span>
                    )}
                  </div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{selTask.title}</div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={() => { closeDetail(); setModalTaskId(selected.id); }} className="btn"
                    style={{ fontSize: 11, padding: '4px 10px' }}>📋 看板</button>
                  <button onClick={closeDetail} style={{ fontSize: 18, cursor: 'pointer', background: 'none',
                    border: 'none', color: 'var(--muted)', padding: '0 4px', lineHeight: 1 }}>✕</button>
                </div>
              </div>

              {/* 标签切换 */}
              <div style={{ display: 'flex', gap: 0, marginTop: 12 }}>
                {([
                  ['flow', '📜 流转记录', selTask.flow_log?.length || 0],
                  ['violation', '🚨 违规记录', selViolations.length],
                  ['notif', '📢 通报记录', selNotifs.length],
                  ['progress', '📊 进展日志', activityData?.activity?.length || 0],
                ] as const).map(([k, label, cnt]) => (
                  <button key={k} onClick={() => setDetailTab(k)} style={{
                    fontSize: 12, padding: '6px 16px', cursor: 'pointer', fontWeight: 600,
                    background: detailTab === k ? '#6a9eff18' : 'transparent',
                    color: detailTab === k ? '#6a9eff' : 'var(--muted)',
                    borderBottom: detailTab === k ? '2px solid #6a9eff' : '2px solid transparent',
                    border: 'none', borderRadius: '6px 6px 0 0', transition: 'all 0.15s',
                  }}>{label}{cnt > 0 && <span style={{ marginLeft: 4, fontSize: 10, opacity: 0.7 }}>({cnt})</span>}</button>
                ))}
              </div>
            </div>

            {/* 标签内容 */}
            <div style={{ padding: '16px 20px' }}>
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
        <div onClick={closeDetail} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 900,
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, width: 540, maxWidth: '90vw',
            background: 'var(--bg, #1a1a2e)', borderLeft: '1px solid var(--line)',
            overflowY: 'auto', padding: 20, animation: 'slideIn 0.25s ease-out',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div>
                <span style={{ fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
                  background: '#6a9eff22', color: '#6a9eff' }}>{selected.id}</span>
                <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 8 }}>
                  {selected.archived ? '(归档任务)' : ''}</span>
              </div>
              <button onClick={closeDetail} style={{ fontSize: 18, cursor: 'pointer', background: 'none',
                border: 'none', color: 'var(--muted)' }}>✕</button>
            </div>
            {selected.archived ? (
              <ArchiveDetailFallback taskId={selected.id} archivedViols={archivedViolations}
                archivedNotifs={archivedNotifs} openDetail={openDetail} closeDetail={closeDetail} />
            ) : (
              <div className="mb-empty" style={{ padding: 40 }}>任务数据未找到，请刷新后重试</div>
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

function Section({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, borderBottom: '1px solid var(--line)', paddingBottom: 6,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span>{title}</span>
        {extra}
      </div>
      {children}
    </div>
  );
}

/* ── 通知卡片 ── */
function NotifCard({ n, onClick }: { n: AuditNotification; onClick?: () => void }) {
  const meta = NOTIF_META[n.type] || { icon: '📢', color: '#6a9eff' };
  const sent = timeAgo(n.sent_at || (n as any).at || '');
  const [expanded, setExpanded] = useState(false);
  return (
    <div onClick={() => { if (n.detail) setExpanded(!expanded); if (onClick) onClick(); }}
      style={{ padding: '8px 14px', borderRadius: 8, background: n.status === 'sent' ? 'var(--panel2)' : '#ff527008',
        border: `1px solid ${n.status === 'sent' ? 'var(--line)' : '#ff527033'}`,
        borderLeft: `3px solid ${meta.color}`, cursor: 'pointer', flexShrink: 0,
        transition: 'background 0.15s' }}
      onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.background = 'var(--panel1)'; }}
      onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = n.status === 'sent' ? 'var(--panel2)' : '#ff527008'; }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13 }}>{meta.icon}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: meta.color, padding: '1px 6px', borderRadius: 4,
          background: `${meta.color}15` }}>{n.type}</span>
        {n.to && <span style={{ fontSize: 12, fontWeight: 600 }}>→ {n.to}</span>}
        {(n.task_id || n.task_ids) && (
          <span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: '#6a9eff15', color: '#6a9eff' }}>
            {n.task_ids ? n.task_ids.join(', ') : n.task_id}</span>
        )}
        <span style={{ fontSize: 11, color: 'var(--muted)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {n.summary || n.detail?.substring(0, 80) || ''}</span>
        <span style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{n.status === 'sent' ? '✅' : '❌'}</span>
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
  if (flowLog.length === 0) return <div className="mb-empty" style={{ padding: 20 }}>暂无流转记录</div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {flowLog.map((f, i) => {
        const fromColor = DEPT_COLOR[f.from] || '#888';
        const toColor = DEPT_COLOR[f.to] || '#888';
        const isLast = i === flowLog.length - 1;
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
          <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
            {/* 时间列 */}
            <div style={{ width: 56, flexShrink: 0, textAlign: 'right', paddingTop: 2 }}>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>{t}</div>
              {i === 0 && <div style={{ fontSize: 10, color: 'var(--muted)', opacity: 0.6 }}>{dateStr}</div>}
            </div>
            {/* 时间线圆点 + 连接线 */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 24, flexShrink: 0 }}>
              <div style={{
                width: 12, height: 12, borderRadius: '50%',
                background: toColor, marginTop: 3,
                boxShadow: `0 0 6px ${toColor}44`,
                flexShrink: 0,
              }} />
              {!isLast && <div style={{ width: 2, flex: 1, minHeight: 16, background: 'var(--line)', marginTop: 2, marginBottom: 2 }} />}
            </div>
            {/* 内容 */}
            <div style={{ flex: 1, paddingBottom: isLast ? 0 : 14, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: fromColor }}>{f.from}</span>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>→</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: toColor }}>{f.to}</span>
              </div>
              {f.remark && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3, lineHeight: 1.5 }}>
                {f.remark.replace(/^🧭\s*/, '')}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── 违规详情面板 ── */
function ViolationPanel({ violations }: { violations: AuditViolation[] }) {
  if (violations.length === 0) return <div className="mb-empty" style={{ padding: 20 }}>✅ 该任务无违规记录</div>;
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
  if (notifs.length === 0) return <div className="mb-empty" style={{ padding: 20 }}>该任务无通报记录</div>;
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
  if (loading) return <div className="mb-empty" style={{ padding: 20 }}>加载中...</div>;

  const entries = activity?.activity || progressLog || [];
  if (entries.length === 0 && !taskNow) return <div className="mb-empty" style={{ padding: 20 }}>暂无进展记录</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* 阶段耗时 */}
      {activity?.phaseDurations && activity.phaseDurations.length > 0 && (
        <div style={{ padding: 12, borderRadius: 8, background: 'var(--panel2)', border: '1px solid var(--line)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>⏱️ 阶段耗时</div>
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
        <div style={{ padding: 12, borderRadius: 8, background: '#6a9eff10', border: '1px solid #6a9eff33' }}>
          <div style={{ fontSize: 11, color: '#6a9eff', fontWeight: 600, marginBottom: 4 }}>📍 当前进展</div>
          <div style={{ fontSize: 12, color: 'var(--text)', lineHeight: 1.6 }}>{taskNow}</div>
        </div>
      )}

      {/* Todo 列表 */}
      {taskTodos && taskTodos.length > 0 && (
        <div style={{ padding: 12, borderRadius: 8, background: 'var(--panel2)', border: '1px solid var(--line)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>📝 任务清单</div>
          {taskTodos.map((todo: any, i: number) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, padding: '3px 0',
              opacity: todo.status === 'completed' ? 0.5 : 1, textDecoration: todo.status === 'completed' ? 'line-through' : 'none' }}>
              <span>{todo.status === 'completed' ? '✅' : todo.status === 'in-progress' ? '🔄' : '⬜'}</span>
              <span style={{ color: 'var(--text)' }}>{todo.title}</span>
            </div>
          ))}
        </div>
      )}

      {/* 进展日志列表 */}
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>📋 进展日志 ({entries.length})</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {entries.slice(-50).reverse().map((e: any, i: number) => {
          const at = e.at ? timeAgo(typeof e.at === 'number' ? new Date(e.at * 1000).toISOString() : e.at) : '';
          const agent = e.agent || e.agentLabel || '';
          const text = e.text || e.remark || '';
          const tool = e.tool || e.tools?.[0]?.name || '';
          return (
            <div key={i} style={{ padding: '8px 10px', borderRadius: 6, background: 'var(--panel2)', border: '1px solid var(--line)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: text || tool ? 4 : 0 }}>
                {agent && (
                  <span style={{ fontSize: 11, fontWeight: 600, padding: '1px 6px', borderRadius: 3,
                    background: (DEPT_COLOR[agent] || '#888') + '18', color: DEPT_COLOR[agent] || '#888' }}>{agent}</span>
                )}
                {tool && <span style={{ fontSize: 10, color: '#a07aff' }}>🔧 {tool}</span>}
                <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>{at}</span>
              </div>
              {text && <div style={{ fontSize: 11, color: 'var(--text)', lineHeight: 1.5 }}>{text}</div>}
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
      <div style={{ display: 'flex', gap: 0, marginBottom: 16 }}>
        <button onClick={() => setTab('violation')} style={{
          fontSize: 12, padding: '6px 16px', cursor: 'pointer', fontWeight: 600,
          background: tab === 'violation' ? '#6a9eff18' : 'transparent',
          color: tab === 'violation' ? '#6a9eff' : 'var(--muted)',
          borderBottom: tab === 'violation' ? '2px solid #6a9eff' : '2px solid transparent',
          border: 'none', borderRadius: '6px 6px 0 0',
        }}>🚨 违规 ({viols.length})</button>
        <button onClick={() => setTab('notif')} style={{
          fontSize: 12, padding: '6px 16px', cursor: 'pointer', fontWeight: 600,
          background: tab === 'notif' ? '#6a9eff18' : 'transparent',
          color: tab === 'notif' ? '#6a9eff' : 'var(--muted)',
          borderBottom: tab === 'notif' ? '2px solid #6a9eff' : '2px solid transparent',
          border: 'none', borderRadius: '6px 6px 0 0',
        }}>📢 通报 ({notifs.length})</button>
      </div>
      {tab === 'violation' && <ViolationPanel violations={viols} />}
      {tab === 'notif' && <NotifPanel notifs={notifs} />}
    </div>
  );
}
