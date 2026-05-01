import { useEffect, useState } from 'react';
import { useStore } from '../store';
import { api, RemoteSkillItem, ClawHubSkill } from '../api';

interface CommunitySource {
  label: string;
  emoji: string;
  repo: string;
  branch: string;
  basePath: string;
  stars: string;
  desc: string;
  skills: { name: string; path: string }[];
}

const COMMUNITY_SOURCES_RAW: CommunitySource[] = [
  {
    label: 'ClawHub 技能商店',
    emoji: '🔍',
    repo: '',
    branch: '',
    basePath: '',
    stars: '62k+',
    desc: '搜索和安装社区技能',
    skills: [],
  },
  {
    label: 'obra/superpowers',
    emoji: '⚡',
    repo: 'obra/superpowers',
    branch: 'main',
    basePath: 'skills',
    stars: '...',
    desc: '完整开发工作流技能集',
    skills: [
      { name: 'brainstorming', path: 'brainstorming/SKILL.md' },
      { name: 'test-driven-development', path: 'test-driven-development/SKILL.md' },
      { name: 'systematic-debugging', path: 'systematic-debugging/SKILL.md' },
      { name: 'subagent-driven-development', path: 'subagent-driven-development/SKILL.md' },
      { name: 'writing-plans', path: 'writing-plans/SKILL.md' },
      { name: 'executing-plans', path: 'executing-plans/SKILL.md' },
      { name: 'requesting-code-review', path: 'requesting-code-review/SKILL.md' },
      { name: 'root-cause-tracing', path: 'root-cause-tracing/SKILL.md' },
      { name: 'verification-before-completion', path: 'verification-before-completion/SKILL.md' },
      { name: 'dispatching-parallel-agents', path: 'dispatching-parallel-agents/SKILL.md' },
    ],
  },
  {
    label: 'anthropics/skills',
    emoji: '🏛️',
    repo: 'anthropics/skills',
    branch: 'main',
    basePath: 'skills',
    stars: '...',
    desc: 'Anthropic 官方技能库',
    skills: [
      { name: 'docx', path: 'docx/SKILL.md' },
      { name: 'pdf', path: 'pdf/SKILL.md' },
      { name: 'xlsx', path: 'xlsx/SKILL.md' },
      { name: 'pptx', path: 'pptx/SKILL.md' },
      { name: 'mcp-builder', path: 'mcp-builder/SKILL.md' },
      { name: 'frontend-design', path: 'frontend-design/SKILL.md' },
      { name: 'web-artifacts-builder', path: 'web-artifacts-builder/SKILL.md' },
      { name: 'webapp-testing', path: 'webapp-testing/SKILL.md' },
      { name: 'algorithmic-art', path: 'algorithmic-art/SKILL.md' },
      { name: 'canvas-design', path: 'canvas-design/SKILL.md' },
    ],
  },
  {
    label: 'ComposioHQ/awesome-claude-skills',
    emoji: '🌐',
    repo: 'ComposioHQ/awesome-claude-skills',
    branch: 'master',
    basePath: '',
    stars: '...',
    desc: '100+ 社区精选技能',
    skills: [
      { name: 'github-integration', path: 'github-integration/SKILL.md' },
      { name: 'data-analysis', path: 'data-analysis/SKILL.md' },
      { name: 'code-review', path: 'code-review/SKILL.md' },
    ],
  },
];

function buildSkillUrl(source: CommunitySource, skill: { path: string }): string {
  return `https://raw.githubusercontent.com/${source.repo}/refs/heads/${source.branch}/${source.basePath ? source.basePath + '/' : ''}${skill.path}`;
}

function fetchGitHubStars(repo: string): Promise<string> {
  return fetch(`https://api.github.com/repos/${repo}`, {
    headers: { 'Accept': 'application/vnd.github.v3+json' },
  })
    .then(r => r.json())
    .then(data => {
      const stargazers = data?.stargazers_count;
      if (typeof stargazers === 'number') {
        return stargazers >= 1000 ? `${(stargazers / 1000).toFixed(1)}k` : String(stargazers);
      }
      return '?';
    })
    .catch(() => '?');
}

export default function SkillsConfig() {
  const agentConfig = useStore((s) => s.agentConfig);
  const loadAgentConfig = useStore((s) => s.loadAgentConfig);
  const toast = useStore((s) => s.toast);

  const [skillModal, setSkillModal] = useState<{ agentId: string; name: string; content: string; path: string } | null>(null);
  const [addForm, setAddForm] = useState<{ agentId: string; agentLabel: string } | null>(null);
  const [formData, setFormData] = useState({ name: '', desc: '', trigger: '' });
  const [submitting, setSubmitting] = useState(false);

  const [activeTab, setActiveTab] = useState<'local' | 'remote'>('local');

  const [remoteSkills, setRemoteSkills] = useState<RemoteSkillItem[]>([]);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [addRemoteForm, setAddRemoteForm] = useState(false);
  const [remoteFormData, setRemoteFormData] = useState({ agentId: '', skillName: '', sourceUrl: '', description: '' });
  const [remoteSubmitting, setRemoteSubmitting] = useState(false);
  const [updatingSkill, setUpdatingSkill] = useState<string | null>(null);
  const [removingSkill, setRemovingSkill] = useState<string | null>(null);
  const [quickPickSource, setQuickPickSource] = useState<(typeof COMMUNITY_SOURCES_RAW)[0] | null>(null);
  const [quickPickAgent, setQuickPickAgent] = useState('');

  const [communitySources, setCommunitySources] = useState(COMMUNITY_SOURCES_RAW);

  // ClawHub search state
  const [clawhubQuery, setClawhubQuery] = useState('');
  const [clawhubResults, setClawhubResults] = useState<ClawHubSkill[]>([]);
  const [clawhubSearching, setClawhubSearching] = useState(false);
  const [clawhubInstalling, setClawhubInstalling] = useState<string | null>(null);

  useEffect(() => { loadAgentConfig(); }, [loadAgentConfig]);

  useEffect(() => {
    let cancelled = false;
    const promises = COMMUNITY_SOURCES_RAW.map(async (src) => {
      if (src.repo) {
        const stars = await fetchGitHubStars(src.repo);
        return { ...src, stars };
      }
      return src;
    });
    Promise.all(promises).then((results) => {
      if (!cancelled) setCommunitySources(results);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (activeTab === 'remote') loadRemoteSkills();
  }, [activeTab]);

  const loadRemoteSkills = async () => {
    setRemoteLoading(true);
    try {
      const r = await api.remoteSkillsList();
      if (r.ok) setRemoteSkills(r.remoteSkills || []);
    } catch { toast('远程技能列表加载失败', 'err'); }
    setRemoteLoading(false);
  };

  const openSkill = async (agentId: string, skillName: string) => {
    setSkillModal({ agentId, name: skillName, content: '⟳ 加载中…', path: '' });
    try {
      const r = await api.skillContent(agentId, skillName);
      if (r.ok) { setSkillModal({ agentId, name: skillName, content: r.content || '', path: r.path || '' }); }
      else { setSkillModal({ agentId, name: skillName, content: '❌ ' + (r.error || '无法读取'), path: '' }); }
    } catch { setSkillModal({ agentId, name: skillName, content: '❌ 服务器连接失败', path: '' }); }
  };

  const openAddForm = (agentId: string, agentLabel: string) => {
    setAddForm({ agentId, agentLabel });
    setFormData({ name: '', desc: '', trigger: '' });
  };

  const submitAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addForm || !formData.name) return;
    setSubmitting(true);
    try {
      const r = await api.addSkill(addForm.agentId, formData.name, formData.desc, formData.trigger);
      if (r.ok) { toast(`✅ 技能 ${formData.name} 已添加到 ${addForm.agentLabel}`, 'ok'); setAddForm(null); loadAgentConfig(); }
      else { toast(r.error || '添加失败', 'err'); }
    } catch { toast('服务器连接失败', 'err'); }
    setSubmitting(false);
  };

  const submitAddRemote = async (e: React.FormEvent) => {
    e.preventDefault();
    const { agentId, skillName, sourceUrl, description } = remoteFormData;
    if (!agentId || !skillName || !sourceUrl) return;
    setRemoteSubmitting(true);
    try {
      const r = await api.addRemoteSkill(agentId, skillName, sourceUrl, description);
      if (r.ok) { toast(`✅ 远程技能 ${skillName} 已添加到 ${agentId}`, 'ok'); setAddRemoteForm(false); setRemoteFormData({ agentId: '', skillName: '', sourceUrl: '', description: '' }); loadRemoteSkills(); loadAgentConfig(); }
      else { toast(r.error || '添加失败', 'err'); }
    } catch { toast('服务器连接失败', 'err'); }
    setRemoteSubmitting(false);
  };

  const handleUpdate = async (skill: RemoteSkillItem) => {
    const key = `${skill.agentId}/${skill.skillName}`;
    setUpdatingSkill(key);
    try {
      const r = await api.updateRemoteSkill(skill.agentId, skill.skillName);
      if (r.ok) { toast(`✅ 技能 ${skill.skillName} 已更新`, 'ok'); loadRemoteSkills(); }
      else { toast(r.error || '更新失败', 'err'); }
    } catch { toast('服务器连接失败', 'err'); }
    setUpdatingSkill(null);
  };

  const handleRemove = async (skill: RemoteSkillItem) => {
    const key = `${skill.agentId}/${skill.skillName}`;
    setRemovingSkill(key);
    try {
      const r = await api.removeRemoteSkill(skill.agentId, skill.skillName);
      if (r.ok) { toast(`🗑️ 技能 ${skill.skillName} 已移除`, 'ok'); loadRemoteSkills(); loadAgentConfig(); }
      else { toast(r.error || '移除失败', 'err'); }
    } catch { toast('服务器连接失败', 'err'); }
    setRemovingSkill(null);
  };

  const handleQuickImport = async (skillUrl: string, skillName: string) => {
    if (!quickPickAgent) { toast('请先选择目标 Agent', 'err'); return; }
    try {
      const r = await api.addRemoteSkill(quickPickAgent, skillName, skillUrl, '');
      if (r.ok) { toast(`✅ ${skillName} → ${quickPickAgent}`, 'ok'); loadRemoteSkills(); loadAgentConfig(); }
      else { toast(r.error || '导入失败', 'err'); }
    } catch { toast('服务器连接失败', 'err'); }
  };

  // ClawHub search
  const handleClawhubSearch = async () => {
    if (!clawhubQuery.trim()) return;
    setClawhubSearching(true);
    try {
      const r = await api.clawhubSearch(clawhubQuery.trim());
      if (r.ok) setClawhubResults(r.results || []);
      else toast(r.error || '搜索失败', 'err');
    } catch { toast('搜索失败，请检查网络', 'err'); }
    setClawhubSearching(false);
  };

  // ClawHub install
  const handleClawhubInstall = async (slug: string, name: string) => {
    if (!quickPickAgent) { toast('请先选择目标 Agent', 'err'); return; }
    setClawhubInstalling(slug);
    try {
      const r = await api.clawhubInstall(quickPickAgent, slug);
      if (r.ok) { toast(`✅ ${name} → ${quickPickAgent}`, 'ok'); loadRemoteSkills(); loadAgentConfig(); }
      else { toast(r.error || '安装失败', 'err'); }
    } catch { toast('安装失败，请检查网络', 'err'); }
    setClawhubInstalling(null);
  };

  const isClawHubSource = (src: CommunitySource) => src.repo === '' && src.label === 'ClawHub 技能商店';

  if (!agentConfig?.agents) {
    return <div className="empty">无法加载</div>;
  }

  const localPanel = (
    <div>
      <div className="skills-grid">
        {agentConfig.agents.map((ag) => (
          <div className="sk-card" key={ag.id}>
            <div className="sk-hdr">
              <span className="sk-emoji">{ag.emoji || '🏛️'}</span>
              <span className="sk-name">{ag.label}</span>
              <span className="sk-cnt">{(ag.skills || []).length} 技能</span>
            </div>
            <div className="sk-list">
              {!(ag.skills || []).length ? (
                <div className="sk-empty">暂无 Skills</div>
              ) : (
                (ag.skills || []).map((sk) => (
                  <div className="sk-item" key={sk.name} onClick={() => openSkill(ag.id, sk.name)}>
                    <span className="si-name">📦 {sk.name}</span>
                    <span className="si-desc">{sk.description || '无描述'}</span>
                    <span className="si-arrow">›</span>
                  </div>
                ))
              )}
            </div>
            <div className="sk-add" onClick={() => openAddForm(ag.id, ag.label)}>
              ＋ 添加技能
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  const remotePanel = (
    <div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
        <button style={{ padding: '8px 18px', background: 'var(--acc)', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: 13 }}
          onClick={() => { setAddRemoteForm(true); setQuickPickSource(null); }}>
          ＋ 添加远程 Skill
        </button>
        <button style={{ padding: '8px 14px', background: 'transparent', color: 'var(--acc)', border: '1px solid var(--acc)', borderRadius: 8, cursor: 'pointer', fontSize: 12 }}
          onClick={loadRemoteSkills}>
          ⟳ 刷新列表
        </button>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 4 }}>
          共 {remoteSkills.length} 个远程技能
        </span>
      </div>

      {/* 社区快选区 */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--muted)', letterSpacing: '.06em', marginBottom: 10 }}>
          🌐 社区技能源 — 一键导入
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {communitySources.map((src) => (
            <div key={src.label}
              onClick={() => setQuickPickSource(quickPickSource?.label === src.label ? null : src)}
              style={{
                padding: '8px 14px',
                background: quickPickSource?.label === src.label ? '#0d1f45' : 'var(--panel)',
                border: `1px solid ${quickPickSource?.label === src.label ? 'var(--acc)' : 'var(--line)'}`,
                borderRadius: 10, cursor: 'pointer', fontSize: 12, transition: 'all .15s',
              }}>
              <span style={{ marginRight: 6 }}>{src.emoji}</span>
              <b style={{ color: 'var(--text)' }}>{src.label}</b>
              <span style={{ marginLeft: 6, color: '#f0b429', fontSize: 11 }}>★ {src.stars}</span>
              <span style={{ marginLeft: 8, color: 'var(--muted)' }}>{src.desc}</span>
            </div>
          ))}
        </div>

        {quickPickSource && (
          <div style={{ marginTop: 14, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 12, padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
              <span style={{ fontSize: 12, fontWeight: 600 }}>目标 Agent：</span>
              <select value={quickPickAgent} onChange={(e) => setQuickPickAgent(e.target.value)}
                style={{ padding: '6px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--text)', fontSize: 12 }}>
                <option value="">— 选择 Agent —</option>
                {agentConfig.agents.map((ag) => (
                  <option key={ag.id} value={ag.id}>{ag.emoji} {ag.label} ({ag.id})</option>
                ))}
              </select>
            </div>

            {/* ClawHub: 搜索框 + 搜索结果 */}
            {isClawHubSource(quickPickSource) ? (
              <div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                  <input type="text" value={clawhubQuery} onChange={(e) => setClawhubQuery(e.target.value)}
                    placeholder="搜索技能，如 code-review, data-analysis..."
                    onKeyDown={(e) => { if (e.key === 'Enter') handleClawhubSearch(); }}
                    style={{ flex: 1, padding: '8px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
                  <button onClick={handleClawhubSearch} disabled={clawhubSearching}
                    style={{ padding: '8px 18px', background: 'var(--acc)', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', opacity: clawhubSearching ? 0.5 : 1 }}>
                    {clawhubSearching ? '⟳ 搜索中…' : '🔍 搜索'}
                  </button>
                </div>

                {clawhubResults.length > 0 && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 8 }}>
                    {clawhubResults.map((sk) => {
                      const alreadyAdded = remoteSkills.some((r) => r.skillName === sk.slug && r.agentId === quickPickAgent);
                      return (
                        <div key={sk.slug} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--panel2)', borderRadius: 8, border: '1px solid var(--line)' }}>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>📦 {sk.name || sk.slug}</div>
                            <div style={{ fontSize: 10, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sk.description || sk.slug}</div>
                            {sk.downloads != null && <div style={{ fontSize: 9, color: '#f0b429' }}>⬇ {sk.downloads >= 1000 ? `${(sk.downloads / 1000).toFixed(1)}k` : sk.downloads}</div>}
                          </div>
                          {alreadyAdded ? (
                            <span style={{ fontSize: 10, color: '#4caf88', fontWeight: 600, whiteSpace: 'nowrap', marginLeft: 8 }}>✓ 已导入</span>
                          ) : (
                            <button onClick={() => handleClawhubInstall(sk.slug, sk.name || sk.slug)}
                              disabled={clawhubInstalling === sk.slug}
                              style={{ padding: '4px 10px', background: 'var(--acc)', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 11, whiteSpace: 'nowrap', marginLeft: 8, opacity: clawhubInstalling === sk.slug ? 0.5 : 1 }}>
                              {clawhubInstalling === sk.slug ? '⟳' : '安装'}
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}

                {!clawhubSearching && clawhubResults.length === 0 && clawhubQuery && (
                  <div style={{ textAlign: 'center', padding: 16, color: 'var(--muted)', fontSize: 12 }}>
                    输入关键词后点击搜索，从 ClawHub 查找技能
                  </div>
                )}

                <div style={{ marginTop: 10, fontSize: 10, color: 'var(--muted)' }}>
                  💡 从 clawhub.ai 搜索技能，点击安装到指定 Agent
                </div>
              </div>
            ) : (
              /* GitHub 源：原有固定技能列表 + 逐个导入 */
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 }}>
                {quickPickSource.skills.map((sk) => {
                  const skillUrl = buildSkillUrl(quickPickSource, sk);
                  const alreadyAdded = remoteSkills.some((r) => r.skillName === sk.name && r.agentId === quickPickAgent);
                  return (
                    <div key={sk.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--panel2)', borderRadius: 8, border: '1px solid var(--line)' }}>
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 600 }}>📦 {sk.name}</div>
                        <div style={{ fontSize: 10, color: 'var(--muted)', wordBreak: 'break-all', maxWidth: 180 }}>{skillUrl.split('/').slice(-2).join('/')}</div>
                      </div>
                      {alreadyAdded ? (
                        <span style={{ fontSize: 10, color: '#4caf88', fontWeight: 600 }}>✓ 已导入</span>
                      ) : (
                        <button onClick={() => handleQuickImport(skillUrl, sk.name)}
                          style={{ padding: '4px 10px', background: 'var(--acc)', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 11, whiteSpace: 'nowrap' }}>
                          导入
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* 已添加的远程技能列表 */}
      {remoteLoading ? (
        <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--muted)', fontSize: 13 }}>⟳ 加载中…</div>
      ) : remoteSkills.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px', background: 'var(--panel)', borderRadius: 12, border: '1px dashed var(--line)' }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>🌐</div>
          <div style={{ fontSize: 14, color: 'var(--muted)' }}>尚无远程技能</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>从社区技能源快速导入，或手动添加 URL</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {remoteSkills.map((sk) => {
            const key = `${sk.agentId}/${sk.skillName}`;
            const isUpdating = updatingSkill === key;
            const isRemoving = removingSkill === key;
            const agInfo = agentConfig.agents.find((a) => a.id === sk.agentId);
            return (
              <div key={key} style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 12, padding: '14px 18px', display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, alignItems: 'center' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                    <span style={{ fontSize: 14, fontWeight: 700 }}>📦 {sk.skillName}</span>
                    <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 999, background: sk.status === 'valid' ? '#0d3322' : '#3d1111', color: sk.status === 'valid' ? '#4caf88' : '#ff5270', fontWeight: 600 }}>
                      {sk.status === 'valid' ? '✓ 有效' : '✗ 文件丢失'}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--muted)', background: 'var(--panel2)', padding: '2px 8px', borderRadius: 6 }}>
                      {agInfo?.emoji} {agInfo?.label || sk.agentId}
                    </span>
                  </div>
                  {sk.description && <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>{sk.description}</div>}
                  <div style={{ fontSize: 10, color: 'var(--muted)', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                    <span>🔗 <a href={sk.sourceUrl} target="_blank" rel="noreferrer" style={{ color: 'var(--acc)', textDecoration: 'none' }}>{sk.sourceUrl.length > 60 ? sk.sourceUrl.slice(0, 60) + '…' : sk.sourceUrl}</a></span>
                    <span>📅 {sk.lastUpdated ? sk.lastUpdated.slice(0, 10) : sk.addedAt?.slice(0, 10)}</span>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={() => openSkill(sk.agentId, sk.skillName)}
                    style={{ padding: '6px 12px', background: 'transparent', color: 'var(--muted)', border: '1px solid var(--line)', borderRadius: 6, cursor: 'pointer', fontSize: 11 }}>
                    查看
                  </button>
                  <button onClick={() => handleUpdate(sk)} disabled={isUpdating}
                    style={{ padding: '6px 12px', background: 'transparent', color: 'var(--acc)', border: '1px solid var(--acc)', borderRadius: 6, cursor: 'pointer', fontSize: 11 }}>
                    {isUpdating ? '⟳' : '更新'}
                  </button>
                  <button onClick={() => handleRemove(sk)} disabled={isRemoving}
                    style={{ padding: '6px 12px', background: 'transparent', color: '#ff5270', border: '1px solid #ff5270', borderRadius: 6, cursor: 'pointer', fontSize: 11 }}>
                    {isRemoving ? '⟳' : '删除'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  return (
    <div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid var(--line)', paddingBottom: 0 }}>
        {[
          { key: 'local', label: '🏛️ 本地技能', count: agentConfig.agents.reduce((n, a) => n + (a.skills?.length || 0), 0) },
          { key: 'remote', label: '🌐 远程技能', count: remoteSkills.length },
        ].map((t) => (
          <div key={t.key} onClick={() => setActiveTab(t.key as 'local' | 'remote')}
            style={{
              padding: '8px 18px', cursor: 'pointer', fontSize: 13, borderRadius: '8px 8px 0 0',
              fontWeight: activeTab === t.key ? 700 : 400,
              background: activeTab === t.key ? 'var(--panel)' : 'transparent',
              color: activeTab === t.key ? 'var(--text)' : 'var(--muted)',
              border: activeTab === t.key ? '1px solid var(--line)' : '1px solid transparent',
              borderBottom: activeTab === t.key ? '1px solid var(--panel)' : '1px solid transparent',
              position: 'relative', bottom: -1, transition: 'all .15s',
            }}>
            {t.label}
            {t.count > 0 && <span style={{ marginLeft: 6, fontSize: 10, padding: '1px 6px', borderRadius: 999, background: '#1a2040', color: 'var(--acc)' }}>{t.count}</span>}
          </div>
        ))}
      </div>

      {activeTab === 'local' ? localPanel : remotePanel}

      {skillModal && (
        <div className="modal-bg open" onClick={() => setSkillModal(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setSkillModal(null)}>✕</button>
            <div className="modal-body">
              <div style={{ fontSize: 11, color: 'var(--acc)', fontWeight: 700, letterSpacing: '.04em', marginBottom: 4 }}>
                {skillModal.agentId.toUpperCase()}
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 16 }}>📦 {skillModal.name}</div>
              <div className="sk-modal-body">
                <div className="sk-md" style={{ whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.7 }}>
                  {skillModal.content}
                </div>
                {skillModal.path && (
                  <div className="sk-path" style={{ fontSize: 10, color: 'var(--muted)', marginTop: 12 }}>
                    📂 {skillModal.path}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {addForm && (
        <div className="modal-bg open" onClick={() => setAddForm(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setAddForm(null)}>✕</button>
            <div className="modal-body">
              <div style={{ fontSize: 11, color: 'var(--acc)', fontWeight: 700, letterSpacing: '.04em', marginBottom: 4 }}>
                为 {addForm.agentLabel} 添加技能
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 18 }}>＋ 新增 Skill</div>
              <div style={{ background: 'var(--panel2)', border: '1px solid var(--line)', borderRadius: 10, padding: 14, marginBottom: 18, fontSize: 12, lineHeight: 1.7, color: 'var(--muted)' }}>
                <b style={{ color: 'var(--text)' }}>📋 Skill 规范说明</b><br />
                • 技能名称使用<b style={{ color: 'var(--text)' }}>小写英文 + 连字符</b><br />
                • 创建后会生成模板文件 SKILL.md<br />
                • 技能会在 agent 收到相关任务时<b style={{ color: 'var(--text)' }}>自动激活</b>
              </div>
              <form onSubmit={submitAdd} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 6 }}>技能名称 <span style={{ color: '#ff5270' }}>*</span></label>
                  <input type="text" required placeholder="如 data-analysis, code-review" value={formData.name}
                    onChange={(e) => setFormData((p) => ({ ...p, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '') }))}
                    style={{ width: '100%', padding: '10px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 6 }}>技能描述</label>
                  <input type="text" placeholder="一句话说明用途" value={formData.desc}
                    onChange={(e) => setFormData((p) => ({ ...p, desc: e.target.value }))}
                    style={{ width: '100%', padding: '10px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 6 }}>触发条件（可选）</label>
                  <input type="text" placeholder="何时激活此技能" value={formData.trigger}
                    onChange={(e) => setFormData((p) => ({ ...p, trigger: e.target.value }))}
                    style={{ width: '100%', padding: '10px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
                </div>
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
                  <button type="button" className="btn btn-g" onClick={() => setAddForm(null)} style={{ padding: '8px 20px' }}>取消</button>
                  <button type="submit" disabled={submitting}
                    style={{ padding: '8px 20px', fontSize: 13, background: 'var(--acc)', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600 }}>
                    {submitting ? '⟳ 创建中…' : '📦 创建技能'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {addRemoteForm && (
        <div className="modal-bg open" onClick={() => setAddRemoteForm(false)}>
          <div className="modal" style={{ maxWidth: 520 }} onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setAddRemoteForm(false)}>✕</button>
            <div className="modal-body">
              <div style={{ fontSize: 11, color: '#a07aff', fontWeight: 700, letterSpacing: '.04em', marginBottom: 4 }}>远程技能管理</div>
              <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 18 }}>🌐 添加远程 Skill</div>
              <div style={{ background: 'var(--panel2)', border: '1px solid var(--line)', borderRadius: 10, padding: 12, marginBottom: 18, fontSize: 11, color: 'var(--muted)', lineHeight: 1.7 }}>
                支持 GitHub Raw URL，如：<br />
                <code style={{ color: 'var(--acc)', fontSize: 10 }}>https://raw.githubusercontent.com/obra/superpowers/refs/heads/main/skills/brainstorming/SKILL.md</code>
              </div>
              <form onSubmit={submitAddRemote} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 6 }}>目标 Agent <span style={{ color: '#ff5270' }}>*</span></label>
                  <select required value={remoteFormData.agentId} onChange={(e) => setRemoteFormData((p) => ({ ...p, agentId: e.target.value }))}
                    style={{ width: '100%', padding: '10px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 13 }}>
                    <option value="">— 选择 Agent —</option>
                    {agentConfig.agents.map((ag) => (
                      <option key={ag.id} value={ag.id}>{ag.emoji} {ag.label} ({ag.id})</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 6 }}>技能名称 <span style={{ color: '#ff5270' }}>*</span></label>
                  <input type="text" required placeholder="如 brainstorming, code-review" value={remoteFormData.skillName}
                    onChange={(e) => setRemoteFormData((p) => ({ ...p, skillName: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '') }))}
                    style={{ width: '100%', padding: '10px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 6 }}>源 URL <span style={{ color: '#ff5270' }}>*</span></label>
                  <input type="url" required placeholder="https://raw.githubusercontent.com/..." value={remoteFormData.sourceUrl}
                    onChange={(e) => setRemoteFormData((p) => ({ ...p, sourceUrl: e.target.value }))}
                    style={{ width: '100%', padding: '10px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 12, outline: 'none' }} />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 6 }}>描述（可选）</label>
                  <input type="text" placeholder="一句话说明用途" value={remoteFormData.description}
                    onChange={(e) => setRemoteFormData((p) => ({ ...p, description: e.target.value }))}
                    style={{ width: '100%', padding: '10px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, color: 'var(--text)', fontSize: 13, outline: 'none' }} />
                </div>
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
                  <button type="button" className="btn btn-g" onClick={() => setAddRemoteForm(false)} style={{ padding: '8px 20px' }}>取消</button>
                  <button type="submit" disabled={remoteSubmitting}
                    style={{ padding: '8px 20px', fontSize: 13, background: '#a07aff', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600 }}>
                    {remoteSubmitting ? '⟳ 下载中…' : '🌐 添加远程技能'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
