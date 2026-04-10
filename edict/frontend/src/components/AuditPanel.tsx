import { useEffect, useRef, useState, useMemo } from 'react';
import { useStore, timeAgo } from '../store';
import { api } from '../api';
import type { AuditViolation, WatchedTask, AuditNotification } from '../api';

/** 违规类型对应的样式 */
const TYPE_META: Record<string, { icon: string; color: string; bg: string }> = {
  '越权调用': { icon: '🚫', color: '#ff5270', bg: '#ff527018' },
  '流程跳步': { icon: '⚡', color: '#e8a040', bg: '#e8a04018' },
  '断链超时': { icon: '🔗', color: '#6a9eff', bg: '#6a9eff18' },
  '直接执行越权': { icon: '⛔', color: '#ff2d55', bg: '#ff2d5518' },
  '极端停滞': { icon: '⏰', color: '#ff0040', bg: '#ff004018' },
  '未完成回奏': { icon: '🛑', color: '#ff6b35', bg: '#ff6b3518' },
  '会话未注册': { icon: '🔑', color: '#a07aff', bg: '#a07aff18' },
  '会话通信过多': { icon: '🔄', color: '#e8a040', bg: '#e8a04018' },
  '会话可疑': { icon: '⚠️', color: '#e8a040', bg: '#e8a04018' },
  '会话违规': { icon: '🚨', color: '#ff5270', bg: '#ff527018' },
};

/** 通知类型对应的样式 */
const NOTIFY_TYPE_META: Record<string, { icon: string; color: string }> = {
  '越权通报': { icon: '🚨', color: '#ff5270' },
  '跳步通报': { icon: '⚡', color: '#e8a040' },
  '断链唤醒': { icon: '🔔', color: '#e8a040' },
  '断链通知': { icon: '📡', color: '#6a9eff' },
  '会话警告': { icon: '🔑', color: '#a07aff' },
  '归档': { icon: '📦', color: '#888888' },
  '巡检': { icon: '🔍', color: '#4ecdc4' },
  // 兼容旧格式后端 type 值
  '唤醒': { icon: '🔔', color: '#e8a040' },
  '通知': { icon: '📡', color: '#6a9eff' },
  '违规': { icon: '🚨', color: '#ff5270' },
};

/** 通知分类定义 */
const NOTIFY_CATEGORIES = [
  { key: 'all', label: '全部', icon: '📋' },
  { key: '越权通报', label: '越权通报', icon: '🚨' },
  { key: '跳步通报', label: '跳步通报', icon: '⚡' },
  { key: '断链唤醒', label: '断链唤醒', icon: '🔔' },
  { key: '断链通知', label: '断链通知', icon: '📡' },
  { key: '会话警告', label: '会话警告', icon: '🔑' },
  { key: '归档', label: '归档', icon: '📦' },
  { key: '巡检', label: '巡检', icon: '🔍' },
] as const;

/** 任务状态对应标签 */
const STATE_LABEL: Record<string, string> = {
  Taizi: '太子分拣',
  Zhongshu: '中书起草',
  Menxia: '门下审议',
  Assigned: '尚书派发',
  Doing: '执行中',
  Review: '汇总审查',
  Pending: '待处理',
  Blocked: '阻塞',
};

export default function AuditPanel() {
  const auditData = useStore((s) => s.auditData);
  const loadAudit = useStore((s) => s.loadAudit);

  useEffect(() => {
    loadAudit();
  }, [loadAudit]);

  // 每 10 秒自动刷新监察数据
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    timerRef.current = setInterval(() => {
      loadAudit();
    }, 10000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [loadAudit]);

  const lastCheck = auditData?.last_check || '';
  const violations: AuditViolation[] = auditData?.violations || [];
  const watchedTasks: WatchedTask[] = auditData?.watched_tasks || [];
  const watchedCount = auditData?.watched_count || 0;
  const checkCount = auditData?.check_count || 0;
  const totalViolations = auditData?.total_violations || 0;
  const notifications: AuditNotification[] = auditData?.notifications || [];

  // 监察运行状态判断
  const isRunning = !!lastCheck;
  const lastCheckAgo = timeAgo(lastCheck);
  const isStale = (() => {
    if (!lastCheck) return true;
    try {
      const d = new Date(lastCheck.includes('T') ? lastCheck : lastCheck.replace(' ', 'T') + 'Z');
      return Date.now() - d.getTime() > 3 * 60 * 1000;
    } catch {
      return true;
    }
  })();

  // 区分活跃违规和已解决违规
  const watchedTaskIds = new Set(watchedTasks.map(t => t.task_id));
  const activeViolations = violations.filter(v => watchedTaskIds.has(v.task_id));
  const resolvedViolations = violations.filter(v => !watchedTaskIds.has(v.task_id));
  const [showResolved, setShowResolved] = useState(false);

  const displayViolations = showResolved ? violations : activeViolations;

  // 按任务 ID 分组违规记录
  const violationsByTask = useMemo(() => {
    const map = new Map<string, AuditViolation[]>();
    const source = displayViolations;
    const recent = source.slice(-200).reverse();
    for (const v of recent) {
      const key = v.task_id;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(v);
    }
    return map;
  }, [displayViolations]);

  // 通知分类筛选状态
  const [notifCategory, setNotifCategory] = useState<string>('all');
  const [notifCollapsed, setNotifCollapsed] = useState(false);

  // 通知记录倒序 + 分类过滤
  const recentNotifications = useMemo(() => {
    const base = notifications.slice(-50).reverse();
    if (notifCategory === 'all') return base;
    return base.filter(n => n.type === notifCategory);
  }, [notifications, notifCategory]);

  // 统计各分类通知数量
  const notifCounts = useMemo(() => {
    const counts: Record<string, number> = { all: notifications.length };
    for (const n of notifications) {
      counts[n.type] = (counts[n.type] || 0) + 1;
    }
    return counts;
  }, [notifications]);

  return (
    <div>
      {/* ── Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>
            🛡️ 流程监察
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>
            监督三省六部任务流转完整性，检测越权、跳步、断链（仅监察 JJC- 旨意任务）
          </div>
        </div>
        <button className="btn btn-g" onClick={loadAudit} style={{ fontSize: 12, padding: '6px 14px' }}>
          ⟳ 刷新
        </button>
      </div>

      {/* ── 状态卡片 ── */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20,
      }}>
        <StatCard
          icon={isRunning && !isStale ? '🟢' : isRunning && isStale ? '🟡' : '🔴'}
          label="监察状态"
          value={isRunning && !isStale ? '运行中' : isRunning && isStale ? '可能停止' : '未启动'}
          sub={lastCheckAgo ? `最后检查: ${lastCheckAgo}` : '暂无数据'}
        />
        <StatCard
          icon="📊"
          label="累计检查"
          value={`${checkCount} 次`}
          sub={`发现 ${totalViolations} 项违规`}
        />
        <StatCard
          icon="👁️"
          label="正在监察"
          value={`${watchedCount} 个任务`}
          sub={watchedCount > 0 ? '实时监控旨意任务' : '当前无活跃旨意'}
        />
        <StatCard
          icon="📢"
          label="通报记录"
          value={`${notifications.length} 条`}
          sub={notifications.length > 0 ? '含越权通报+断链唤醒' : '暂无通报'}
        />
      </div>

      {/* ── 正在监察的任务 ── */}
      <Section title={`👁️ 正在监察的任务 (${watchedCount})`}>
        {watchedTasks.length === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>
            当前没有活跃旨意任务，监察处于待命状态
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {watchedTasks.map((task) => (
              <WatchedTaskCard key={task.task_id} task={task} onUpdate={loadAudit} />
            ))}
          </div>
        )}
      </Section>

      {/* ── 通报记录（可折叠 + 分类 + 固定大小滑动）── */}
      <div style={{ marginBottom: 20 }}>
        <div style={{
          fontSize: 14, fontWeight: 700, marginBottom: 10,
          borderBottom: '1px solid var(--line)', paddingBottom: 6,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span>📢 通报记录 ({recentNotifications.length})</span>
          <button
            onClick={() => setNotifCollapsed(!notifCollapsed)}
            style={{
              fontSize: 11, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
              background: 'var(--panel2)', color: 'var(--text)',
              border: '1px solid var(--line)',
            }}
          >
            {notifCollapsed ? '▸ 展开' : '▾ 折叠'}
          </button>
        </div>

        {!notifCollapsed && (
          <>
            {/* 分类标签栏 */}
            <div style={{
              display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap',
            }}>
              {NOTIFY_CATEGORIES.map(cat => {
                const count = notifCounts[cat.key] || 0;
                if (cat.key !== 'all' && count === 0) return null;
                return (
                  <button
                    key={cat.key}
                    onClick={() => setNotifCategory(cat.key)}
                    style={{
                      fontSize: 11, padding: '4px 10px', borderRadius: 4,
                      cursor: 'pointer', whiteSpace: 'nowrap',
                      background: notifCategory === cat.key
                        ? (NOTIFY_TYPE_META[cat.key]?.color || '#6a9eff') + '22'
                        : 'var(--panel2)',
                      color: notifCategory === cat.key
                        ? (NOTIFY_TYPE_META[cat.key]?.color || '#6a9eff')
                        : 'var(--muted)',
                      border: `1px solid ${notifCategory === cat.key
                        ? (NOTIFY_TYPE_META[cat.key]?.color || '#6a9eff') + '44'
                        : 'var(--line)'}`,
                      fontWeight: notifCategory === cat.key ? 600 : 400,
                    }}
                  >
                    {cat.icon} {cat.label} {count > 0 ? `(${count})` : ''}
                  </button>
                );
              })}
            </div>

            {/* 通知列表（固定高度滚动） */}
            {recentNotifications.length === 0 ? (
              <div className="mb-empty" style={{ padding: 20 }}>
                {isRunning ? '✅ 暂无通报，所有流程正常' : '暂无监察数据'}
              </div>
            ) : (
              <div style={{
                maxHeight: 480,
                overflowY: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                paddingRight: 4,
              }}>
                {recentNotifications.map((n, i) => (
                  <NotificationCard key={`${n.sent_at}-${i}`} notification={n} />
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── 违规记录（按任务分组）── */}
      <Section title={`🚨 违规记录 (${activeViolations.length}条活跃${resolvedViolations.length > 0 ? `，${resolvedViolations.length}条已解决` : ''}，${violationsByTask.size}个任务)`}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <button
            className={`btn ${showResolved ? 'btn-g' : ''}`}
            onClick={() => setShowResolved(!showResolved)}
            style={{
              fontSize: 11, padding: '4px 12px', borderRadius: 4,
              border: showResolved ? '1px solid var(--line)' : '1px solid transparent',
              opacity: showResolved ? 1 : 0.6, cursor: 'pointer',
            }}
          >
            {showResolved ? '✅ 显示全部（含已解决）' : '🔍 仅显示活跃违规'}
          </button>
          {resolvedViolations.length > 0 && !showResolved && (
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>
              💡 {resolvedViolations.length} 条已解决违规已隐藏（任务已完成/归档，监察已自动清理）
            </span>
          )}
        </div>
        {violations.length === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>
            {isRunning ? '✅ 所有任务流程正常，暂无违规' : '暂无监察数据'}
          </div>
        ) : violationsByTask.size === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>
            {showResolved
              ? '暂无违规记录'
              : '✅ 当前所有活跃任务流程正常，无违规'
            }
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {Array.from(violationsByTask.entries()).map(([taskId, taskViolations]) => {
              const isResolved = !watchedTaskIds.has(taskId);
              return (
                <TaskViolationGroup
                  key={taskId}
                  taskId={taskId}
                  violations={taskViolations}
                  isResolved={isResolved}
                />
              );
            })}
          </div>
        )}
      </Section>

      {/* ── 说明 ── */}
      <div style={{
        marginTop: 20, padding: 16, borderRadius: 10,
        background: 'var(--panel2)', border: '1px solid var(--line)',
        fontSize: 12, color: 'var(--muted)', lineHeight: 1.8,
      }}>
        <div style={{ fontWeight: 700, marginBottom: 8, color: 'var(--text)' }}>📋 监察说明</div>
        <div><b>监察范围：</b>仅监察 JJC- 开头的旨意任务，不监察对话</div>
        <div><b>完整流程：</b>皇上 → 太子 → 中书省 → 门下省 → 中书省 → 尚书省 → 六部（具体部门）→ 尚书省 → 中书省 → 太子 → 皇上</div>
        <div><b>越权调用：</b>from→to 不在合法流转对表内（如太子→六部、尚书省使用「六部」泛称），监察会通过会话通知太子</div>
        <div><b>流程跳步：</b>缺少必要环节（如跳过门下省审议、缺少太子→皇上回奏），仅记录不通知</div>
        <div><b>未完成回奏：</b>旨意任务必须经过太子汇报皇上才能标记完成，未完成回奏的 Done 操作会被拒绝</div>
        <div><b>断链超时：</b>某部门 1 分钟内未回应，监察自动唤醒 + 通知上级</div>
        <div><b>会话未注册：</b>任务有跨部门通信但无 session_key 记录，说明 Agent 未使用 session-keys save，存在会话膨胀风险</div>
        <div><b>会话通信过多：</b>同一部门对通信次数超过正常阈值，说明可能未复用 session，反复 spawn 新会话</div>
        <div><b>自动归档：</b>任务完成超过 5 分钟自动归档，归档任务不在流程监察中显示</div>
        <div><b>运行方式：</b>pipeline_watchdog.py 每 60 秒由 run_loop.sh 调用一次</div>
      </div>
    </div>
  );
}


/* ── 子组件 ── */

function StatCard({ icon, label, value, sub }: { icon: string; label: string; value: string; sub: string }) {
  return (
    <div style={{
      padding: 14, borderRadius: 10,
      background: 'var(--panel2)', border: '1px solid var(--line)',
    }}>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>{icon} {label}</div>
      <div style={{ fontSize: 18, fontWeight: 800 }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{sub}</div>
    </div>
  );
}


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, borderBottom: '1px solid var(--line)', paddingBottom: 6 }}>
        {title}
      </div>
      {children}
    </div>
  );
}


function WatchedTaskCard({ task, onUpdate }: { task: WatchedTask; onUpdate: () => void }) {
  const [excluding, setExcluding] = useState(false);
  const [excludeMsg, setExcludeMsg] = useState('');
  const [showKeys, setShowKeys] = useState(false);
  const stateLabel = STATE_LABEL[task.state] || task.state;
  const sessionKeyCount = task.session_key_count ?? (task.session_keys ? Object.keys(task.session_keys).length : 0);
  const hasSessionKeys = sessionKeyCount > 0;

  const handleExclude = async () => {
    if (!confirm(`确认停止监察任务 ${task.task_id}？\n${task.title}`)) return;
    setExcluding(true);
    setExcludeMsg('');
    try {
      const res = await api.auditExclude(task.task_id, 'exclude');
      setExcludeMsg(res.ok ? '✅ 已停止' : `❌ ${res.error || '失败'}`);
      if (res.ok) onUpdate();
    } catch (e: any) {
      setExcludeMsg(`❌ ${e.message || '请求失败'}`);
    } finally {
      setExcluding(false);
      setTimeout(() => setExcludeMsg(''), 3000);
    }
  };

  return (
    <div style={{
      padding: '10px 14px',
      borderRadius: 8,
      background: 'var(--panel2)',
      border: '1px solid var(--line)',
    }}>
      {/* 主信息行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
          background: '#6a9eff22', color: '#6a9eff', whiteSpace: 'nowrap',
        }}>
          {task.task_id}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {task.title}
          </div>
        </div>
        <div style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 4,
          background: '#2ecc8a18', color: '#2ecc8a', whiteSpace: 'nowrap',
        }}>
          {task.org || stateLabel}
        </div>
        <div style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 4,
          background: '#a07aff18', color: '#a07aff', whiteSpace: 'nowrap',
        }}>
          {stateLabel}
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
          流转 {task.flow_count} 步
        </div>
      </div>

      {/* Session Keys 行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <button
          onClick={() => setShowKeys(!showKeys)}
          style={{
            fontSize: 11, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
            background: hasSessionKeys ? '#a07aff15' : '#ff527015',
            color: hasSessionKeys ? '#a07aff' : '#ff5270',
            border: `1px solid ${hasSessionKeys ? '#a07aff33' : '#ff527033'}`,
          }}
        >
          🔑 会话 {hasSessionKeys ? `${sessionKeyCount} 个` : '未注册'}
        </button>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2, marginLeft: 'auto' }}>
          <button
            onClick={handleExclude}
            disabled={excluding}
            style={{
              fontSize: 11, padding: '3px 10px', borderRadius: 4,
              background: '#ff527018', color: '#ff5270', border: '1px solid #ff527033',
              cursor: excluding ? 'wait' : 'pointer', whiteSpace: 'nowrap',
              opacity: excluding ? 0.5 : 1,
            }}
          >
            {excluding ? '...' : '✕ 停止监察'}
          </button>
          {excludeMsg && <span style={{ fontSize: 10, color: excludeMsg.startsWith('✅') ? '#2ecc8a' : '#ff5270' }}>{excludeMsg}</span>}
        </div>
      </div>

      {/* Session Keys 详情 */}
      {showKeys && (
        <div style={{
          marginTop: 8, padding: '8px 10px', borderRadius: 6,
          background: 'var(--panel1)', border: '1px solid var(--line)',
          fontSize: 11, lineHeight: 1.8,
        }}>
          {hasSessionKeys ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {task.session_keys && Object.entries(task.session_keys).map(([pair, info]) => (
                <div key={pair} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    padding: '1px 6px', borderRadius: 3,
                    background: '#2ecc8a15', color: '#2ecc8a', fontWeight: 600,
                  }}>
                    {pair}
                  </span>
                  <span style={{ color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 300 }}>
                    {info.sessionKey}
                  </span>
                  <span style={{ color: 'var(--muted)', fontSize: 10, marginLeft: 'auto' }}>
                    {info.savedAt ? new Date(info.savedAt.includes('T') ? info.savedAt : info.savedAt.replace(' ', 'T')).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: '#ff5270' }}>
              ⚠️ 该任务尚无已注册的 session key，Agent 间的跨部门通信可能每次都创建新会话。
              建议检查 Agent 是否遵守 session-keys save/send 协议。
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function NotificationCard({ notification }: { notification: AuditNotification }) {
  const meta = NOTIFY_TYPE_META[notification.type] || { icon: '📢', color: '#6a9eff' };
  const _sentAt = notification.sent_at || (notification as any).at || '';
  const sent = timeAgo(_sentAt);
  const isSent = notification.status === 'sent';
  const _to = notification.to || (notification as any).target || '';
  const _summary = notification.summary || notification.detail?.substring(0, 80) || '';
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{
      padding: '8px 14px', borderRadius: 8,
      background: isSent ? 'var(--panel2)' : '#ff527008',
      border: `1px solid ${isSent ? 'var(--line)' : '#ff527033'}`,
      borderLeft: `3px solid ${meta.color}`,
      cursor: notification.detail ? 'pointer' : 'default',
      flexShrink: 0,
    }}
      onClick={() => notification.detail && setExpanded(!expanded)}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13 }}>{meta.icon}</span>
        <span style={{
          fontSize: 11, fontWeight: 700, color: meta.color,
          padding: '1px 6px', borderRadius: 4, background: `${meta.color}15`,
        }}>
          {notification.type}
        </span>
        {_to && (
          <span style={{ fontSize: 12, fontWeight: 600 }}>
            → {_to}
          </span>
        )}
        {(notification.task_id || notification.task_ids) && (
          <span style={{
            fontSize: 11, padding: '2px 6px', borderRadius: 4,
            background: '#6a9eff15', color: '#6a9eff',
          }}>
            {notification.task_ids ? notification.task_ids.join(', ') : notification.task_id}
          </span>
        )}
        <span style={{ fontSize: 11, color: 'var(--muted)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {_summary}
        </span>
        <span style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
          {isSent ? '✅' : '❌'}
        </span>
        <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
          {sent}
        </span>
      </div>
      {expanded && notification.detail && (
        <div style={{
          marginTop: 6, fontSize: 11, color: 'var(--muted)',
          padding: '6px 10px', borderRadius: 4, background: 'var(--panel2)',
          wordBreak: 'break-all', lineHeight: 1.6,
        }}>
          {notification.detail}
        </div>
      )}
    </div>
  );
}


function TaskViolationGroup({ taskId, violations, isResolved }: { taskId: string; violations: AuditViolation[]; isResolved?: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  const title = violations[0]?.title || taskId;

  return (
    <div style={{
      borderRadius: 10, background: isResolved ? 'var(--panel1)' : 'var(--panel2)',
      border: `1px solid ${isResolved ? 'var(--line)' : 'var(--line)'}`,
      overflow: 'hidden', opacity: isResolved ? 0.55 : 1,
    }}>
      {/* 任务标题行（可点击折叠） */}
      <div
        onClick={() => setCollapsed(!collapsed)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
          cursor: 'pointer', borderBottom: collapsed ? 'none' : '1px solid var(--line)',
        }}
      >
        <span style={{ fontSize: 12 }}>{collapsed ? '▸' : '▾'}</span>
        <span style={{
          fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
          background: '#6a9eff18', color: '#6a9eff',
        }}>
          {taskId}
        </span>
        <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {title}
        </span>
        {isResolved && (
          <span style={{
            fontSize: 10, padding: '2px 6px', borderRadius: 4,
            background: '#2ecc8a18', color: '#2ecc8a',
          }}>
            ✓ 已解决
          </span>
        )}
        <span style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 4,
          background: violations.length > 0 ? '#ff527018' : '#2ecc8a18',
          color: violations.length > 0 ? '#ff5270' : '#2ecc8a',
        }}>
          {violations.length} 条违规
        </span>
      </div>

      {/* 违规列表（固定高度滚动） */}
      {!collapsed && (
        <div style={{
          maxHeight: 320, overflowY: 'auto', padding: '8px 14px',
          display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          {violations.map((v, i) => (
            <ViolationCard key={`${v.detected_at}-${v.flow_index ?? i}`} violation={v} />
          ))}
        </div>
      )}
    </div>
  );
}


function ViolationCard({ violation }: { violation: AuditViolation }) {
  const meta = TYPE_META[violation.type] || { icon: '⚠️', color: '#e8a040', bg: '#e8a04018' };
  const detected = timeAgo(violation.detected_at);
  return (
    <div style={{
      padding: '10px 12px', borderRadius: 6,
      background: meta.bg, border: `1px solid ${meta.color}22`,
      borderLeft: `3px solid ${meta.color}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 13 }}>{meta.icon}</span>
        <span style={{
          fontSize: 11, fontWeight: 700, color: meta.color,
          padding: '1px 6px', borderRadius: 3, background: `${meta.color}18`,
        }}>
          {violation.type}
        </span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
          {detected}
        </span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>
        {violation.detail}
      </div>
    </div>
  );
}
