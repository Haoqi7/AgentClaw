/**
 * 📦 产出阁 — 任务产出管理面板
 *
 * 功能：
 * 1. 左侧任务列表（按任务标题搜索过滤）
 * 2. 右侧选中任务的产出文件列表，按部门分组
 * 3. 支持预览和下载
 * 4. 文件类型图标 + 部门颜色标签
 *
 * 侵入点：零侵入，仅在 App.tsx 中注册为 Tab 即可使用
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useStore, deptColor, type Task } from '../store';
import { api } from '../api';

/* ═══════════════════════════════════════════════════════════
   Types
   ═══════════════════════════════════════════════════════════ */

interface Artifact {
  name: string;
  dept: string;
  type: string;       // file ext
  size: number;
  path: string;
  uploadedAt: string;
}

interface TaskOutputData {
  ok: boolean;
  taskId?: string;
  taskTitle?: string;
  artifacts?: Artifact[];
  totalSize?: number;
  error?: string;
}

/* ═══════════════════════════════════════════════════════════
   Constants
   ═══════════════════════════════════════════════════════════ */

const DEPT_NAMES = [
  '中书省', '门下省', '尚书省',
  '工部', '兵部', '户部', '礼部', '刑部', '吏部',
];

const FILE_ICONS: Record<string, string> = {
  py: '🐍', js: '📜', ts: '🟦', tsx: '🟦', jsx: '🟦',
  md: '📝', txt: '📄', json: '📋', yaml: '⚙️', yml: '⚙️',
  csv: '📊', xlsx: '📊', xls: '📊',
  pdf: '📕', doc: '📘', docx: '📘',
  png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', svg: '🎨',
  zip: '📦', tar: '📦', gz: '📦',
  sh: '⚡', dockerfile: '🐳', sql: '🗃️', html: '🌐', css: '🎨',
};

const SIZE_UNITS = ['B', 'KB', 'MB', 'GB'];
function formatSize(bytes: number): string {
  if (bytes <= 0) return '0 B';
  let i = 0;
  let s = bytes;
  while (s >= 1024 && i < SIZE_UNITS.length - 1) { s /= 1024; i++; }
  return `${s.toFixed(i > 0 ? 1 : 0)} ${SIZE_UNITS[i]}`;
}

function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return FILE_ICONS[ext] || '📄';
}

function fileExt(name: string): string {
  return name.split('.').pop()?.toLowerCase() || '';
}

/* ═══════════════════════════════════════════════════════════
   Component
   ═══════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════
   Markdown 渲染样式
   ═══════════════════════════════════════════════════════════ */
const MD_STYLES = `.markdown-body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.7;color:#d0d0d0}
.markdown-body h1,.markdown-body h2,.markdown-body h3{margin:1em 0 .6em;font-weight:600;color:#e8e8e8;border-bottom:1px solid #333;padding-bottom:.3em}
.markdown-body h1{font-size:1.5em}.markdown-body h2{font-size:1.3em}.markdown-body h3{font-size:1.15em}
.markdown-body p{margin:.6em 0}.markdown-body ul,.markdown-body ol{padding-left:2em;margin:.5em 0}
.markdown-body li{margin:.2em 0}.markdown-body code{background:#2a2a4a;padding:2px 6px;border-radius:4px;font-size:.9em;color:#9ae}
.markdown-body pre{background:#12121f;padding:14px;border-radius:8px;overflow-x:auto;margin:.8em 0}
.markdown-body pre code{background:none;padding:0;color:#d0d0d0}
.markdown-body blockquote{border-left:4px solid #4a6fff;padding:.5em 1em;margin:.8em 0;background:rgba(74,111,255,.06);color:#b0b0cc}
.markdown-body table{border-collapse:collapse;width:100%;margin:.8em 0}
.markdown-body th,.markdown-body td{border:1px solid #333;padding:8px 12px;text-align:left}
.markdown-body th{background:#22223a;font-weight:600;color:#e0e0e0}
.markdown-body a{color:#6a9eff;text-decoration:none}.markdown-body a:hover{text-decoration:underline}
.markdown-body hr{border:none;border-top:1px solid #333;margin:1.5em 0}
.markdown-body img{max-width:100%;border-radius:8px}`;

function ensureMarked() {
  if ((window as any).marked) return;
  if (!document.getElementById('marked-cdn')) {
    const s = document.createElement('script');
    s.id = 'marked-cdn';
    s.src = 'https://cdn.jsdelivr.net/npm/marked/marked.min.js';
    document.head.appendChild(s);
  }
  if (!document.getElementById('marked-style')) {
    const st = document.createElement('style');
    st.id = 'marked-style';
    st.textContent = MD_STYLES;
    document.head.appendChild(st);
  }
}

export default function TaskOutputPanel() {
  const liveStatus = useStore((s) => s.liveStatus);
  const toast = useStore((s) => s.toast);
  useEffect(() => { ensureMarked(); }, []);

  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [outputData, setOutputData] = useState<TaskOutputData | null>(null);
  const [loading, setLoading] = useState(false);
  const [deptFilter, setDeptFilter] = useState<string>('');
  const [preview, setPreview] = useState<{ name: string; content: string } | null>(null);

  // Derive task list
  const tasks = liveStatus?.tasks || [];
  const filtered = tasks.filter((t: Task) => {
    if (!/^JJC-/i.test(t.id || '')) return false;
    if (search && !t.title.toLowerCase().includes(search.toLowerCase()) && !t.id.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const selectedTask = filtered.find((t: Task) => t.id === selectedId) || null;

  // Load output data when task selected
  const loadOutput = useCallback(async (taskId: string) => {
    setLoading(true);
    try {
      const data = await api.taskOutputList(taskId);
      setOutputData(data);
    } catch (e) {
      console.error('loadOutput error:', e);
      setOutputData({ ok: false, error: '加载失败' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) loadOutput(selectedId);
    else setOutputData(null);
  }, [selectedId, loadOutput]);

  // Auto-select first task
  useEffect(() => {
    if (!selectedId && filtered.length > 0) {
      setSelectedId(filtered[0].id);
    }
  }, [filtered.length]);



  // Group artifacts by dept
  const artifacts = outputData?.artifacts || [];
  const grouped = artifacts.reduce<Record<string, Artifact[]>>((acc, a) => {
    const key = a.dept || '未分类';
    if (!acc[key]) acc[key] = [];
    acc[key].push(a);
    return acc;
  }, {});
  const filteredGrouped = deptFilter
    ? Object.fromEntries([[deptFilter, grouped[deptFilter] || []]])
    : grouped;

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 110px)', gap: 0 }}>
      {/* ── Left: Task List ── */}
      <div style={{
        width: 320, minWidth: 280, borderRight: '1px solid var(--border, #2a2a3a)',
        display: 'flex', flexDirection: 'column', background: 'var(--bg2, #1a1a2e)',
      }}>
        <div style={{ padding: '12px', borderBottom: '1px solid var(--border, #2a2a3a)' }}>
          <input
            type="text"
            placeholder="搜索任务标题 / ID ..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: '100%', boxSizing: 'border-box', padding: '8px 12px',
              borderRadius: 6, border: '1px solid var(--border, #333)',
              background: 'var(--bg, #12121f)', color: 'var(--fg, #e0e0e0)',
              fontSize: 13, outline: 'none',
            }}
          />
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
          {filtered.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted, #666)', fontSize: 13 }}>
              暂无任务
            </div>
          )}
          {filtered.map((t: Task) => (
            <div
              key={t.id}
              onClick={() => setSelectedId(t.id)}
              style={{
                padding: '10px 14px', cursor: 'pointer',
                background: selectedId === t.id ? 'rgba(100, 140, 255, 0.12)' : 'transparent',
                borderLeft: selectedId === t.id ? '3px solid #6a9eff' : '3px solid transparent',
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                if (selectedId !== t.id) (e.currentTarget.style.background = 'rgba(255,255,255,0.04)');
              }}
              onMouseLeave={(e) => {
                if (selectedId !== t.id) (e.currentTarget.style.background = 'transparent');
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--muted, #888)', fontFamily: 'monospace' }}>
                  {t.id}
                </span>
                <span style={{
                  fontSize: 10, padding: '1px 6px', borderRadius: 4,
                  background: deptColor(t.org) + '22', color: deptColor(t.org),
                  fontWeight: 600,
                }}>
                  {t.org}
                </span>
              </div>
              <div style={{
                fontSize: 13, color: 'var(--fg, #e0e0e0)', marginTop: 4,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {t.title}
              </div>
              {t.state === 'Done' && (
                <div style={{ fontSize: 11, color: '#2ecc8a', marginTop: 2 }}>
                  ✅ 已完成
                </div>
              )}
            </div>
          ))}
        </div>
        <div style={{
          padding: '8px 14px', borderTop: '1px solid var(--border, #2a2a3a)',
          fontSize: 12, color: 'var(--muted, #666)',
        }}>
          共 {filtered.length} 道旨意
        </div>
      </div>

      {/* ── Right: Output Panel ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {!selectedTask ? (
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: 'var(--muted, #555)', fontSize: 14,
          }}>
            ← 请选择一个任务查看产出
          </div>
        ) : (
          <>
            {/* Header */}
            <div style={{
              padding: '14px 20px', borderBottom: '1px solid var(--border, #2a2a3a)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--fg, #e0e0e0)' }}>
                  📦 {selectedTask.title}
                </div>
                <div style={{ fontSize: 12, color: 'var(--muted, #888)', marginTop: 2, fontFamily: 'monospace' }}>
                  {selectedTask.id} · {selectedTask.org} · {artifacts.length} 个文件
                  {outputData?.totalSize ? ` · ${formatSize(outputData.totalSize)}` : ''}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {/* Dept filter */}
                <select
                  value={deptFilter}
                  onChange={(e) => setDeptFilter(e.target.value)}
                  style={{
                    padding: '5px 10px', borderRadius: 6, fontSize: 12,
                    background: 'var(--bg, #12121f)', color: 'var(--fg, #ccc)',
                    border: '1px solid var(--border, #333)', outline: 'none', cursor: 'pointer',
                  }}
                >
                  <option value="">全部部门</option>
                  {DEPT_NAMES.map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Content */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
              {loading && (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--muted, #888)' }}>
                  加载中...
                </div>
              )}

              {!loading && artifacts.length === 0 && (
                <div style={{
                  textAlign: 'center', padding: '60px 20px', color: 'var(--muted, #555)',
                }}>
                  <div style={{ fontSize: 48, marginBottom: 12 }}>📦</div>
                  <div style={{ fontSize: 14, marginBottom: 6 }}>暂无产出文件</div>
                  <div style={{ fontSize: 12 }}>
                    任务执行过程中各部门的产出物将自动显示在此处
                  </div>
                </div>
              )}

              {!loading && artifacts.length > 0 && Object.entries(filteredGrouped).map(([dept, files]) => (
                <div key={dept} style={{ marginBottom: 20 }}>
                  {/* Dept header */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    marginBottom: 8, paddingBottom: 6,
                    borderBottom: `2px solid ${deptColor(dept)}44`,
                  }}>
                    <span style={{
                      display: 'inline-block', width: 10, height: 10, borderRadius: 3,
                      background: deptColor(dept),
                    }} />
                    <span style={{ fontSize: 13, fontWeight: 600, color: deptColor(dept) }}>
                      {dept}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--muted, #666)' }}>
                      {files.length} 个文件
                    </span>
                  </div>

                  {/* File cards */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {files.map((f) => (
                      <div key={f.name} style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: '10px 14px', borderRadius: 8,
                        background: 'var(--bg, #12121f)',
                        border: '1px solid var(--border, #2a2a3a)',
                        transition: 'border-color 0.15s',
                      }}
                        onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#444'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border, #2a2a3a)'; }}
                      >
                        {/* Icon */}
                        <span style={{ fontSize: 22, flexShrink: 0 }}>
                          {fileIcon(f.name)}
                        </span>

                        {/* Info */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{
                            fontSize: 13, color: 'var(--fg, #e0e0e0)',
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }}>
                            {f.name}
                          </div>
                          <div style={{ fontSize: 11, color: 'var(--muted, #666)', marginTop: 2 }}>
                            {formatSize(f.size)}
                            {f.uploadedAt && ` · ${new Date(f.uploadedAt).toLocaleString('zh-CN')}`}
                          </div>
                        </div>

                        {/* Actions */}
                        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                          {/* Preview (text files) */}
                          {['md', 'txt', 'json', 'yaml', 'yml', 'py', 'js', 'ts', 'sh', 'csv', 'html', 'css', 'sql', 'log'].includes(fileExt(f.name)) && (
                            <button
                              onClick={async () => {
                                try {
                                  const data = await api.taskOutputPreview(selectedId!, f.name);
                                  if (data.ok && data.content) {
                                    setPreview({ name: f.name, content: data.content as string });
                                  } else {
                                    toast(data.error || '文件为空', 'err');
                                  }
                                } catch (e) { toast('预览失败', 'err'); }
                              }}
                              style={{
                                padding: '4px 10px', borderRadius: 4, fontSize: 11, cursor: 'pointer',
                                background: 'transparent', border: '1px solid #444', color: '#aaa',
                              }}
                            >
                              预览
                            </button>
                          )}
                          {/* Download */}
                          <a
                            href={`/api/outputs/${encodeURIComponent(selectedId!)}/download/${encodeURIComponent(f.name)}`}
                            download={f.name}
                            style={{
                              padding: '4px 10px', borderRadius: 4, fontSize: 11,
                              background: 'rgba(46, 204, 138, 0.1)', border: '1px solid rgba(46, 204, 138, 0.3)',
                              color: '#2ecc8a', textDecoration: 'none', display: 'inline-block',
                            }}
                          >
                            下载
                          </a>

                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* ── Preview Modal ── */}
      {preview && (
        <div
          onClick={() => setPreview(null)}
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.6)', zIndex: 9999,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '80vw', maxWidth: 900, height: '78vh', maxHeight: 700,
              background: '#1a1a2e', borderRadius: 12,
              border: '1px solid #333', display: 'flex', flexDirection: 'column',
              boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
            }}
          >
            {/* Modal header */}
            <div style={{
              padding: '14px 20px', borderBottom: '1px solid #2a2a3a',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0,
            }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#e0e0e0' }}>
                {fileIcon(preview.name)} {preview.name}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <a
                  href={`/api/outputs/${encodeURIComponent(selectedId!)}/download/${encodeURIComponent(preview.name)}`}
                  download={preview.name}
                  style={{
                    padding: '4px 12px', borderRadius: 4, fontSize: 12, textDecoration: 'none',
                    background: 'rgba(46,204,138,0.1)', border: '1px solid rgba(46,204,138,0.3)', color: '#2ecc8a',
                  }}
                >下载</a>
                <button
                  onClick={() => setPreview(null)}
                  style={{
                    padding: '4px 12px', borderRadius: 4, fontSize: 14, cursor: 'pointer',
                    background: 'rgba(255,82,112,0.1)', border: '1px solid rgba(255,82,112,0.3)', color: '#ff5270',
                  }}
                >✕</button>
              </div>
            </div>
            {/* Modal body */}
            <div style={{
              flex: 1, overflow: 'auto', padding: '20px 24px',
              fontSize: 14, lineHeight: 1.7, color: '#d0d0d0',
            }}>
              {fileExt(preview.name) === 'md' ? (
                <div
                  className="markdown-body"
                  dangerouslySetInnerHTML={{
                    __html: typeof window !== 'undefined' && (window as any).marked
                      ? (window as any).marked.parse(preview.content)
                      : preview.content.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br/>'),
                  }}
                />
              ) : (
                <pre style={{
                  margin: 0, fontFamily: 'monospace', fontSize: 13,
                  whiteSpace: 'pre-wrap', wordWrap: 'break-word', color: '#d0d0d0',
                }}>{preview.content}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
