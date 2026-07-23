import {
  BookCopy,
  BookOpen,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleGauge,
  Cpu,
  FileOutput,
  FileUp,
  Files,
  FolderOpen,
  FolderUp,
  Languages,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ScanText,
  ScrollText,
  Sparkles,
  Square,
  UploadCloud,
} from 'lucide-react';
import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow';
import { useEffect, useRef, useState } from 'react';
import type { Dispatch, DragEvent, SetStateAction } from 'react';
import { deriveCommandAvailability } from '../adapters/deriveCommandAvailability';
import type {
  BookflowSnapshot,
  FrontendPreferences,
  SourceLanguage,
  TargetLanguage,
} from '../domain/bookflow-contract';
import {
  SOURCE_LANGUAGES,
  TARGET_LANGUAGES,
} from '../domain/language-capabilities';
import { translate } from '../i18n/messages';
import { localizeDisplayValue } from '../i18n/status-codes';
import { mascotAssetUrl } from '../mascot/mascot-assets';
import { bookflowBridge } from '../state/useBookflowSnapshot';

function activeBackendRecord(
  snapshot: BookflowSnapshot,
  collection: 'projects' | 'sources' | 'jobs',
  contextKey: 'active_project_id' | 'active_source_id' | 'active_job_id',
  recordKey: 'project_id' | 'source_id' | 'job_id',
): Record<string, unknown> | undefined {
  const activeId = snapshot.backendState?.activeContext?.[contextKey];
  if (typeof activeId !== 'string' || !activeId) return undefined;
  return (snapshot.backendState?.[collection] ?? [])
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .find((item) => item[recordKey] === activeId);
}

const LANGUAGE_LABELS: Record<SourceLanguage, string> = {
  'auto-detect': 'Auto',
  'zh-Hans': '简体中文',
  en: 'English',
  fr: 'Français',
  de: 'Deutsch',
  ja: '日本語',
  es: 'Español',
};

function LanguageAndPreviewToolbar({
  snapshot,
  preferences,
  setPreferences,
}: {
  snapshot: BookflowSnapshot;
  preferences: FrontendPreferences;
  setPreferences: Dispatch<SetStateAction<FrontendPreferences>>;
}) {
  const selectLanguage = (source: SourceLanguage, target: TargetLanguage) => {
    void bookflowBridge.command({ type: 'language.select', source, target });
  };
  const t = (key: Parameters<typeof translate>[1]) =>
    translate(preferences.uiLocale, key);
  const commands = deriveCommandAvailability(snapshot);
  const fontScaleLabel = preferences.uiLocale === 'zh-Hans'
    ? '调整正文字号'
    : t('previewFontScale');
  const zoomLabel = preferences.uiLocale === 'zh-Hans'
    ? '调整页面缩放'
    : t('previewZoom');

  return (
    <section className="workbench-toolbar surface" data-testid="workbench-toolbar">
      <div className="language-control">
        <label>
          <span>{t('sourceLanguage')}</span>
          <select
            value={snapshot.sourceLanguageSelected}
            onChange={(event) =>
              selectLanguage(
                event.target.value as SourceLanguage,
                snapshot.targetLanguageSelected,
              )
            }
          >
            {SOURCE_LANGUAGES.map((language) => (
              <option key={language} value={language}>
                {language === 'auto-detect' ? t('autoDetect') : LANGUAGE_LABELS[language]}
              </option>
            ))}
          </select>
        </label>
        <Languages size={18} />
        <label>
          <span>{t('targetLanguage')}</span>
          <select
            value={snapshot.targetLanguageSelected}
            onChange={(event) =>
              selectLanguage(
                snapshot.sourceLanguageSelected,
                event.target.value as TargetLanguage,
              )
            }
          >
            {TARGET_LANGUAGES.map((language) => (
              <option key={language} value={language}>{LANGUAGE_LABELS[language]}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="preview-mode-tabs" role="tablist" aria-label={t('previewMode')}>
        {(['source', 'target', 'bilingual'] as const).map((mode) => (
          <button
            type="button"
            role="tab"
            key={mode}
            aria-selected={preferences.previewLayout === mode}
            onClick={() =>
              setPreferences((current) => ({ ...current, previewLayout: mode }))
            }
          >
            {t(
              mode === 'source'
                ? 'sourcePreview'
                : mode === 'target'
                  ? 'targetPreview'
                  : 'bilingualPreview',
            )}
          </button>
        ))}
      </div>
      <div className="preview-sliders">
        <label title={fontScaleLabel}>
          A
          <input
            aria-label={fontScaleLabel}
            data-testid="preview-font-scale"
            type="range"
            min="80"
            max="160"
            value={preferences.previewFontScale}
            onChange={(event) =>
              setPreferences((current) => ({
                ...current,
                previewFontScale: Number(event.target.value),
              }))
            }
          />
          A+
        </label>
        <label title={zoomLabel}>
          −
          <input
            aria-label={zoomLabel}
            data-testid="preview-zoom"
            type="range"
            min="75"
            max="150"
            step="5"
            value={preferences.previewZoom}
            onChange={(event) =>
              setPreferences((current) => ({
                ...current,
                previewZoom: Number(event.target.value),
              }))
            }
          />
          +
        </label>
        <button
          type="button"
          className="export-button"
          disabled={!commands.export}
          onClick={() => void bookflowBridge.command({ type: 'outputs.export' })}
        >
          <FileOutput size={15} /> {t('export')}
        </button>
      </div>
    </section>
  );
}

function ProjectPanel({
  snapshot,
  locale,
}: {
  snapshot: BookflowSnapshot;
  locale: FrontendPreferences['uiLocale'];
}) {
  const activeProject = activeBackendRecord(snapshot, 'projects', 'active_project_id', 'project_id');
  const activeSource = activeBackendRecord(snapshot, 'sources', 'active_source_id', 'source_id');
  const sourceName = String(activeSource?.filename ?? '尚未导入书籍');
  const bookTitle = sourceName.replace(/\.[^.]+$/, '');
  const coverPath = typeof activeSource?.cover === 'string' ? activeSource.cover : '';
  return (
    <section className="project-panel surface">
      <div className="panel-heading">
        <span>{translate(locale, 'currentProject')}</span>
        <BookOpen size={17} />
      </div>
      <div className="book-summary">
        <div className="book-cover" aria-hidden="true">
          {coverPath ? <img src={convertFileSrc(coverPath)} alt="" /> : <><Sparkles size={17} /><strong>{bookTitle}</strong><small>等待真实封面</small></>}
        </div>
        <div>
          <h2>{bookTitle}</h2>
          <span className="pair-badge">{snapshot.sourceLanguageDetected ?? snapshot.sourceLanguageSelected} → {snapshot.targetLanguageSelected}</span>
          <dl>
            <div><dt>项目</dt><dd>{String(activeProject?.name ?? '请先创建项目')}</dd></div>
            <div><dt>页数</dt><dd>{String(activeSource?.page_count ?? 0)}</dd></div>
            <div><dt>{translate(locale, 'currentStage')}</dt><dd>{localizeDisplayValue(snapshot.currentStage, locale)}</dd></div>
          </dl>
        </div>
      </div>
    </section>
  );
}

function ProgressPanel({
  snapshot,
  locale,
}: {
  snapshot: BookflowSnapshot;
  locale: FrontendPreferences['uiLocale'];
}) {
  const percentage = Math.round(
    (snapshot.completedUnits / Math.max(snapshot.totalUnits, 1)) * 100,
  );
  const stageOrder = ['workspace', 'text_quality', 'inspect', 'structure', 'plan', 'translation', 'render', 'validate', 'completed'];
  const activeIndex = Math.max(0, stageOrder.indexOf(snapshot.currentStage));
  const stages = [
    [translate(locale, 'documentParsing'), activeIndex > 2 ? 100 : percentage],
    ['结构分析', activeIndex > 3 ? 100 : activeIndex === 3 ? percentage : 0],
    [translate(locale, 'translationStage'), activeIndex > 5 ? 100 : activeIndex === 5 ? percentage : 0],
    [translate(locale, 'layoutRebuild'), activeIndex > 6 ? 100 : activeIndex === 6 ? percentage : 0],
    [translate(locale, 'qualityReview'), snapshot.workflowState === 'completed' ? 100 : activeIndex === 7 ? percentage : 0],
  ] as const;
  const commands = deriveCommandAvailability(snapshot);
  return (
    <section className="progress-panel surface">
      <div className="panel-heading">
        <span>{translate(locale, 'progress')}</span>
        <strong data-testid="progress-value">{percentage}%</strong>
      </div>
      <div className="stage-list">
        {stages.map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <i><b style={{ width: `${value}%` }} /></i>
            <em>{value}%</em>
          </div>
        ))}
      </div>
      <div className="workflow-controls">
        <button
          type="button"
          disabled={!commands.start}
          title={!commands.start ? '请先导入书籍并选择一个已排队任务' : undefined}
          onClick={() => void bookflowBridge.command({ type: 'workflow.start' })}
        >
          <Play size={14} /> {translate(locale, 'start')}
        </button>
        <button
          type="button"
          disabled={!commands.pause}
          onClick={() => void bookflowBridge.command({ type: 'workflow.pause' })}
        >
          <Pause size={14} /> {translate(locale, 'pause')}
        </button>
        <button
          type="button"
          disabled={!commands.resume}
          onClick={() => void bookflowBridge.command({ type: 'workflow.resume' })}
        >
          <Play size={14} /> {translate(locale, 'resume')}
        </button>
        <button
          type="button"
          disabled={!commands.cancel}
          onClick={() => void bookflowBridge.command({ type: 'workflow.cancel' })}
        >
          <Square size={12} /> {translate(locale, 'cancel')}
        </button>
        <button
          type="button"
          disabled={!commands.retry}
          onClick={() => void bookflowBridge.command({ type: 'workflow.retry' })}
        >
          <RefreshCw size={12} /> {translate(locale, 'retry')}
        </button>
        <span>{snapshot.completedUnits}/{snapshot.totalUnits}</span>
      </div>
    </section>
  );
}

function QuickActions({
  locale,
  snapshot,
}: {
  locale: FrontendPreferences['uiLocale'];
  snapshot: BookflowSnapshot;
}) {
  const singleInput = useRef<HTMLInputElement>(null);
  const multipleInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<{
    selected: number; discovered: number; succeeded: number; duplicates: number; rejected: number; failed: number; reasons: string[];
  } | null>(null);
  const importPaths = async (paths: string[]) => {
    if (paths.length === 0) return;
    try {
      const response = await bookflowBridge.command({ type: 'sources.import', paths });
    const payload = response.data.result && typeof response.data.result === 'object'
      ? response.data.result as Record<string, unknown> : {};
    const items = Array.isArray(payload.results) ? payload.results.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object') : [];
    const codeCount = (code: string) => items.filter((item) => item.result_code === code).length;
    const reasons = items.filter((item) => !['accepted', 'reused_immutable_source', 'duplicate_in_project'].includes(String(item.result_code ?? '')))
      .map((item) => String(item.reason ?? (item.error && typeof item.error === 'object' ? (item.error as Record<string, unknown>).message : item.result_code) ?? '导入失败'));
      setResult({
        selected: paths.length,
        discovered: Number(payload.discovered ?? items.length),
        succeeded: codeCount('accepted') + codeCount('reused_immutable_source'),
        duplicates: codeCount('duplicate_in_project'),
        rejected: codeCount('unsupported') + codeCount('unreadable'),
        failed: codeCount('failed') + (response.data.accepted ? 0 : 1),
        reasons: response.data.accepted ? reasons : [response.data.reasonCode ?? translate(locale, 'importRejected')],
      });
    } catch (error) {
      setResult({
        selected: paths.length,
        discovered: 0,
        succeeded: 0,
        duplicates: 0,
        rejected: 0,
        failed: paths.length,
        reasons: [error instanceof Error ? error.message : String(error)],
      });
    }
  };
  const filesFromInput = (files: FileList | null) => {
    const paths = Array.from(files ?? []).map((file) => (file as File & { path?: string }).path).filter((path): path is string => Boolean(path));
    if (paths.length) void importPaths(paths);
    else setResult({ selected: 0, discovered: 0, succeeded: 0, duplicates: 0, rejected: 0, failed: 1,
      reasons: ['浏览器开发预览无法读取本地绝对路径，请使用 Tauri 桌面入口。'] });
  };
  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    filesFromInput(event.dataTransfer.files);
  };
  const pick = async (mode: 'single' | 'multiple' | 'folder') => {
    if ('__TAURI_INTERNALS__' in window) {
      const paths = await invoke<string[]>('bookflow_pick_paths', { mode });
      await importPaths(paths);
      return;
    }
    ({ single: singleInput, multiple: multipleInput, folder: folderInput })[mode].current?.click();
  };

  useEffect(() => {
    if (!('__TAURI_INTERNALS__' in window)) return undefined;
    let dispose: (() => void) | undefined;
    void getCurrentWebviewWindow().onDragDropEvent((event) => {
      if (event.payload.type === 'drop') void importPaths(event.payload.paths);
    }).then((unlisten) => { dispose = unlisten; });
    return () => dispose?.();
  }, []);
  return (
    <section
      className="quick-actions surface"
      data-testid="import-sources-zone"
      onDragOver={(event) => event.preventDefault()}
      onDrop={onDrop}
    >
      <div className="panel-heading">
        <span>{translate(locale, 'quickActions')}</span>
        <Sparkles size={16} />
      </div>
      <input ref={singleInput} hidden type="file" accept=".pdf,application/pdf" onChange={(event) => filesFromInput(event.target.files)} />
      <input ref={multipleInput} hidden type="file" multiple accept=".pdf,application/pdf" onChange={(event) => filesFromInput(event.target.files)} />
      <input
        ref={(node) => {
          folderInput.current = node;
          node?.setAttribute('webkitdirectory', '');
          node?.setAttribute('directory', '');
        }}
        hidden
        type="file"
        multiple
        accept=".pdf,application/pdf"
        onChange={(event) => filesFromInput(event.target.files)}
      />
      <div className="import-action-grid">
        <button type="button" data-testid="import-single" onClick={() => void pick('single')}>
          <FileUp size={18} /><span>{translate(locale, 'singleFile')}</span>
        </button>
        <button type="button" data-testid="import-multiple" onClick={() => void pick('multiple')}>
          <Files size={18} /><span>{translate(locale, 'multipleFiles')}</span>
        </button>
        <button type="button" data-testid="import-folder" onClick={() => void pick('folder')}>
          <FolderUp size={18} /><span>{translate(locale, 'folderImport')}</span>
        </button>
        <div className="drop-hint"><UploadCloud size={17} /><span>{translate(locale, 'dropSources')}</span></div>
      </div>
      {!snapshot.backendState?.activeContext?.active_project_id && (
        <p className="import-project-hint">首次导入会自动创建项目，无需预先进入“项目”页面。</p>
      )}
      {result && <output className="import-result" data-testid="import-result"><span>选择 {result.selected}</span><span>发现 {result.discovered}</span><span>成功 {result.succeeded}</span><span>重复 {result.duplicates}</span><span>拒绝 {result.rejected}</span><span>失败 {result.failed}</span>{result.reasons.length > 0 && <ul>{result.reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul>}</output>}
    </section>
  );
}

function ReaderStage({
  preferences,
  snapshot,
  pdfPageDataUrl,
  currentPage,
  pageCount,
  onPageChange,
}: {
  preferences: FrontendPreferences;
  snapshot: BookflowSnapshot;
  pdfPageDataUrl: string;
  currentPage: number;
  pageCount: number;
  onPageChange: (page: number) => void;
}) {
  const [pageDraft, setPageDraft] = useState(String(currentPage));
  const hasSource = Boolean(activeBackendRecord(snapshot, 'sources', 'active_source_id', 'source_id'));
  const emptyText = hasSource ? '处理完成后在此显示最终成品 PDF。' : '导入书籍后在此显示最终成品 PDF。';
  useEffect(() => setPageDraft(String(currentPage)), [currentPage]);
  const commitPageDraft = () => {
    const parsed = Number.parseInt(pageDraft, 10);
    const page = Number.isFinite(parsed) ? Math.min(pageCount, Math.max(1, parsed)) : currentPage;
    setPageDraft(String(page));
    if (page !== currentPage) onPageChange(page);
  };
  return (
    <section
      className="reader-stage pdf-reader-stage surface"
      data-testid="reader-stage"
    >
      {pdfPageDataUrl ? (
        <div className="final-pdf-page">
          <img
          className="final-pdf-preview"
          data-testid="final-pdf-preview"
            src={pdfPageDataUrl}
            alt={`最终成品 PDF，第 ${currentPage} 页`}
            style={{ width: `${preferences.previewZoom}%` }}
          />
        </div>
      ) : <p className="pdf-preview-empty">{emptyText}</p>}
      {pageCount > 0 && (
        <nav className="reader-pagination" aria-label="成品 PDF 翻页">
          <button
            type="button"
            aria-label="上一页"
            disabled={currentPage <= 1}
            onClick={() => onPageChange(currentPage - 1)}
          >
            <ChevronLeft size={16} />
          </button>
          <label>
            <input
              aria-label="输入成品 PDF 页码"
              inputMode="numeric"
              value={pageDraft}
              onChange={(event) => setPageDraft(event.target.value.replace(/\D/g, ''))}
              onBlur={commitPageDraft}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  commitPageDraft();
                  event.currentTarget.blur();
                }
                if (event.key === 'Escape') {
                  setPageDraft(String(currentPage));
                  event.currentTarget.blur();
                }
              }}
            />
            <span>/ {pageCount}</span>
          </label>
          <button
            type="button"
            aria-label="下一页"
            disabled={currentPage >= pageCount}
            onClick={() => onPageChange(currentPage + 1)}
          >
            <ChevronRight size={16} />
          </button>
        </nav>
      )}
    </section>
  );
}

function PageRail({
  locale,
  snapshot,
  pageCount,
  currentPage,
  onPageChange,
}: {
  locale: FrontendPreferences['uiLocale'];
  snapshot: BookflowSnapshot;
  pageCount: number;
  currentPage: number;
  onPageChange: (page: number) => void;
}) {
  const commands = deriveCommandAvailability(snapshot);
  const pageWindowSize = Math.min(10, pageCount);
  const pageWindowStart = Math.min(
    Math.floor(Math.max(0, currentPage - 1) / 10) * 10 + 1,
    Math.max(1, pageCount - pageWindowSize + 1),
  );
  const visiblePages = Array.from(
    { length: pageWindowSize },
    (_, index) => pageWindowStart + index,
  );
  return (
    <aside className="page-rail surface" aria-label={translate(locale, 'pageThumbnails')}>
      <section className="backend-inspector" data-testid="backend-inspector">
        <div className="panel-heading"><span>{translate(locale, 'currentStage')}</span><CircleGauge size={15} /></div>
        <strong>{localizeDisplayValue(snapshot.currentStage, locale)}</strong>
        <dl>
          <div><dt>{translate(locale, 'queue')}</dt><dd>{snapshot.queueSummary.queued} / {snapshot.queueSummary.running}</dd></div>
          <div><dt>{translate(locale, 'checkpoint')}</dt><dd>{snapshot.checkpoint ? localizeDisplayValue('ready', locale) : '—'}</dd></div>
          <div><dt>{translate(locale, 'warnings')}</dt><dd>{snapshot.warningCode ? 1 : 0}</dd></div>
          <div><dt>{translate(locale, 'errors')}</dt><dd>{snapshot.errorCode ? 1 : 0}</dd></div>
        </dl>
        {snapshot.commandPending && <em>{localizeDisplayValue('command_pending', locale)}</em>}
        <div className="inspector-actions">
          <button type="button" disabled={!commands.recover} onClick={() => void bookflowBridge.command({ type: 'workflow.recover' })}><RotateCcw size={12} /><span>{translate(locale, 'recover')}</span></button>
          <button type="button" disabled={!commands.openOutputFolder} title={!commands.openOutputFolder ? translate(locale, 'unsupported') : undefined} onClick={() => void bookflowBridge.command({ type: 'outputs.openFolder' })}><FolderOpen size={12} /><span>{translate(locale, 'outputs')}</span></button>
          <button type="button" disabled={!commands.revealLogFile} title={!commands.revealLogFile ? translate(locale, 'unsupported') : undefined} onClick={() => void bookflowBridge.command({ type: 'logs.reveal' })}><ScrollText size={12} /><span>{translate(locale, 'logs')}</span></button>
        </div>
      </section>
      <div className="panel-heading">
        <span>成品 PDF 页码</span>
        <BookCopy size={16} />
      </div>
      <div className="pdf-page-list" data-testid="pdf-page-list">
        {visiblePages.map((page) => (
          <button
            type="button"
            key={page}
            aria-current={page === currentPage ? 'page' : undefined}
            aria-label={`转到第 ${page} 页`}
            onClick={() => onPageChange(page)}
          >
          <span className="mini-spread">
              <i />
          </span>
            <small>{page}</small>
          </button>
        ))}
      </div>
      <strong>{pageCount} {translate(locale, 'pages')}</strong>
    </aside>
  );
}

function CompanionStrip({ locale }: { locale: FrontendPreferences['uiLocale'] }) {
  const companions = [
    ['mascot_editor', 'Eleanor'],
    ['mascot_scholar', 'Clara'],
    ['mascot_explorer', 'Stella'],
  ] as const;
  return (
    <div className="dock-panel companion-strip">
      <div className="panel-heading"><span>{translate(locale, 'desktopCompanions')}</span><CircleGauge size={15} /></div>
      <div>
        {companions.map(([character, label]) => (
          <figure key={character}>
            <img
              src={mascotAssetUrl(character, 'skin_default', 'chibi', 'idle')}
              alt={label}
            />
            <figcaption>{label}</figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}

function ActivityLog({ snapshot, locale }: { snapshot: BookflowSnapshot; locale: FrontendPreferences['uiLocale'] }) {
  return (
    <div className="dock-panel">
      <div className="panel-heading"><span>{translate(locale, 'recentActivity')}</span><RotateCcw size={15} /></div>
      <ol className="activity-log">
        <li><time>{translate(locale, 'currentStage')}</time> {localizeDisplayValue(snapshot.currentStage, locale)}</li>
        <li><time>{translate(locale, 'queueSummary')}</time> {snapshot.queueSummary.queued} / {snapshot.queueSummary.running}</li>
        <li><time>{locale === 'zh-Hans' ? '事件流' : 'Event stream'}</time> {localizeDisplayValue(snapshot.connectionState, locale)}</li>
      </ol>
    </div>
  );
}

function ServiceMonitor({ snapshot, locale }: { snapshot: BookflowSnapshot; locale: FrontendPreferences['uiLocale'] }) {
  return (
    <div className="dock-panel">
      <div className="panel-heading"><span>{translate(locale, 'servicesConfiguration')}</span><Cpu size={15} /></div>
      <div className="service-monitor">
        {(['text', 'vlm'] as const).map((kind) => (
          <div key={kind}>
            <CheckCircle2 size={14} />
            <span>{kind === 'text' ? translate(locale, 'textModelService') : translate(locale, 'visualModelService')}</span>
            <strong>{localizeDisplayValue(snapshot.providerStatus[kind].status, locale)}</strong>
          </div>
        ))}
        <div><ScanText size={14} /><span>{translate(locale, 'pdfRenderer')}</span><strong>{localizeDisplayValue(snapshot.rendererStatus.pdf, locale)}</strong></div>
      </div>
    </div>
  );
}

function BottomDock({ snapshot, locale }: { snapshot: BookflowSnapshot; locale: FrontendPreferences['uiLocale'] }) {
  return (
    <section className="bottom-dock surface">
      <CompanionStrip locale={locale} />
      <ActivityLog snapshot={snapshot} locale={locale} />
      <ServiceMonitor snapshot={snapshot} locale={locale} />
    </section>
  );
}

export function OverviewWorkspace({
  snapshot,
  preferences,
  setPreferences,
}: {
  snapshot: BookflowSnapshot;
  preferences: FrontendPreferences;
  setPreferences: Dispatch<SetStateAction<FrontendPreferences>>;
}) {
  const [pdfPageDataUrl, setPdfPageDataUrl] = useState('');
  const [pdfPageCount, setPdfPageCount] = useState(0);
  const [currentPdfPage, setCurrentPdfPage] = useState(1);
  const activePdfArtifactRef = useRef('');
  const activeJob = activeBackendRecord(snapshot, 'jobs', 'active_job_id', 'job_id');
  const details = activeJob?.pipeline_details as Record<string, unknown> | undefined;
  const artifactManifest = details?.artifact_manifest as Record<string, unknown> | undefined;
  const pdfRole = `${preferences.previewLayout}_pdf`;
  const pdfArtifact = artifactManifest?.[pdfRole] as Record<string, unknown> | undefined;
  const pdfArtifactId = typeof pdfArtifact?.artifact_id === 'string' ? pdfArtifact.artifact_id : '';

  useEffect(() => {
    let active = true;
    const artifactChanged = activePdfArtifactRef.current !== pdfArtifactId;
    activePdfArtifactRef.current = pdfArtifactId;
    const requestedPage = artifactChanged ? 1 : currentPdfPage;
    if (artifactChanged) {
      setCurrentPdfPage(1);
      setPdfPageCount(0);
    }
    setPdfPageDataUrl('');
    if (pdfArtifactId) {
      void bookflowBridge.command({ type: 'artifact.page', artifactId: pdfArtifactId, page: requestedPage })
        .then(({ data }) => {
          const result = data.result as Record<string, unknown> | undefined;
          if (!active || !data.accepted || typeof result?.data_url !== 'string') return;
          setPdfPageDataUrl(result.data_url);
          setPdfPageCount(Math.max(0, Number(result.page_count ?? 0)));
        });
    }
    return () => { active = false; };
  }, [pdfArtifactId, currentPdfPage]);

  return (
    <>
      <LanguageAndPreviewToolbar
        snapshot={snapshot}
        preferences={preferences}
        setPreferences={setPreferences}
      />
      <ProjectPanel snapshot={snapshot} locale={preferences.uiLocale} />
      <ProgressPanel snapshot={snapshot} locale={preferences.uiLocale} />
      <QuickActions locale={preferences.uiLocale} snapshot={snapshot} />
      <ReaderStage
        preferences={preferences}
        snapshot={snapshot}
        pdfPageDataUrl={pdfPageDataUrl}
        currentPage={currentPdfPage}
        pageCount={pdfPageCount}
        onPageChange={setCurrentPdfPage}
      />
      <PageRail
        locale={preferences.uiLocale}
        snapshot={snapshot}
        pageCount={pdfPageCount}
        currentPage={currentPdfPage}
        onPageChange={setCurrentPdfPage}
      />
      <BottomDock snapshot={snapshot} locale={preferences.uiLocale} />
    </>
  );
}
