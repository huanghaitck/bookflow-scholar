import { X } from 'lucide-react';
import { useState } from 'react';
import type { Dispatch, ReactNode, SetStateAction } from 'react';
import type { FrontendPreferences } from '../domain/bookflow-contract';
import { translate } from '../i18n/messages';

export function GlobalHelpLayer({
  preferences,
  setPreferences,
}: {
  preferences: FrontendPreferences;
  setPreferences: Dispatch<SetStateAction<FrontendPreferences>>;
}) {
  const [query, setQuery] = useState('');
  if (!preferences.helpOpen) return null;
  const isChinese = preferences.uiLocale === 'zh-Hans';
  const sections: Array<{ title: string; keywords: string; content: ReactNode }> = isChinese ? [
    {
      title: '1. 首次使用：先确认模型服务',
      keywords: '模型 服务 provider key 连接 测试',
      content: <ol>
        <li>打开左侧“模型服务”，分别配置文字翻译模型和视觉版面模型。</li>
        <li>保存凭据并点击连接测试；顶部应显示“T · 已就绪、V · 已就绪”。</li>
        <li>连接未通过时不要开始正式任务；先核对接口地址、模型名称、额度和网络。</li>
      </ol>,
    },
    {
      title: '2. 创建项目并导入 PDF',
      keywords: '创建 项目 导入 pdf 单个 多个 文件夹 自动',
      content: <ol>
        <li>推荐方式：进入“项目”，点击创建项目并填写书名或课题名，然后打开该项目。</li>
        <li>也可在“概览”直接点“单个文件、多个文件或文件夹”；没有当前项目时，首次导入会按文件名自动创建项目。</li>
        <li>导入前先在概览顶部选好源语言和目标语言。源语言不确定时可选“自动检测”。</li>
        <li>导入成功后，概览应出现文件名、当前项目、页数和待处理任务。一本书可作为一个项目，也可按需要把相关材料放入同一项目。</li>
      </ol>,
    },
    {
      title: '3. 开始处理与任务控制',
      keywords: '开始 暂停 继续 取消 重试 恢复 没反应',
      content: <ol>
        <li>确认当前项目、来源文件、语言方向和模型状态后，点击“开始”。</li>
        <li>处理阶段依次包括解析、版面识别、结构切分、翻译、重建和质量复核。</li>
        <li>可使用“暂停/继续”；确定不再需要时才使用“取消”。失败任务修正原因后使用“重试”，异常退出后使用“恢复”。</li>
        <li>“开始”不可点通常表示当前项目、来源、批次或任务尚未就绪；重新打开项目并选中来源即可。</li>
      </ol>,
    },
    {
      title: '4. 查看最终成品 PDF',
      keywords: '概览 pdf 原文 译文 双语 页码 预览',
      content: <ol>
        <li>任务完成后，概览中央直接显示最终成品 PDF，不再把整篇 Markdown 拉成一张长页。</li>
        <li>用上方“原文、译文、双语”切换对应的最终 PDF。</li>
        <li>阅读器底部可用“上一页 / 下一页”翻页，也可在“当前页 / 总页数”中直接输入页码跳转。</li>
        <li>右侧“成品 PDF 页码”始终只显示包含当前页的一组 10 页；跳到数百或上千页时，页码窗口会自动跟随，不会挤满侧栏。</li>
        <li>原书页码会以“【x】”保留在重建正文中。页眉、页脚、脚注和尾注分别翻译并回到相应位置。</li>
      </ol>,
    },
    {
      title: '5. 复核结构、图片和链接',
      keywords: '复核 ocr 结构 图片 地图 照片 脚注 尾注 链接',
      content: <ol>
        <li>在“原文、文字识别、结构、翻译工作流、对照”中检查识别文本、章节、翻译单元和对应关系。</li>
        <li>生产成品只应保留与正文相关的裁切地图、照片、图表等视觉对象，不应把整张原 PDF 页面当图片铺回成品。</li>
        <li>脚注、尾注和文内引用应保持可追踪关系；发现错位时记录页码和对象，再进入网页辅助复核或人工修订流程。</li>
      </ol>,
    },
    {
      title: '6. 导出和查找成品',
      keywords: '导出 输出 文件夹 md docx pdf 日志',
      content: <ol>
        <li>进入“输出文件”查看原文、译文和双语版本；可打开文件、在资源管理器中定位或复制路径。</li>
        <li>概览的“导出”用于导出当前任务成品；“输出”快捷按钮打开当前 ActiveContext 对应的输出目录。</li>
        <li>遇到问题时打开“日志”，记录项目名、文件名、阶段、页码和错误提示，避免只描述“没有反应”。</li>
      </ol>,
    },
    {
      title: '7. 切换项目与窗口',
      keywords: '切换 项目 书籍 窗口 最大化 最小化 紧凑',
      content: <ol>
        <li>在“项目”页面打开目标项目，再选择该项目的来源；概览和输出会随 ActiveContext 切换。</li>
        <li>右上角依次是最小化、紧凑模式、最大化和关闭。紧凑模式只保留桌面伴侣和状态外观。</li>
        <li>紧凑模式下最大化会暂时禁用；先退出紧凑模式，再调整普通窗口或最大化，避免窗口状态混淆。</li>
      </ol>,
    },
    {
      title: '8. 常见问题',
      keywords: '问题 空白 卡住 预览 导入 失败 provider 图片',
      content: <ul>
        <li><strong>导入后概览没有书：</strong>确认已打开正确项目和来源；首次导入会自动建项目，但已有项目时会导入当前项目。</li>
        <li><strong>点开始没有反应：</strong>检查模型已就绪、ActiveContext 完整，并查看按钮提示和日志。</li>
        <li><strong>进度长期不动：</strong>先查看当前阶段和日志；不要连续重复点击。可暂停后继续，失败后按错误原因重试。</li>
        <li><strong>PDF 预览空白：</strong>确认任务已完成且存在对应的原文/译文/双语 PDF；也可从“输出文件”直接打开成品。</li>
        <li><strong>成品图片异常：</strong>记录成品页码、原 PDF 页码和图片类型，检查是否为裁切视觉对象，避免把历史旧成品误认为最新构建。</li>
      </ul>,
    },
  ] : [
    {
      title: '1. Configure model services',
      keywords: 'model provider connection key',
      content: <p>Open Model Services, configure and test both the text and visual providers, and continue only when T and V are ready.</p>,
    },
    {
      title: '2. Create a project and import a PDF',
      keywords: 'project import pdf',
      content: <p>Create and open a named project, then import one or more PDFs. A first import from Overview can create a project automatically. Select source and target languages before importing.</p>,
    },
    {
      title: '3. Run and monitor',
      keywords: 'start pause resume retry recover',
      content: <p>Start the active job, monitor each stage, and use Pause, Resume, Retry, or Recover only when their buttons are enabled.</p>,
    },
    {
      title: '4. Preview and export',
      keywords: 'preview pdf pages export',
      content: <p>After completion, switch among source, target, and bilingual final PDFs in Overview. Use Previous/Next or type a page number directly; the right rail follows with at most ten nearby pages. Export from Outputs.</p>,
    },
    {
      title: '5. Troubleshooting',
      keywords: 'troubleshooting blank disabled',
      content: <p>If Import or Start appears inactive, verify the active project, source, batch, job, provider status, and Logs. Exit Compact mode before maximizing the window.</p>,
    },
  ];
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleSections = normalizedQuery
    ? sections.filter((section) => `${section.title} ${section.keywords}`.toLocaleLowerCase().includes(normalizedQuery))
    : sections;
  return (
    <aside
      className="global-help-layer"
      data-layer-index="8"
      aria-label={translate(preferences.uiLocale, 'help')}
    >
      <div className="help-header">
        <span>{translate(preferences.uiLocale, 'helpTitle')}</span>
        <button
          type="button"
          aria-label={translate(preferences.uiLocale, 'closeHelp')}
          onClick={() =>
            setPreferences((current) => ({ ...current, helpOpen: false }))
          }
        >
          <X size={16} />
        </button>
      </div>
      <h2>{isChinese ? 'Bookflow 客户端使用手册' : 'Bookflow Client Guide'}</h2>
      <input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        aria-label={translate(preferences.uiLocale, 'searchHelp')}
        placeholder={`${translate(preferences.uiLocale, 'searchHelp')}…`}
      />
      <div className="help-manual">
        {visibleSections.map((section) => (
          <section key={section.title}>
            <h3>{section.title}</h3>
            {section.content}
          </section>
        ))}
        {visibleSections.length === 0 && <p>{isChinese ? '没有匹配的帮助条目。' : 'No matching help topic.'}</p>}
      </div>
      <p>{translate(preferences.uiLocale, 'noBackendInference')}</p>
    </aside>
  );
}
