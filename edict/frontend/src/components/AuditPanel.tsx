import { useEffect, useRef } from 'react';
import { useStore, timeAgo } from '../store';
import { api } from '../api';
import type { AuditViolation, WatchedTask } from '../api';

/** 违规类型对应的样式 */
const TYPE_META: Record<string, { icon: string; color: string; bg: string }> = {
  '越权调用': { icon: '🚫', color: '#ff5270', bg: '#ff527018' },
  '流程跳步': { icon: '⚡', color: '#e8a040', bg: '#e8a04018' },
  '断链超时': { icon: '🔗', color: '#6a9eff', bg: '#6a9eff18' },
};

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

  // 每 10 秒自动刷新监察数据（比其他面板更频繁）
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

  // 监察运行状态判断
  const isRunning = !!lastCheck;
  const lastCheckAgo = timeAgo(lastCheck);
  // 如果最后检查时间超过 3 分钟，认为监察可能停止
  const isStale = (() => {
    if (!lastCheck) return true;
    try {
      const d = new Date(lastCheck.includes('T') ? lastCheck : lastCheck.replace(' ', 'T') + 'Z');
      return Date.now() - d.getTime() > 3 * 60 * 1000;
    } catch {
      return true;
    }
  })();

  const recentViolations = violations.slice(-20).reverse(); // 最近20条，倒序

  return (
    <div>
      {/* ── Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>
            🛡️ 流程监察
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>
            监督三省六部任务流转完整性，检测越权、跳步、断链
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
          sub={watchedCount > 0 ? '实时监控活跃任务' : '当前无活跃任务'}
        />
        <StatCard
          icon="⚠️"
          label="待处理违规"
          value={`${violations.length} 条`}
          sub={violations.length > 0 ? '请关注并纠正' : '暂无违规'}
        />
      </div>

      {/* ── 正在监察的任务 ── */}
      <Section title={`👁️ 正在监察的任务 (${watchedCount})`}>
        {watchedTasks.length === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>
            当前没有活跃任务，监察处于待命状态
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {watchedTasks.map((task) => (
              <WatchedTaskCard key={task.task_id} task={task} />
            ))}
          </div>
        )}
      </Section>

      {/* ── 违规记录 ── */}
      <Section title={`🚨 违规记录 (${recentViolations.length})`}>
        {recentViolations.length === 0 ? (
          <div className="mb-empty" style={{ padding: 20 }}>
            {isRunning ? '✅ 所有任务流程正常，暂无违规' : '暂无监察数据，请确认 pipeline_watchdog.py 是否在运行'}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {recentViolations.map((v, i) => (
              <ViolationCard key={`${v.task_id}-${v.detected_at}-${i}`} violation={v} />
            ))}
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
        <div><b>标准流程：</b>皇上 → 太子 → 中书省 → 门下省 → 中书省 → 尚书省 → 六部 → 尚书省 → 中书省 → 太子 → 皇上</div>
        <div><b>越权调用：</b>from→to 不在合法流转对表内（如太子→六部）</div>
        <div><b>流程跳步：</b>缺少必要环节（如跳过门下省审议）</div>
        <div><b>断链超时：</b>某部门 1 分钟内未回应，监察自动唤醒 + 通知上级</div>
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


function WatchedTaskCard({ task }: { task: WatchedTask }) {
  const stateLabel = STATE_LABEL[task.state] || task.state;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
      borderRadius: 8, background: 'var(--panel2)', border: '1px solid var(--line)',
    }}>
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
  );
}


function ViolationCard({ violation }: { violation: AuditViolation }) {
  const meta = TYPE_META[violation.type] || { icon: '⚠️', color: '#e8a040', bg: '#e8a04018' };
  const detected = timeAgo(violation.detected_at);
  return (
    <div style={{
      padding: '12px 14px', borderRadius: 8,
      background: meta.bg, border: `1px solid ${meta.color}33`,
      borderLeft: `3px solid ${meta.color}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 14 }}>{meta.icon}</span>
        <span style={{
          fontSize: 12, fontWeight: 700, color: meta.color,
          padding: '1px 8px', borderRadius: 4, background: `${meta.color}22`,
        }}>
          {violation.type}
        </span>
        <span style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 4,
          background: '#6a9eff18', color: '#6a9eff',
        }}>
          {violation.task_id}
        </span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
          {detected}
        </span>
      </div>
      <div style={{ fontSize: 13, marginBottom: 4, fontWeight: 600 }}>
        {violation.title}
      </div>
      <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>
        {violation.detail}
      </div>
    </div>
  );
}
