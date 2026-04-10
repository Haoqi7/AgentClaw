import { useStore, isEdict, STATE_LABEL, timeAgo } from '../store';
import type { Task, GatewayConversation } from '../api';
import { api } from '../api';
import { useState, useEffect, useCallback } from 'react';

// Agent maps built from agentConfig
function useAgentMaps() {
  const cfg = useStore((s) => s.agentConfig);
  const emojiMap: Record<string, string> = {};
  const labelMap: Record<string, string> = {};
  if (cfg?.agents) {
    cfg.agents.forEach((a) => {
      emojiMap[a.id] = a.emoji || '🏛️';
      labelMap[a.id] = a.label || a.id;
    });
  }
  return { emojiMap, labelMap };
}

function extractAgent(t: Task): string {
  const m = (t.id || '').match(/^OC-(\w+)-/);
  if (m) return m[1];
  return (t.org || '').replace(/省|部/g, '').toLowerCase();
}

function humanTitle(t: Task, labelMap: Record<string, string>): string {
  let title = t.title || '';
  if (title === 'heartbeat 会话') return '💓 心跳检测';
  const m = title.match(/^agent:(\w+):(\w+)/);
  if (m) {
    const agLabel = labelMap[m[1]] || m[1];
    if (m[2] === 'main') return agLabel + ' · 主会话';
    if (m[2] === 'subagent') return agLabel + ' · 子任务执行';
    if (m[2] === 'cron') return agLabel + ' · 定时任务';
    return agLabel + ' · ' + m[2];
  }
  return title.replace(/ 会话$/, '') || t.id;
}

function channelLabel(t: Task): { icon: string; text: string } {
  const now = t.now || '';
  if (now.includes('feishu/direct')) return { icon: '💬', text: '飞书对话' };
  if (now.includes('feishu')) return { icon: '💬', text: '飞书' };
  if (now.includes('webchat')) return { icon: '🌐', text: 'WebChat' };
  if (now.includes('cron')) return { icon: '⏰', text: '定时' };
  if (now.includes('direct')) return { icon: '📨', text: '直连' };
  return { icon: '🔗', text: '会话' };
}

function lastMessage(t: Task): string {
  const acts = t.activity || [];
  for (let i = acts.length - 1; i >= 0; i--) {
    const a = acts[i];
    if (a.kind === 'assistant') {
      let txt = a.text || '';
      if (txt.startsWith('NO_REPLY') || txt.startsWith('Reasoning:')) continue;
      txt = txt.replace(/\[\[.*?\]\]/g, '').replace(/\*\*/g, '').replace(/^#+\s/gm, '').trim();
      return txt.substring(0, 120) + (txt.length > 120 ? '…' : '');
    }
  }
  return '';
}

export default function SessionsPanel() {
  const liveStatus = useStore((s) => s.liveStatus);
  const sessFilter = useStore((s) => s.sessFilter);
  const setSessFilter = useStore((s) => s.setSessFilter);
  const { emojiMap, labelMap } = useAgentMaps();
  const [detailTask, setDetailTask] = useState<Task | null>(null);
  const [clearingAgent, setClearingAgent] = useState<string | null>(null);
  const [showAgentMenu, setShowAgentMenu] = useState(false);
  const [showGwSessions, setShowGwSessions] = useState(false);
  const [gwConvs, setGwConvs] = useState<GatewayConversation[]>([]);
  const [gwLoading, setGwLoading] = useState(false);
  const [deletingConv, setDeletingConv] = useState<string | null>(null);
  const toast = useStore((s) => s.toast);

  // 加载 Gateway 会话列表（通过 Dashboard 代理 API）
  const loadGwConversations = useCallback(async () => {
    setGwLoading(true);
    try {
      const r = await api.gatewayConversations();
      if (r.ok && r.conversations) {
        setGwConvs(r.conversations);
      } else {
        toast(`⚠️ ${r.error || '获取 Gateway 会话失败'}`, 'err');
      }
    } catch {
      toast('⚠️ Gateway 连接失败，请确认 Gateway 已启动', 'err');
    } finally {
      setGwLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (showGwSessions) loadGwConversations();
  }, [showGwSessions, loadGwConversations]);

  // 删除指定 Gateway 会话
  const deleteGwConversation = async (convId: string) => {
    setDeletingConv(convId);
    try {
      const r = await api.gatewayDeleteConversation(convId);
      if (r.ok) {
        toast(`✅ 会话 ${convId.substring(0, 16)}… 已删除`);
        setGwConvs((prev) => prev.filter((c) => c.id !== convId));
      } else {
        toast(`❌ ${r.error || '删除失败'}`, 'err');
      }
    } catch {
      toast('❌ Gateway 连接失败', 'err');
    } finally {
      setDeletingConv(null);
    }
  };

  const tasks = liveStatus?.tasks || [];
  const sessions = tasks.filter((t) => !isEdict(t));

  let filtered = sessions;
  if (sessFilter === 'active') filtered = sessions.filter((t) => !['Done', 'Cancelled'].includes(t.state));
  else if (sessFilter !== 'all') filtered = sessions.filter((t) => extractAgent(t) === sessFilter);

  // Unique agents for filter tabs
  const agentIds = [...new Set(sessions.map(extractAgent))];

  // Gateway 会话管理（内嵌式面板，通过 Dashboard 代理 API 操作，避免外部 URL 不可达）
  const openGatewaySessions = () => {
    setShowGwSessions(true);
  };

  // 清空指定 Agent 的非 main 会话
  const clearAgentSessions = async (agentId: string) => {
    setClearingAgent(agentId);
    setShowAgentMenu(false);
    try {
      const r = await api.gatewayClearAgentSessions(agentId);
      if (r.ok) {
        toast(`✅ 已清理 ${labelMap[agentId] || agentId} 的 ${r.cleared || 0} 个非主会话`, 'ok');
      } else {
        toast(`❌ ${r.error || '清理失败'}`, 'err');
      }
    } catch {
      toast('❌ Gateway 连接失败', 'err');
    } finally {
      setClearingAgent(null);
    }
  };

  return (
    <div>
      {/* Header Actions */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button
          className="btn btn-g"
          onClick={openGatewaySessions}
          style={{ fontSize: 12, padding: '5px 14px' }}
        >
          🔗 Gateway 会话管理
        </button>
        {showGwSessions && (
          <button
            className="btn"
            onClick={() => setShowGwSessions(false)}
            style={{ fontSize: 12, padding: '5px 14px', color: 'var(--muted)', border: '1px solid var(--line)', borderRadius: 6, cursor: 'pointer' }}
          >
            ✕ 关闭面板
          </button>
        )}
        <div style={{ position: 'relative' }}>
          <button
            className="btn"
            onClick={() => setShowAgentMenu(!showAgentMenu)}
            disabled={clearingAgent !== null}
            style={{ fontSize: 12, padding: '5px 14px', background: 'rgba(255,82,112,0.12)', color: 'var(--danger)', border: '1px solid rgba(255,82,112,0.25)', borderRadius: 6, cursor: 'pointer' }}
          >
            {clearingAgent ? `⏳ 清理中...` : '🧹 清空Agent会话'}
          </button>
          {showAgentMenu && (
            <div
              style={{
                position: 'absolute', top: '100%', left: 0, zIndex: 100,
                background: 'var(--panel2)', border: '1px solid var(--line)',
                borderRadius: 8, padding: 6, minWidth: 160, marginTop: 4,
                boxShadow: '0 4px 16px rgba(0,0,0,0.2)'
              }}
            >
              <div style={{ fontSize: 11, color: 'var(--muted)', padding: '4px 8px', marginBottom: 4 }}>选择要清理的 Agent：</div>
              {agentIds.slice(0, 12).map((id) => (
                <div
                  key={id}
                  onClick={() => clearAgentSessions(id)}
                  style={{
                    padding: '6px 10px', borderRadius: 6, cursor: 'pointer',
                    fontSize: 12, display: 'flex', alignItems: 'center', gap: 6,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--panel)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <span>{emojiMap[id] || '🏛️'}</span>
                  <span>{labelMap[id] || id}</span>
                  <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto' }}>
                    {sessions.filter((t) => extractAgent(t) === id).length} 会话
                  </span>
                </div>
              ))}
              <div style={{ borderTop: '1px solid var(--line)', marginTop: 4, paddingTop: 4 }}>
                <div
                  onClick={() => clearAgentSessions('all')}
                  style={{
                    padding: '6px 10px', borderRadius: 6, cursor: 'pointer',
                    fontSize: 12, color: 'var(--danger)', fontWeight: 600,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,82,112,0.1)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  🧹 清空所有Agent
                </div>
              </div>
              <div
                onClick={() => setShowAgentMenu(false)}
                style={{ padding: '4px 10px', marginTop: 2 }}
              >
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>取消</span>
              </div>
            </div>
          )}
        </div>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
          共 {sessions.length} 个会话 · 仅清理非主会话（保留上下文）
        </span>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        {[
          { key: 'all', label: `全部 (${sessions.length})` },
          { key: 'active', label: '活跃' },
          ...agentIds.slice(0, 8).map((id) => ({ key: id, label: labelMap[id] || id })),
        ].map((f) => (
          <span
            key={f.key}
            className={`sess-filter${sessFilter === f.key ? ' active' : ''}`}
            onClick={() => setSessFilter(f.key)}
          >
            {f.label}
          </span>
        ))}
      </div>

      {/* Gateway 会话管理面板（内嵌式） */}
      {showGwSessions && (
        <div style={{
          marginBottom: 16, border: '1px solid var(--line)', borderRadius: 10,
          background: 'var(--panel2)', overflow: 'hidden',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '10px 16px', borderBottom: '1px solid var(--line)',
            background: 'var(--panel)',
          }}>
            <span style={{ fontSize: 13, fontWeight: 700 }}>🔌 Gateway 会话列表</span>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>
              {gwLoading ? '⟳ 加载中…' : `共 ${gwConvs.length} 个会话`}
            </span>
            <button
              onClick={loadGwConversations}
              disabled={gwLoading}
              style={{
                marginLeft: 'auto', fontSize: 11, padding: '3px 10px',
                background: 'transparent', color: 'var(--acc)', border: '1px solid var(--acc)',
                borderRadius: 4, cursor: 'pointer',
              }}
            >
              🔄 刷新
            </button>
          </div>
          <div style={{ maxHeight: 420, overflowY: 'auto' }}>
            {gwLoading ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>⟳ 正在从 Gateway 获取会话列表…</div>
            ) : gwConvs.length === 0 ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>暂无 Gateway 会话</div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ background: 'var(--panel)', color: 'var(--muted)', fontSize: 10 }}>
                    <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>Agent / 会话 ID</th>
                    <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>标题</th>
                    <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600 }}>消息数</th>
                    <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>更新时间</th>
                    <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600 }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {gwConvs.map((c) => {
                    const agentId = c.agent_id || c.agentId || '';
                    const isMain = (c.id || '').toLowerCase().includes(':main') || (c.title || '').toLowerCase().includes('main');
                    return (
                      <tr key={c.id} style={{ borderTop: '1px solid var(--line)' }}>
                        <td style={{ padding: '8px 12px' }}>
                          <div style={{ fontWeight: 600, fontSize: 11, color: 'var(--acc)' }}>{agentId || '—'}</div>
                          <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'monospace' }}>{(c.id || '').substring(0, 28)}…</div>
                        </td>
                        <td style={{ padding: '8px 12px', maxWidth: 200 }}>
                          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {isMain ? '💫 ' : ''}{c.title || '(无标题)'}
                          </div>
                        </td>
                        <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                          {c.message_count ?? c.messageCount ?? 0}
                        </td>
                        <td style={{ padding: '8px 12px', fontSize: 10, color: 'var(--muted)' }}>
                          {timeAgo(c.updated_at || c.updatedAt || '') || timeAgo(c.created_at || c.createdAt || '') || '—'}
                        </td>
                        <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                          {isMain ? (
                            <span style={{ fontSize: 10, color: 'var(--muted)' }}>主会话</span>
                          ) : (
                            <button
                              onClick={() => deleteGwConversation(c.id)}
                              disabled={deletingConv === c.id}
                              style={{
                                fontSize: 11, padding: '3px 8px',
                                background: 'rgba(255,82,112,0.1)', color: 'var(--danger)',
                                border: '1px solid rgba(255,82,112,0.25)', borderRadius: 4, cursor: 'pointer',
                              }}
                            >
                              {deletingConv === c.id ? '⏳' : '🗑️'} 删除
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Grid */}
      <div className="sess-grid">
        {!filtered.length ? (
          <div style={{ fontSize: 13, color: 'var(--muted)', padding: 24, textAlign: 'center', gridColumn: '1/-1' }}>
            暂无小任务/会话数据
          </div>
        ) : (
          filtered.map((t) => {
            const agent = extractAgent(t);
            const emoji = emojiMap[agent] || '🏛️';
            const agLabel = labelMap[agent] || t.org || agent;
            const hb = t.heartbeat || { status: 'unknown' as const, label: '' };
            const ch = channelLabel(t);
            const title = humanTitle(t, labelMap);
            const msg = lastMessage(t);
            const sm = t.sourceMeta || {};
            const totalTk = (sm as Record<string, unknown>).totalTokens as number | undefined;
            const updatedAt = t.eta || '';
            const hbDot = hb.status === 'active' ? '🟢' : hb.status === 'warn' ? '🟡' : hb.status === 'stalled' ? '🔴' : '⚪';
            const st = t.state || 'Unknown';

            return (
              <div className="sess-card" key={t.id} onClick={() => setDetailTask(t)}>
                <div className="sc-top">
                  <span className="sc-emoji">{emoji}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className="sc-agent">{agLabel}</span>
                      <span style={{ fontSize: 10, color: 'var(--muted)', background: 'var(--panel2)', padding: '2px 6px', borderRadius: 4 }}>
                        {ch.icon} {ch.text}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span title={hb.label || ''}>{hbDot}</span>
                    <span className={`tag st-${st}`} style={{ fontSize: 10 }}>{STATE_LABEL[st] || st}</span>
                  </div>
                </div>
                <div className="sc-title">{title}</div>
                {msg && (
                  <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5, marginBottom: 8, borderLeft: '2px solid var(--line)', paddingLeft: 8, maxHeight: 40, overflow: 'hidden' }}>
                    {msg}
                  </div>
                )}
                <div className="sc-meta">
                  {totalTk ? <span style={{ fontSize: 10, color: 'var(--muted)' }}>🪙 {totalTk.toLocaleString()} tokens</span> : null}
                  {updatedAt ? <span className="sc-time">{timeAgo(updatedAt)}</span> : null}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Session Detail Modal */}
      {detailTask && (
        <SessionDetailModal task={detailTask} labelMap={labelMap} emojiMap={emojiMap} onClose={() => setDetailTask(null)} />
      )}
    </div>
  );
}

function SessionDetailModal({
  task: t,
  labelMap,
  emojiMap,
  onClose,
}: {
  task: Task;
  labelMap: Record<string, string>;
  emojiMap: Record<string, string>;
  onClose: () => void;
}) {
  const agent = extractAgent(t);
  const emoji = emojiMap[agent] || '🏛️';
  const title = humanTitle(t, labelMap);
  const ch = channelLabel(t);
  const hb = t.heartbeat || { status: 'unknown' as const, label: '' };
  const sm = t.sourceMeta || {};
  const acts = t.activity || [];
  const st = t.state || 'Unknown';

  const totalTokens = (sm as Record<string, unknown>).totalTokens as number | undefined;
  const inputTokens = (sm as Record<string, unknown>).inputTokens as number | undefined;
  const outputTokens = (sm as Record<string, unknown>).outputTokens as number | undefined;

  return (
    <div className="modal-bg open" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>✕</button>
        <div className="modal-body">
          <div style={{ fontSize: 11, color: 'var(--acc)', fontWeight: 700, letterSpacing: '.04em', marginBottom: 4 }}>{t.id}</div>
          <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 6 }}>{emoji} {title}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18, flexWrap: 'wrap' }}>
            <span className={`tag st-${st}`}>{STATE_LABEL[st] || st}</span>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>{ch.icon} {ch.text}</span>
            {hb.label && <span style={{ fontSize: 11 }}>{hb.label}</span>}
          </div>

          {/* Stats */}
          <div style={{ display: 'flex', gap: 14, marginBottom: 18, flexWrap: 'wrap' }}>
            {totalTokens != null && (
              <div style={{ background: 'var(--panel2)', padding: '10px 16px', borderRadius: 8, fontSize: 12 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--acc)' }}>{totalTokens.toLocaleString()}</div>
                <div style={{ color: 'var(--muted)', fontSize: 10 }}>总 Tokens</div>
              </div>
            )}
            {inputTokens != null && (
              <div style={{ background: 'var(--panel2)', padding: '10px 16px', borderRadius: 8, fontSize: 12 }}>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{inputTokens.toLocaleString()}</div>
                <div style={{ color: 'var(--muted)', fontSize: 10 }}>输入</div>
              </div>
            )}
            {outputTokens != null && (
              <div style={{ background: 'var(--panel2)', padding: '10px 16px', borderRadius: 8, fontSize: 12 }}>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{outputTokens.toLocaleString()}</div>
                <div style={{ color: 'var(--muted)', fontSize: 10 }}>输出</div>
              </div>
            )}
          </div>

          {/* Recent Activity */}
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
            📋 最近活动 <span style={{ fontWeight: 400, color: 'var(--muted)' }}>({acts.length} 条)</span>
          </div>
          <div style={{ maxHeight: 350, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 10, background: 'var(--panel2)' }}>
            {!acts.length ? (
              <div style={{ padding: 16, color: 'var(--muted)', fontSize: 12, textAlign: 'center' }}>暂无活动记录</div>
            ) : (
              acts.slice(-15).reverse().map((a, i) => {
                const kind = a.kind || '';
                const kIcon = kind === 'assistant' ? '🤖' : kind === 'tool' ? '🔧' : kind === 'user' ? '👤' : '📝';
                const kLabel = kind === 'assistant' ? '回复' : kind === 'tool' ? '工具' : kind === 'user' ? '用户' : '事件';
                let txt = (a.text || '').replace(/\[\[.*?\]\]/g, '').replace(/\*\*/g, '').trim();
                if (txt.length > 200) txt = txt.substring(0, 200) + '…';
                const time = ((a.at as string) || '').substring(11, 19);
                return (
                  <div key={i} style={{ padding: '8px 12px', borderBottom: '1px solid var(--line)', fontSize: 12, lineHeight: 1.5 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                      <span>{kIcon}</span>
                      <span style={{ fontWeight: 600, fontSize: 11 }}>{kLabel}</span>
                      <span style={{ color: 'var(--muted)', fontSize: 10, marginLeft: 'auto' }}>{time}</span>
                    </div>
                    <div style={{ color: 'var(--muted)' }}>{txt}</div>
                  </div>
                );
              })
            )}
          </div>

          {t.output && t.output !== '-' && (
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 12, wordBreak: 'break-all', borderTop: '1px solid var(--line)', paddingTop: 8 }}>
              📂 {t.output}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
