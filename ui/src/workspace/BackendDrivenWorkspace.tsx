import { invoke } from '@tauri-apps/api/core';
import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import type { BookflowCommand, BookflowSnapshot, FrontendPreferences } from '../domain/bookflow-contract';
import type { RouteId } from '../shell/navigation';
import { bookflowBridge } from '../state/useBookflowSnapshot';

type Row = Record<string, unknown>;
type ModelRole = 'language' | 'vision';
type ModelDraft = { baseUrl: string; apiKey: string; model: string };

const MODEL_ROLES: readonly { role: ModelRole; label: string; description: string }[] = [
  { role: 'language', label: '语言模型', description: '可配置兼容的语言模型 API' },
  { role: 'vision', label: '视觉模型', description: '可配置兼容图像输入的视觉模型 API' },
];

const asRows = (value: readonly unknown[] | undefined): Row[] =>
  (value ?? []).filter((item): item is Row => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
const text = (value: unknown, fallback = '—') => typeof value === 'string' && value ? value : fallback;

const COLUMN_LABELS: Record<string, string> = {
  name: '项目名称', state: '状态', updated_at: '更新时间', filename: '文件名', page_count: '页数',
  source_language: '源语言', status: '状态', stage: '当前阶段', progress: '进度', attempts: '尝试次数',
  provider_id: '服务', provider_type: '类型', base_url: '服务地址', model: '模型',
  purpose: '用途', credential_source: '凭据来源', credential_present: '已配置', valid: '配置有效',
  connection_status: '连接状态', last_test_at: '最后测试', capabilities: '能力', sequence: '序号',
  timestamp: '时间', event_type: '事件', payload: '详情', format: '格式', displayName: '文件',
  display_name: '文件', role: '角色', build_id: '构建', size: '大小', version: '版本', generated_at: '生成时间',
  openable: '可打开', application_id: '应用记录', applied_at: '应用时间', applied_items: '变更项', undone: '已撤销',
};

const STATUS_LABELS: Record<string, string> = {
  open: '已打开', closed: '已关闭', ready: '就绪', queued: '等待处理', running: '正在处理',
  paused: '已暂停', completed: '已完成', failed: '失败', cancelled: '已取消', recovering: '正在恢复',
  imported: '已导入', linked: '已关联', true: '是', false: '否',
};

function displayValue(value: unknown, column: string) {
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (column === 'progress' && typeof value === 'number') return `${Math.round(value * 100)}%`;
  if (Array.isArray(value)) return value.join('、');
  if (value && typeof value === 'object') return '查看技术详情';
  const raw = String(value ?? '—');
  return STATUS_LABELS[raw] ?? raw;
}

function DataTable({ rows, columns }: { rows: Row[]; columns: string[] }) {
  if (!rows.length) return <p className="backend-empty">暂无后端记录</p>;
  return (
    <div className="backend-table-wrap"><table className="backend-table"><thead><tr>
      {columns.map((column) => <th key={column}>{COLUMN_LABELS[column] ?? column}</th>)}
    </tr></thead><tbody>{rows.map((row, index) => <tr key={`${text(
      row.id ?? row.event_id ?? row.artifact_id ?? row.application_id ?? row.package_id
      ?? row.source_id ?? row.batch_id ?? row.job_id ?? row.project_id,
      'row',
    )}-${index}`}>
      {columns.map((column) => <td key={column}>{displayValue(row[column], column)}</td>)}
    </tr>)}</tbody></table></div>
  );
}

function TechnicalDetails({ value }: { value: unknown }) {
  return <details className="technical-details"><summary>技术详情</summary><pre className="backend-json">{JSON.stringify(value, null, 2)}</pre></details>;
}

function AssetImage({ assetId, alt, className, style }: {
  assetId: string; alt: string; className?: string; style?: CSSProperties;
}) {
  const [src, setSrc] = useState('');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setSrc(''); setError('');
    if (!assetId) return () => { active = false; };
    void bookflowBridge.command({ type: 'asset.resolve', assetId }).then(({ data }) => {
      const result = data.result as Row | undefined;
      if (active && data.accepted && typeof result?.data_url === 'string') setSrc(result.data_url);
      else if (active) setError(data.reasonCode ?? 'asset_resolution_failed');
    }).catch(() => { if (active) setError('asset_transport_failed'); });
    return () => { active = false; };
  }, [assetId, retry]);
  if (error) return <span className="asset-error">加载失败：{error}<button onClick={() => setRetry((value) => value + 1)}>重试</button></span>;
  if (!src) return <span className="asset-loading">正在加载…</span>;
  return <img className={className} src={src} alt={alt} style={style} draggable={false} />;
}

function SourceGallery({ source }: { source?: Row }) {
  const thumbnails = asRows(source?.thumbnails as unknown[] | undefined);
  const [pageIndex, setPageIndex] = useState(0);
  const [scale, setScale] = useState(1);
  useEffect(() => { setPageIndex(0); setScale(1); }, [source?.source_id]);
  if (!source) return <p className="backend-empty">导入书籍后，这里会显示真实封面、页数和全部页面缩略图。</p>;
  const boundedIndex = Math.max(0, Math.min(pageIndex, Math.max(0, thumbnails.length - 1)));
  const current = thumbnails[boundedIndex];
  const virtualStart = thumbnails.length > 100 ? Math.max(0, boundedIndex - 25) : 0;
  const visibleThumbnails = thumbnails.length > 100
    ? thumbnails.slice(virtualStart, Math.min(thumbnails.length, virtualStart + 51)) : thumbnails;
  return <section className="source-gallery">
    <header><div>{typeof source.cover_asset_id === 'string' && <AssetImage className="source-cover" assetId={source.cover_asset_id} alt={`${text(source.filename)} 封面`} />}</div>
      <dl><div><dt>书名</dt><dd>{text(source.filename).replace(/\.[^.]+$/, '')}</dd></div><div><dt>文件</dt><dd>{text(source.filename)}</dd></div><div><dt>页数</dt><dd>{String(source.page_count ?? 0)}</dd></div><div><dt>识别语言</dt><dd>{text(source.source_language)}</dd></div></dl></header>
    <div className="source-viewer-tools">
      <button disabled={boundedIndex <= 0} onClick={() => setPageIndex((value) => Math.max(0, value - 1))}>前一页</button>
      <span>第 {String(current?.page ?? 0)} / {thumbnails.length} 页</span>
      <button disabled={boundedIndex >= thumbnails.length - 1} onClick={() => setPageIndex((value) => Math.min(thumbnails.length - 1, value + 1))}>后一页</button>
      <button onClick={() => setScale(.75)}>适合窗口</button><button onClick={() => setScale(1)}>实际大小 / 100%</button>
      <button onClick={() => setScale((value) => Math.min(4, value + .2))}>放大</button>
      <button onClick={() => setScale((value) => Math.max(.2, value - .2))}>缩小</button><span>{Math.round(scale * 100)}%</span>
    </div>
    <div className="source-page-pan" aria-label="原书页查看器">
      {current && <AssetImage assetId={text(current.asset_id, '')} alt={`第 ${current.page} 页原始页图`} style={{ transform: `scale(${scale})` }} />}
    </div>
    <div className="thumbnail-grid" aria-label="页面缩略图">{visibleThumbnails.map((item, offset) => {
      const index = virtualStart + offset;
      return <button className={index === boundedIndex ? 'thumbnail-selected' : ''} key={String(item.page)} onClick={() => setPageIndex(index)}>
        <AssetImage assetId={text(item.thumbnail_asset_id, '')} alt={`第 ${item.page} 页缩略图`} /><span>第 {String(item.page)} 页</span>
      </button>;
    })}</div>
  </section>;
}

function PipelineArtifacts({ job, route }: { job?: Row; route: RouteId }) {
  const details = job?.pipeline_details as Row | undefined;
  const artifacts = details?.artifacts as Row | undefined;
  const selected = route === 'ocr' ? artifacts?.page_intake
    : route === 'structure' ? { structure: artifacts?.book_structure, segments: artifacts?.segmentation_plan, publication: artifacts?.publication_reconstruction }
      : route === 'translationWorkflow' ? { plan: artifacts?.translation_plan, workspace: artifacts?.workspace_manifest }
        : artifacts;
  if (!selected || Object.keys(selected as Row).length === 0) return <p className="backend-empty">流水线运行到此阶段后会显示真实结果；当前没有可展示数据。</p>;
  return <div className="artifact-summary"><TechnicalDetails value={selected} /></div>;
}

function DocumentTextPreview({ job, preferences }: { job?: Row; preferences: FrontendPreferences }) {
  const [content, setContent] = useState('');
  const [error, setError] = useState('');
  const [metadata, setMetadata] = useState<Row | null>(null);
  const [query, setQuery] = useState('');
  const manifest = (job?.pipeline_details as Row | undefined)?.artifact_manifest as Row | undefined;
  const role = preferences.previewLayout === 'source' ? 'source_markdown'
    : preferences.previewLayout === 'target' ? 'target_markdown' : 'bilingual_markdown';
  const artifact = manifest?.[role] as Row | undefined;
  useEffect(() => {
    let active = true;
    setContent(''); setError(''); setMetadata(null);
    const artifactId = text(artifact?.artifact_id, '');
    if (!artifactId) return () => { active = false; };
    void bookflowBridge.command({ type: 'artifact.read', artifactId }).then(({ data }) => {
      const result = data.result as Row | undefined;
      if (active && data.accepted && typeof result?.content === 'string') {
        setContent(result.content); setMetadata(result);
      } else if (active) setError(`读取失败：${data.reasonCode ?? 'artifact_read_failed'}；artifact=${artifactId}`);
    }).catch(() => { if (active) setError(`读取传输失败；artifact=${artifactId}`); });
    return () => { active = false; };
  }, [artifact?.artifact_id]);
  const headings = useMemo(() => content.split('\n').filter((line) => /^#{1,6}\s/.test(line)).slice(0, 100), [content]);
  const matches = query ? content.toLocaleLowerCase().split(query.toLocaleLowerCase()).length - 1 : 0;
  if (error) return <p className="problem-banner">{error}</p>;
  if (!artifact) return <p className="backend-empty">当前 Job 尚无 {role} artifact；请确认构建状态与 ArtifactManifest。</p>;
  return <section className="document-readback">
    <div className="document-search"><input aria-label="搜索文档" placeholder="搜索" value={query} onChange={(event) => setQuery(event.target.value)} /><span>{query ? `${matches} 处匹配` : ''}</span></div>
    {headings.length > 0 && <nav aria-label="章节导航">{headings.map((heading, index) => <button key={`${heading}-${index}`} onClick={() => document.getElementById(`readback-heading-${index}`)?.scrollIntoView()}>{heading.replace(/^#+\s*/, '')}</button>)}</nav>}
    <article className="real-document-preview" style={{ fontSize: `${preferences.previewFontScale}%`, transform: `scale(${preferences.previewZoom / 100})` }}>
      {content ? content.split('\n').map((line, index) => /^#{1,6}\s/.test(line)
        ? <h3 id={`readback-heading-${headings.indexOf(line)}`} key={index}>{line.replace(/^#+\s*/, '')}</h3>
        : <span className={query && line.toLocaleLowerCase().includes(query.toLocaleLowerCase()) ? 'search-hit' : ''} key={index}>{line}{'\n'}</span>) : '正在读取…'}
    </article>
    <TechnicalDetails value={{ artifact_id: artifact.artifact_id, sha256: metadata?.sha256, build_id: metadata?.build_id, role: metadata?.role, job_id: job?.job_id }} />
  </section>;
}

function ImageViewer({ assetId }: { assetId: string }) {
  const [scale, setScale] = useState(1);
  return <section className="image-viewer">
    <div className="image-tools">
      <button disabled={!assetId} onClick={() => setScale((value) => Math.min(4, value + .2))}>放大</button>
      <button disabled={!assetId} onClick={() => setScale((value) => Math.max(.2, value - .2))}>缩小</button>
      <button disabled={!assetId} onClick={() => setScale(.75)}>适合窗口</button>
      <button disabled={!assetId} onClick={() => setScale(1)}>实际大小 / 100%</button>
      <span>{Math.round(scale * 100)}%</span>
    </div>
    {assetId ? <div className="image-pan-surface"><AssetImage assetId={assetId} style={{ transform: `scale(${scale})` }} alt="原始页图" /></div> : <p className="backend-empty">选择含受控 asset_id 的疑难页包后可查看原始页图。</p>}
  </section>;
}

export function BackendDrivenWorkspace({ route, snapshot, preferences }: {
  route: RouteId; snapshot: BookflowSnapshot; preferences: FrontendPreferences;
}) {
  const state = snapshot.backendState;
  const projects = asRows(state?.projects); const sources = asRows(state?.sources);
  const batches = asRows(state?.batches); const jobs = asRows(state?.jobs);
  const outputs = asRows(state?.outputs);
  const packages = asRows(state?.webAssistPackages); const history = asRows(state?.webAssistHistory);
  const events = asRows(state?.recentEvents); const providers = asRows(state?.providerConfiguration);
  const activeContext = state?.activeContext ?? {};
  const activeSource = sources.find((source) => source.source_id === activeContext.active_source_id);
  const activeJob = jobs.find((job) => job.job_id === activeContext.active_job_id);
  const [projectName, setProjectName] = useState('新建双语书项目');
  const [importPath, setImportPath] = useState('');
  const [selectedPackageId, setSelectedPackageId] = useState('');
  const [webTab, setWebTab] = useState<'glossary' | 'difficult' | 'history'>('glossary');
  const [operation, setOperation] = useState('就绪');
  const [detail, setDetail] = useState<Row | null>(null);
  const [modelDrafts, setModelDrafts] = useState<Record<ModelRole, ModelDraft>>({
    language: { baseUrl: '', apiKey: '', model: '' },
    vision: { baseUrl: '', apiKey: '', model: '' },
  });
  const [credentialVisible, setCredentialVisible] = useState<Record<ModelRole, boolean>>({ language: false, vision: false });
  const [credentialMessages, setCredentialMessages] = useState<Record<ModelRole, string>>({ language: '', vision: '' });
  const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  const providerRevision = JSON.stringify(providers.map((provider) => [provider.role, provider.base_url, provider.model]));
  useEffect(() => {
    setModelDrafts((current) => {
      const next = { ...current };
      for (const { role } of MODEL_ROLES) {
        const provider = providers.find((item) => item.role === role);
        next[role] = { baseUrl: text(provider?.base_url, ''), model: text(provider?.model, ''), apiKey: current[role].apiKey };
      }
      return next;
    });
  }, [providerRevision]);
  const filteredPackages = packages.filter((item) =>
    item.package_type === (webTab === 'glossary' ? 'glossary_review' : 'difficult_pages')
    && item.source_document_id === activeContext.active_source_id
  );
  const activePackageId = filteredPackages.some((item) => item.package_id === selectedPackageId)
    ? selectedPackageId : text(filteredPackages[0]?.package_id, '');
  const difficultImage = useMemo(() => {
    if (!detail || detail.package_type !== 'difficult_pages') return '';
    const item = asRows(detail.items as unknown[])[0];
    return item ? text(item.asset_id, '') : '';
  }, [detail]);

  const run = async (command: BookflowCommand) => {
    setOperation('处理中…');
    const response = (await bookflowBridge.command(command)).data;
    setOperation(response.accepted ? '操作成功' : `操作失败：${response.reasonCode ?? 'unknown'}`);
    if (response.accepted && response.result && typeof response.result === 'object') {
      const result = response.result as Row;
      if (result.skipped) {
        setOperation(
          result.reason === 'no_glossary_items'
            ? '没有待审校术语，不生成导出包'
            : '没有疑难页，不生成导出包',
        );
      }
      setDetail(result);
    }
    return response.accepted && response.result && typeof response.result === 'object' ? response.result as Row : null;
  };

  const pickWebAssistImport = async (mode: 'web_assist_file' | 'web_assist_folder') => {
    if (!inTauri) return;
    const paths = await invoke<string[]>('bookflow_pick_paths', { mode });
    if (paths[0]) setImportPath(paths[0]);
  };

  const openWebAssistFolder = async (packageId: string) => {
    if (!inTauri || !packageId) return;
    await invoke('bookflow_open_web_assist_package', { packageId });
  };

  const copyOfficialPrompt = async (packageId: string) => {
    const result = await run({ type: 'webAssist.get', packageId });
    const prompt = text(result?.official_prompt, '');
    if (!prompt) {
      setOperation('操作失败：未找到官方提示词');
      return;
    }
    await navigator.clipboard.writeText(prompt);
    setOperation('官方提示词已复制');
  };

  const credentialAction = async (role: ModelRole, action: 'set' | 'delete') => {
    if (!inTauri) return;
    setCredentialMessages((current) => ({ ...current, [role]: '正在安全配置…' }));
    try {
      await invoke('bookflow_credential_command', {
        action,
        role,
        secret: action === 'set' ? modelDrafts[role].apiKey : null,
      });
      setModelDrafts((current) => ({ ...current, [role]: { ...current[role], apiKey: '' } }));
      if (action === 'set') {
        const tested = (await bookflowBridge.command({ type: 'provider.test', role })).data;
        setCredentialMessages((current) => ({
          ...current,
          [role]: tested.accepted ? '已安全保存并立即生效；连接测试成功。' : '已安全保存并立即生效；连接测试失败，请检查密钥或服务配置。',
        }));
      } else {
        await bookflowBridge.command({ type: 'provider.test', role });
        setCredentialMessages((current) => ({ ...current, [role]: '已移除用户覆盖；如环境变量已配置，将自动回退。' }));
      }
    } catch {
      setModelDrafts((current) => ({ ...current, [role]: { ...current[role], apiKey: '' } }));
      setCredentialMessages((current) => ({ ...current, [role]: '安全配置失败；密钥未写入项目文件。' }));
    }
  };

  const saveModelRole = async (role: ModelRole) => {
    const draft = modelDrafts[role];
    const saved = await run({ type: 'provider.save', role, baseUrl: draft.baseUrl, model: draft.model });
    if (!saved) return;
    if (draft.apiKey) await credentialAction(role, 'set');
    else setCredentialMessages((current) => ({ ...current, [role]: '配置已保存；继续使用现有安全凭据。' }));
  };

  const title = {
    projects: '项目', sourceDocument: '原文', ocr: '文字识别', structure: '结构',
    translationWorkflow: '翻译工作流', comparison: '双语对照', outputFiles: '输出文件',
    logs: '日志', historyTasks: '历史任务', modelServices: '模型服务', settings: '设置', webAssist: '网页辅助', overview: '概览',
  }[route];

  if (route === 'projects') return <section className="backend-workspace surface"><h1>{title}</h1><p className="operation-status">{operation}</p>
    <div className="backend-form"><input value={projectName} onChange={(e) => setProjectName(e.target.value)} /><button onClick={() => run({ type: 'workspace.create', name: projectName })}>创建项目</button></div>
    <DataTable rows={projects} columns={['name', 'state', 'updated_at']} />
    <div className="backend-actions">{projects.map((project) => <button key={text(project.project_id)} onClick={() => run({ type: 'workspace.open', workspaceId: text(project.project_id) })}>打开 {text(project.name)}</button>)}</div>
    <TechnicalDetails value={projects} />
  </section>;

  if (route === 'webAssist') return <section className="backend-workspace surface"><h1>{title}</h1><p>导出审校包 → 网页 AI 人工处理 → 导回 → 差异预览 → 确认应用。</p><p className="operation-status">{operation}</p>
    <div className="backend-tabs"><button aria-pressed={webTab === 'glossary'} onClick={() => setWebTab('glossary')}>术语表辅助</button><button aria-pressed={webTab === 'difficult'} onClick={() => setWebTab('difficult')}>疑难页辅助</button><button aria-pressed={webTab === 'history'} onClick={() => setWebTab('history')}>回流记录</button></div>
    {webTab !== 'history' && <><div className="backend-actions"><button disabled={!activeContext.active_source_id} onClick={async () => { const result = await run({ type: 'webAssist.create', packageType: webTab === 'glossary' ? 'glossary_review' : 'difficult_pages' }); const created = result?.package as Row | undefined; if (created?.package_id) setSelectedPackageId(text(created.package_id, '')); }}>导出{webTab === 'glossary' ? '术语表' : '疑难页'}包</button><button disabled={!activePackageId} onClick={() => run({ type: 'webAssist.get', packageId: activePackageId })}>读取包详情</button><button disabled={!activePackageId} onClick={() => copyOfficialPrompt(activePackageId)}>复制官方提示词</button><button disabled={!activePackageId || !inTauri} onClick={() => openWebAssistFolder(activePackageId)}>打开包文件夹</button></div>
      <label>审校包 <select value={activePackageId} onChange={(e) => setSelectedPackageId(e.target.value)}><option value="">请选择</option>{filteredPackages.map((item) => <option key={text(item.package_id)} value={text(item.package_id)}>{text(item.package_id)} · {text(item.status)}</option>)}</select></label>
      <div className="backend-form"><input aria-label="审校结果路径" placeholder="选择网页 AI 返回文件或审校包目录" value={importPath} onChange={(e) => setImportPath(e.target.value)} /><button disabled={!inTauri} onClick={() => pickWebAssistImport('web_assist_file')}>选择结果文件</button><button disabled={!inTauri} onClick={() => pickWebAssistImport('web_assist_folder')}>选择审校包目录</button><button disabled={!activePackageId || !importPath} onClick={() => run({ type: 'webAssist.validate', packageId: activePackageId, importPath })}>校验导入</button><button disabled={!activePackageId} onClick={() => run({ type: 'webAssist.preview', packageId: activePackageId })}>预览差异</button><button disabled={!activePackageId} onClick={() => run({ type: 'webAssist.apply', packageId: activePackageId })}>确认应用</button><button disabled={!activePackageId} onClick={() => run({ type: 'webAssist.discard', packageId: activePackageId })}>丢弃</button><button disabled={!activeContext.active_source_id} onClick={() => run({ type: 'webAssist.undo' })}>撤销最近应用</button></div>
      {webTab === 'difficult' && <ImageViewer assetId={difficultImage} />}
      {detail && <pre className="backend-json">{JSON.stringify(detail, null, 2)}</pre>}</>}
    {webTab === 'history' && <><DataTable rows={history} columns={['applied_at', 'applied_items', 'undone']} /><TechnicalDetails value={history} /></>}
  </section>;

  if (route === 'modelServices') return <section className="backend-workspace surface"><h1>{title}</h1><p>只配置两个模型角色。API Key 安全保存到 Windows 凭据管理器，不进入项目文件、浏览器存储、数据库、日志或报告。</p><p className="operation-status">{operation}</p>
    <div className="model-role-grid">{MODEL_ROLES.map(({ role, label, description }) => { const provider = providers.find((item) => item.role === role) ?? {}; const draft = modelDrafts[role]; const visible = credentialVisible[role]; return <article className="model-role-card" data-model-role={role} key={role}><h2>{label}</h2><p>{description}</p>
      <label>Base URL<input aria-label={`${label} Base URL`} value={draft.baseUrl} onChange={(event) => setModelDrafts((current) => ({ ...current, [role]: { ...current[role], baseUrl: event.target.value } }))} placeholder="API 基础地址" /></label>
      <label>API Key<div className="credential-input"><input aria-label={`${label} API Key`} autoComplete="new-password" type={visible ? 'text' : 'password'} value={draft.apiKey} onChange={(event) => setModelDrafts((current) => ({ ...current, [role]: { ...current[role], apiKey: event.target.value } }))} placeholder="你的授权密钥" disabled={!inTauri} /><button type="button" onClick={() => setCredentialVisible((current) => ({ ...current, [role]: !visible }))}>{visible ? '隐藏' : '显示'}</button></div></label>
      <label>Model<input aria-label={`${label} Model`} value={draft.model} onChange={(event) => setModelDrafts((current) => ({ ...current, [role]: { ...current[role], model: event.target.value } }))} placeholder="使用的模型名称" /></label>
      <div className="backend-actions"><button disabled={!draft.baseUrl || !draft.model || (!draft.apiKey && !provider.credential_present)} onClick={() => void saveModelRole(role)}>保存</button><button disabled={!inTauri} onClick={() => void credentialAction(role, 'delete')}>移除</button><button disabled={!provider.valid || !provider.credential_present} onClick={() => run({ type: 'provider.test', role })}>测试连接</button></div>
      <small>配置状态：{displayValue(provider.connection_status ?? (provider.configured ? 'ready' : '未配置'), 'status')}；凭据：{provider.credential_present ? '已安全配置' : '未配置'}</small><small>{credentialMessages[role] || (inTauri ? 'API Key 默认隐藏且不会回显。' : '请在 Bookflow 桌面应用中配置。')}</small>
    </article>; })}</div></section>;

  if (route === 'logs') return <section className="backend-workspace surface"><h1>{title}</h1><p>事件日志来自常驻后端事件流，敏感字段由后端统一脱敏。</p><button onClick={() => run({ type: 'logs.reveal' })}>打开日志文件</button><DataTable rows={events} columns={['sequence', 'timestamp', 'event_type', 'payload']} /><TechnicalDetails value={events} /></section>;
  if (route === 'historyTasks') return <section className="backend-workspace surface"><h1>{title}</h1><DataTable rows={batches} columns={['state', 'created_at', 'updated_at']} /><DataTable rows={jobs} columns={['filename', 'state', 'stage', 'progress', 'attempts']} /><TechnicalDetails value={{ batches, jobs }} /></section>;
  if (route === 'outputFiles') return <section className="backend-workspace surface"><h1>{title}</h1><p className="operation-status">{operation}</p>
    <button disabled={!outputs.length} onClick={() => run({ type: 'outputs.openFolder' })}>打开输出目录</button>
    <DataTable rows={outputs} columns={['display_name', 'format', 'role', 'build_id', 'version', 'size', 'generated_at', 'status']} />
    <div className="artifact-actions">{outputs.map((output) => <div key={text(output.artifact_id)}><strong>{text(output.display_name)}</strong>
      <button onClick={() => run({ type: 'output.open', outputId: text(output.artifact_id) })}>打开</button>
      <button onClick={() => run({ type: 'output.reveal', outputId: text(output.artifact_id) })}>在文件夹中显示</button>
      <button onClick={async () => { const result = await run({ type: 'output.copyPath', outputId: text(output.artifact_id) }); if (result?.target) await navigator.clipboard.writeText(text(result.target)); }}>复制路径</button>
    </div>)}</div><TechnicalDetails value={(activeJob?.pipeline_details as Row | undefined)?.artifact_manifest} /></section>;

  if (route === 'settings') return <section className="backend-workspace surface"><h1>{title}</h1><p>显示偏好由前端本地保存；主题、语言、人物和动画可在顶部栏直接调整。</p><dl className="settings-summary"><div><dt>界面语言</dt><dd>{preferences.uiLocale}</dd></div><div><dt>主题</dt><dd>{preferences.theme}</dd></div><div><dt>正文字号</dt><dd>{preferences.previewFontScale}%</dd></div><div><dt>页面缩放</dt><dd>{preferences.previewZoom}%</dd></div><div><dt>桌宠缩放</dt><dd>{preferences.mascotScale}%</dd></div></dl><TechnicalDetails value={snapshot.backendCapabilities} /></section>;

  if (route === 'sourceDocument') return <section className="backend-workspace surface"><h1>{title}</h1><p className="operation-status">{operation}</p>
    <div className="backend-actions">{sources.map((source) => <button aria-pressed={source.source_id === activeContext.active_source_id} key={text(source.source_id)} onClick={() => run({ type: 'sources.select', sourceId: text(source.source_id) })}>查看 {text(source.filename)}</button>)}</div>
    <SourceGallery source={activeSource} /><DataTable rows={sources} columns={['filename', 'page_count', 'source_language', 'status']} /><TechnicalDetails value={activeSource} />
  </section>;

  if (route === 'comparison') return <section className="backend-workspace surface"><h1>{title}</h1><DocumentTextPreview job={activeJob} preferences={preferences} /></section>;

  if (['ocr', 'structure', 'translationWorkflow'].includes(route)) return <section className="backend-workspace surface"><h1>{title}</h1><p className="operation-status">{operation}</p>
    <DataTable rows={activeJob ? [activeJob] : []} columns={['filename', 'state', 'stage', 'progress', 'attempts']} />
    <PipelineArtifacts job={activeJob} route={route} />
    {route === 'ocr' && <SourceGallery source={activeSource} />}
    <TechnicalDetails value={activeJob} />
  </section>;

  return <section className="backend-workspace surface"><h1>{title}</h1><p className="operation-status">{operation}</p>
    <DataTable rows={jobs} columns={['filename', 'state', 'stage', 'progress', 'attempts']} />
    <TechnicalDetails value={activeJob} />
  </section>;
}
