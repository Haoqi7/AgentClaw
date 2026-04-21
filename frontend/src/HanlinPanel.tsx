/**
 * HanlinPanel.tsx — 翰林院专属面板
 * 小说创作管理：项目列表、大纲编辑、章节阅读、实时进度、终审看板
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';

// ── Types ──

interface ProjectSummary {
  name: string;
  genre: string;
  style: string;
  status: string;
  currentChapter: number;
  totalChapters: number;
  totalWords: number;
  createdAt: string;
  updatedAt: string;
}

interface ChapterItem {
  id: number;
  title: string;
  summary?: string;
  wordCount: number;
  status: string;
  hasReview: boolean;
  reviewResult: string;
}

interface IssueItem {
  level: string;
  dimension: string;
  location?: string;
  detail: string;
  suggestion?: string;
}

interface ReviewData {
  chapter: number;
  title: string;
  wordCount: number;
  overall: string;
  score: {
    writing: number;
    logic: number;
    character: number;
    emotion: number;
    pacing: number;
  };
  issues: IssueItem[];
  highlights: string[];
}

interface ProjectDetail {
  meta: Record<string, unknown>;
  progress: Record<string, unknown>;
  chapters: ChapterItem[];
  chapterPlan: Record<string, unknown>[];
  reviewSummary: {
    totalReviewed: number;
    totalFatal: number;
    totalImportant: number;
    totalSuggestion: number;
  };
  hasOutline: boolean;
  hasWorldview: boolean;
  hasCharacters: boolean;
}

interface CreateProjectForm {
  title: string;
  genre: string;
  style: string;
  requirements: string;
}

type SubTab = 'outline' | 'worldview' | 'characters' | 'chapters' | 'writing' | 'review';

// ── API Helper ──

const API_BASE = 'http://127.0.0.1:7892/api';

async function hanlinFetch(path: string, options?: RequestInit): Promise<unknown> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Utility ──

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    planning: '规划中', outline_ready: '大纲就绪', writing: '写作中',
    reviewing: '审核中', completed: '已完成', cancelled: '已取消', unknown: '未知',
  };
  return map[status] || status;
}

function statusDot(status: string): string {
  const map: Record<string, string> = {
    planning: '#f5c842', outline_ready: '#6a9eff', writing: '#52b788',
    reviewing: '#a07aff', completed: '#2ecc8a', cancelled: '#999', unknown: '#ccc',
  };
  return map[status] || '#ccc';
}

function chapterStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待写作', writing: '写作中', reviewing: '审核中',
    done: '已通过', revision_required: '需修改', reject: '需重写', revising: '修改中',
  };
  return map[status] || status;
}

function chapterStatusDot(status: string): string {
  const map: Record<string, string> = {
    pending: '#ccc', writing: '#f5c842', reviewing: '#a07aff',
    done: '#2ecc8a', revision_required: '#ff9a6a', reject: '#ff5270', revising: '#6a9eff',
  };
  return map[status] || '#ccc';
}

function levelColor(level: string): string {
  const map: Record<string, string> = { '致命': '#ff5270', '重要': '#ff9a6a', '建议': '#52b788' };
  return map[level] || '#999';
}

function levelIcon(level: string): string {
  const map: Record<string, string> = { '致命': '🔴', '重要': '🟡', '建议': '🟢' };
  return map[level] || '⚪';
}

function simpleMarkdown(text: string): string {
  if (!text) return '';
  return text
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br/>');
}

// ── Sub-Components ──

function StatCard({ icon, label, value }: { icon: string; label: string; value: string | number }) {
  return (
    <div className="hl-stat-card">
      <div className="hl-stat-icon">{icon}</div>
      <div className="hl-stat-info">
        <div className="hl-stat-value">{value}</div>
        <div className="hl-stat-label">{label}</div>
      </div>
    </div>
  );
}

function ProjectCard({
  project, active, onClick,
}: {
  project: ProjectSummary;
  active: boolean;
  onClick: () => void;
}) {
  const pct = project.totalChapters > 0
    ? Math.round((project.currentChapter / project.totalChapters) * 100)
    : 0;

  return (
    <div
      className={`hl-project-card ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      <div className="hl-pc-header">
        <span className="hl-pc-status" style={{ background: statusDot(project.status) }} />
        <span className="hl-pc-name">{project.name}</span>
      </div>
      <div className="hl-pc-meta">
        {project.genre && <span className="hl-pc-tag">{project.genre}</span>}
        <span className="hl-pc-tag">{statusLabel(project.status)}</span>
      </div>
      <div className="hl-pc-progress">
        <div className="hl-pc-bar">
          <div className="hl-pc-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="hl-pc-stats">
          <span>{project.currentChapter}/{project.totalChapters} 章</span>
          <span>{project.totalWords.toLocaleString()} 字</span>
        </div>
      </div>
    </div>
  );
}

function CreateProjectModal({
  onClose, onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState<CreateProjectForm>({
    title: '', genre: '仙侠', style: '轻松热血', requirements: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!form.title.trim()) { setError('标题不能为空'); return; }
    setLoading(true);
    setError('');
    try {
      await hanlinFetch('/projects', {
        method: 'POST',
        body: JSON.stringify(form),
      });
      onCreated();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '创建失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="hl-modal-overlay" onClick={onClose}>
      <div className="hl-modal" onClick={(e) => e.stopPropagation()}>
        <div className="hl-modal-header">
          <h3>新建小说项目</h3>
          <button className="hl-modal-close" onClick={onClose}>×</button>
        </div>
        <div className="hl-modal-body">
          {error && <div className="hl-error">{error}</div>}
          <label className="hl-label">
            小说标题
            <input
              className="hl-input"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="例如：苍穹诀"
            />
          </label>
          <label className="hl-label">
            类型
            <select className="hl-select" value={form.genre} onChange={(e) => setForm({ ...form, genre: e.target.value })}>
              {['仙侠', '武侠', '都市', '科幻', '历史', '悬疑', '言情', '奇幻', '其他'].map((g) => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
          </label>
          <label className="hl-label">
            风格
            <select className="hl-select" value={form.style} onChange={(e) => setForm({ ...form, style: e.target.value })}>
              {['轻松热血', '沉重暗黑', '温馨治愈', '悬疑烧脑', '史诗宏大', '幽默诙谐'].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="hl-label">
            创作要求（可选）
            <textarea
              className="hl-textarea"
              value={form.requirements}
              onChange={(e) => setForm({ ...form, requirements: e.target.value })}
              placeholder="皇帝的详细旨意..."
              rows={4}
            />
          </label>
        </div>
        <div className="hl-modal-footer">
          <button className="hl-btn hl-btn-ghost" onClick={onClose}>取消</button>
          <button className="hl-btn hl-btn-primary" onClick={handleSubmit} disabled={loading}>
            {loading ? '创建中...' : '创建项目'}
          </button>
        </div>
      </div>
    </div>
  );
}

function OutlineEditor({ projectName }: { projectName: string }) {
  const [content, setContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [editedAt, setEditedAt] = useState<string | null>(null);

  useEffect(() => {
    hanlinFetch(`/projects/${encodeURIComponent(projectName)}/outline`)
      .then((data) => {
        const d = data as { content: string; editedAt: string | null };
        setContent(d.content);
        setEditedAt(d.editedAt);
      })
      .catch(() => {});
  }, [projectName]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await hanlinFetch(`/projects/${encodeURIComponent(projectName)}/outline`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      }) as { editedAt: string };
      setEditedAt(res.editedAt);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="hl-outline-editor">
      {editedAt && (
        <div className="hl-outline-edited-badge">
          皇上于 {new Date(editedAt).toLocaleString('zh-CN')} 编辑过此大纲
        </div>
      )}
      <textarea
        className="hl-editor-textarea"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="大纲内容将在此显示，皇上可直接编辑..."
      />
      <div className="hl-editor-footer">
        <span className="hl-word-count">{content.length} 字符</span>
        <button className="hl-btn hl-btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '已保存' : '保存修改'}
        </button>
      </div>
    </div>
  );
}

function MarkdownViewer({ content, title }: { content: string; title: string }) {
  return (
    <div className="hl-md-viewer">
      <div className="hl-md-title">{title}</div>
      <div
        className="hl-md-content"
        dangerouslySetInnerHTML={{ __html: `<p>${simpleMarkdown(content)}</p>` }}
      />
    </div>
  );
}

function ChapterReader({
  projectName, chapterId, title,
}: {
  projectName: string;
  chapterId: number;
  title: string;
}) {
  const [data, setData] = useState<{ content: string; wordCount: number; review: ReviewData | null } | null>(null);

  useEffect(() => {
    hanlinFetch(`/projects/${encodeURIComponent(projectName)}/chapters/${chapterId}`)
      .then((d) => setData(d as { content: string; wordCount: number; review: ReviewData | null }))
      .catch(() => {});
  }, [projectName, chapterId]);

  if (!data) return <div className="hl-loading">加载中...</div>;

  return (
    <div className="hl-chapter-reader">
      <div className="hl-cr-header">
        <h3>{title}</h3>
        <span className="hl-cr-wordcount">{data.wordCount.toLocaleString()} 字</span>
        {data.review && (
          <span className="hl-cr-badge" style={{ color: levelColor(data.review.overall === 'pass' ? '建议' : data.review.overall === 'reject' ? '致命' : '重要') }}>
            {data.review.overall === 'pass' ? '通过' : data.review.overall === 'reject' ? '需重写' : '需修改'}
          </span>
        )}
      </div>
      <div className="hl-cr-divider" />
      <div
        className="hl-cr-body"
        dangerouslySetInnerHTML={{ __html: `<p>${simpleMarkdown(data.content)}</p>` }}
      />
      {data.review && data.review.issues.length > 0 && (
        <div className="hl-cr-review-section">
          <h4>审核意见</h4>
          {data.review.issues.map((issue, i) => (
            <div key={i} className="hl-issue-card" style={{ borderLeftColor: levelColor(issue.level) }}>
              <div className="hl-issue-header">
                <span>{levelIcon(issue.level)} {issue.level}</span>
                <span className="hl-issue-dim">{issue.dimension}</span>
                {issue.location && <span className="hl-issue-loc">{issue.location}</span>}
              </div>
              <div className="hl-issue-detail">{issue.detail}</div>
              {issue.suggestion && <div className="hl-issue-suggest">建议：{issue.suggestion}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ReviewBoard({ projectName }: { projectName: string }) {
  const [data, setData] = useState<ProjectDetail | null>(null);

  useEffect(() => {
    hanlinFetch(`/projects/${encodeURIComponent(projectName)}`)
      .then((d) => setData(d as ProjectDetail))
      .catch(() => {});
  }, [projectName]);

  if (!data) return <div className="hl-loading">加载中...</div>;

  const { reviewSummary, chapters } = data;

  return (
    <div className="hl-review-board">
      <div className="hl-rb-summary">
        <div className="hl-rb-stat">
          <div className="hl-rb-num" style={{ color: '#ff5270' }}>{reviewSummary.totalFatal}</div>
          <div className="hl-rb-lbl">致命问题</div>
        </div>
        <div className="hl-rb-stat">
          <div className="hl-rb-num" style={{ color: '#ff9a6a' }}>{reviewSummary.totalImportant}</div>
          <div className="hl-rb-lbl">重要问题</div>
        </div>
        <div className="hl-rb-stat">
          <div className="hl-rb-num" style={{ color: '#52b788' }}>{reviewSummary.totalSuggestion}</div>
          <div className="hl-rb-lbl">优化建议</div>
        </div>
        <div className="hl-rb-stat">
          <div className="hl-rb-num" style={{ color: '#2ecc8a' }}>{reviewSummary.totalReviewed}</div>
          <div className="hl-rb-lbl">已审核章节</div>
        </div>
      </div>
      <div className="hl-rb-chapters">
        <h4>各章审核状态</h4>
        <div className="hl-rb-list">
          {chapters.map((ch) => (
            <div key={ch.id} className="hl-rb-item">
              <span style={{ color: chapterStatusDot(ch.status) }}>●</span>
              <span className="hl-rb-ch-title">{ch.title || `第${ch.id}章`}</span>
              <span className="hl-rb-ch-words">{ch.wordCount}字</span>
              {ch.hasReview && (
                <span className="hl-rb-ch-review" style={{ color: levelColor(ch.reviewResult === 'pass' ? '建议' : ch.reviewResult === 'reject' ? '致命' : '重要') }}>
                  {ch.reviewResult === 'pass' ? '通过' : ch.reviewResult === 'reject' ? '需重写' : '需修改'}
                </span>
              )}
            </div>
          ))}
          {chapters.length === 0 && <div className="hl-empty">暂无审核记录</div>}
        </div>
      </div>
    </div>
  );
}

function WritingStatus({ projectName }: { projectName: string }) {
  const [progress, setProgress] = useState<Record<string, unknown> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProgress = useCallback(() => {
    hanlinFetch(`/projects/${encodeURIComponent(projectName)}/progress`)
      .then((d) => setProgress(d as { progress: Record<string, unknown> }))
      .catch(() => {});
  }, [projectName]);

  useEffect(() => {
    loadProgress();
    timerRef.current = setInterval(loadProgress, 3000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [loadProgress]);

  if (!progress) return <div className="hl-loading">加载中...</div>;

  const p = progress as Record<string, unknown>;
  const chapterStatus = (p.chapterStatus as Record<string, string>) || {};
  const totalCh = (p.totalChapters as number) || 0;
  const currentCh = (p.currentChapter as number) || 0;
  const totalW = (p.totalWords as number) || 0;
  const phase = (p.currentPhase as string) || '';
  const agent = (p.currentAgent as string) || '';
  const task = (p.currentTask as string) || '';

  const phaseLabel: Record<string, string> = {
    architecture: '架构设计', writing: '逐章写作', reviewing: '审核校对', completed: '已完成',
  };
  const agentLabel: Record<string, string> = {
    hanlin_xiuzhuan: '修撰', hanlin_bianxiu: '编修', hanlin_jiantao: '检讨',
  };

  const statusList = Object.entries(chapterStatus).map(([ch, st]) => ({
    chapter: parseInt(ch),
    status: st,
  })).sort((a, b) => a.chapter - b.chapter);

  return (
    <div className="hl-writing-status">
      <div className="hl-ws-header">
        <div className="hl-ws-phase">
          当前阶段：<strong>{phaseLabel[phase] || phase}</strong>
        </div>
        {agent && (
          <div className="hl-ws-agent">
            执行者：<strong>{agentLabel[agent] || agent}</strong>
          </div>
        )}
      </div>

      {task && (
        <div className="hl-ws-task">{task}</div>
      )}

      <div className="hl-ws-stats">
        <StatCard icon="📖" label="当前章节" value={`${currentCh} / ${totalCh}`} />
        <StatCard icon="✍️" label="总字数" value={`${totalW.toLocaleString()}`} />
        <StatCard icon="✅" label="已完成" value={String(statusList.filter((s) => s.status === 'done').length)} />
        <StatCard icon="🔄" label="进行中" value={String(statusList.filter((s) => s.status !== 'done').length)} />
      </div>

      <div className="hl-ws-overall-bar">
        <div className="hl-ws-bar-track">
          <div
            className="hl-ws-bar-fill"
            style={{ width: `${totalCh > 0 ? Math.round((currentCh / totalCh) * 100) : 0}%` }}
          />
        </div>
        <span className="hl-ws-bar-label">
          总进度 {totalCh > 0 ? Math.round((currentCh / totalCh) * 100) : 0}%
        </span>
      </div>

      <div className="hl-ws-chapter-grid">
        {statusList.map((s) => (
          <div
            key={s.chapter}
            className="hl-ws-chip"
            style={{
              borderColor: chapterStatusDot(s.status),
              color: chapterStatusDot(s.status),
            }}
          >
            {s.chapter}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Panel ──

export default function HanlinPanel() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [stats, setStats] = useState({ totalProjects: 0, totalWords: 0, activeProjects: 0 });
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [projectDetail, setProjectDetail] = useState<ProjectDetail | null>(null);
  const [subTab, setSubTab] = useState<SubTab>('outline');
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 加载项目列表
  const loadProjects = useCallback(async () => {
    try {
      const data = await hanlinFetch('/projects') as { projects: ProjectSummary[]; stats: typeof stats };
      setProjects(data.projects);
      setStats(data.stats);
    } catch {
      setError('翰林院服务未启动 (端口 7892)');
    }
  }, []);

  // 加载项目详情
  const loadProjectDetail = useCallback(async (name: string) => {
    try {
      const data = await hanlinFetch(`/projects/${encodeURIComponent(name)}`) as ProjectDetail;
      setProjectDetail(data);
    } catch {
      setProjectDetail(null);
    }
  }, []);

  // 进入面板时启动轮询
  useEffect(() => {
    loadProjects();
    pollRef.current = setInterval(loadProjects, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadProjects]);

  // 选中项目
  useEffect(() => {
    if (selectedProject) {
      setLoading(true);
      loadProjectDetail(selectedProject).finally(() => setLoading(false));
    } else {
      setProjectDetail(null);
    }
  }, [selectedProject, loadProjectDetail]);

  // 子Tab切换
  useEffect(() => {
    if (subTab === 'chapters') {
      setSelectedChapter(projectDetail?.chapters[0]?.id ?? null);
    }
  }, [subTab, projectDetail]);

  const handleProjectCreated = () => {
    setShowCreate(false);
    loadProjects();
  };

  const subTabs: { key: SubTab; label: string; icon: string }[] = [
    { key: 'outline', label: '大纲', icon: '📋' },
    { key: 'worldview', label: '世界观', icon: '🌍' },
    { key: 'characters', label: '人物档案', icon: '👤' },
    { key: 'chapters', label: '章节', icon: '📖' },
    { key: 'writing', label: '写作进度', icon: '✍️' },
    { key: 'review', label: '终审看板', icon: '🔍' },
  ];

  return (
    <div className="hl-panel">
      {/* 左侧边栏 */}
      <div className="hl-sidebar">
        <div className="hl-sidebar-header">
          <div className="hl-sidebar-title">📚 翰林院</div>
          <div className="hl-sidebar-sub">掌院学士 · 从三品</div>
        </div>
        <div className="hl-sidebar-stats">
          <div className="hl-ss-item">
            <span className="hl-ss-val">{stats.totalProjects}</span>
            <span className="hl-ss-lbl">项目</span>
          </div>
          <div className="hl-ss-item">
            <span className="hl-ss-val">{stats.totalWords.toLocaleString()}</span>
            <span className="hl-ss-lbl">总字数</span>
          </div>
          <div className="hl-ss-item">
            <span className="hl-ss-val">{stats.activeProjects}</span>
            <span className="hl-ss-lbl">进行中</span>
          </div>
        </div>
        <div className="hl-project-list">
          {projects.map((p) => (
            <ProjectCard
              key={p.name}
              project={p}
              active={selectedProject === p.name}
              onClick={() => { setSelectedProject(p.name); setSubTab('outline'); }}
            />
          ))}
          {projects.length === 0 && (
            <div className="hl-empty">暂无项目</div>
          )}
        </div>
        <button className="hl-btn hl-btn-add" onClick={() => setShowCreate(true)}>
          + 新建项目
        </button>
      </div>

      {/* 右侧主区域 */}
      <div className="hl-main">
        {error && !selectedProject ? (
          <div className="hl-error-banner">{error}</div>
        ) : selectedProject && projectDetail ? (
          <>
            {/* 顶部头 */}
            <div className="hl-main-header">
              <div className="hl-mh-left">
                <h2 className="hl-mh-title">{selectedProject}</h2>
                <span
                  className="hl-mh-status"
                  style={{ color: statusDot(projectDetail.meta.status as string) }}
                >
                  ● {statusLabel(projectDetail.meta.status as string)}
                </span>
              </div>
              <div className="hl-mh-right">
                <span>{projectDetail.chapters.length} 章</span>
                <span>
                  {projectDetail.chapters.reduce((sum, ch) => sum + ch.wordCount, 0).toLocaleString()} 字
                </span>
                {projectDetail.reviewSummary && (
                  <span>
                    🔴{projectDetail.reviewSummary.totalFatal} 🟡{projectDetail.reviewSummary.totalImportant} 🟢{projectDetail.reviewSummary.totalSuggestion}
                  </span>
                )}
              </div>
            </div>

            {/* 子Tab */}
            <div className="hl-sub-tabs">
              {subTabs.map((st) => (
                <div
                  key={st.key}
                  className={`hl-sub-tab ${subTab === st.key ? 'active' : ''}`}
                  onClick={() => setSubTab(st.key)}
                >
                  {st.icon} {st.label}
                </div>
              ))}
            </div>

            {/* 子视图 */}
            <div className="hl-view">
              {subTab === 'outline' && (
                projectDetail.hasOutline
                  ? <OutlineEditor projectName={selectedProject} />
                  : <div className="hl-empty">大纲尚未生成，等待修撰设计...</div>
              )}

              {subTab === 'worldview' && (
                <WorldViewLoader projectName={selectedProject} hasData={projectDetail.hasWorldview} />
              )}

              {subTab === 'characters' && (
                <CharactersLoader projectName={selectedProject} hasData={projectDetail.hasCharacters} />
              )}

              {subTab === 'chapters' && (
                <div className="hl-chapters-view">
                  <div className="hl-ch-list">
                    {projectDetail.chapters.map((ch) => (
                      <div
                        key={ch.id}
                        className={`hl-ch-item ${selectedChapter === ch.id ? 'active' : ''}`}
                        onClick={() => setSelectedChapter(ch.id)}
                      >
                        <span className="hl-ch-dot" style={{ color: chapterStatusDot(ch.status) }}>●</span>
                        <div className="hl-ch-info">
                          <div className="hl-ch-title">{ch.title || `第${ch.id}章`}</div>
                          <div className="hl-ch-meta">
                            <span>{ch.wordCount}字</span>
                            {ch.hasReview && (
                              <span style={{ color: levelColor(ch.reviewResult === 'pass' ? '建议' : ch.reviewResult === 'reject' ? '致命' : '重要') }}>
                                {chapterStatusLabel(ch.reviewResult)}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                    {projectDetail.chapters.length === 0 && (
                      <div className="hl-empty">暂无章节，等待编修执笔...</div>
                    )}
                  </div>
                  <div className="hl-ch-reader">
                    {selectedChapter ? (
                      <ChapterReader
                        projectName={selectedProject}
                        chapterId={selectedChapter}
                        title={projectDetail.chapters.find((c) => c.id === selectedChapter)?.title || `第${selectedChapter}章`}
                      />
                    ) : (
                      <div className="hl-empty">选择章节查看内容</div>
                    )}
                  </div>
                </div>
              )}

              {subTab === 'writing' && (
                <WritingStatus projectName={selectedProject} />
              )}

              {subTab === 'review' && (
                <ReviewBoard projectName={selectedProject} />
              )}
            </div>
          </>
        ) : (
          <div className="hl-welcome">
            <div className="hl-welcome-icon">📚</div>
            <h2>翰林院 · 小说创作</h2>
            <p>请从左侧选择一个项目，或新建一个小说项目开始创作。</p>
            <p className="hl-welcome-hint">
              创作流程：掌院接旨 → 修撰写架构 → 皇帝审阅大纲 → 编修逐章写作 → 检讨审核 → 全书完成
            </p>
          </div>
        )}
      </div>

      {/* 新建项目弹窗 */}
      {showCreate && <CreateProjectModal onClose={() => setShowCreate(false)} onCreated={handleProjectCreated} />}
    </div>
  );
}

// ── Lazy Loaders ──

function WorldViewLoader({ projectName, hasData }: { projectName: string; hasData: boolean }) {
  const [content, setContent] = useState('');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (hasData) {
      hanlinFetch(`/projects/${encodeURIComponent(projectName)}/worldview`)
        .then((d) => { setContent((d as { content: string }).content); setLoaded(true); })
        .catch(() => setLoaded(true));
    }
  }, [projectName, hasData]);

  if (!hasData) return <div className="hl-empty">世界观尚未生成，等待修撰设计...</div>;
  if (!loaded) return <div className="hl-loading">加载中...</div>;
  return <MarkdownViewer content={content} title="世界观设定" />;
}

function CharactersLoader({ projectName, hasData }: { projectName: string; hasData: boolean }) {
  const [content, setContent] = useState('');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (hasData) {
      hanlinFetch(`/projects/${encodeURIComponent(projectName)}/characters`)
        .then((d) => { setContent((d as { content: string }).content); setLoaded(true); })
        .catch(() => setLoaded(true));
    }
  }, [projectName, hasData]);

  if (!hasData) return <div className="hl-empty">人物档案尚未生成，等待修撰设计...</div>;
  if (!loaded) return <div className="hl-loading">加载中...</div>;
  return <MarkdownViewer content={content} title="人物档案" />;
}
