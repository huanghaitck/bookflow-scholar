import type { UiLocale } from '../domain/bookflow-contract';

const STATUS_MESSAGES: Record<
  string,
  Record<UiLocale, string>
> = {
  WORKFLOW_READY: {
    'zh-Hans': '工作流已就绪',
    en: 'Workflow is ready',
    fr: 'Le flux est prêt',
    de: 'Der Workflow ist bereit',
    ja: 'ワークフローの準備ができました',
    es: 'El flujo está listo',
  },
  PROVIDER_DEGRADED: {
    'zh-Hans': '模型服务性能下降',
    en: 'Provider is degraded',
    fr: 'Le fournisseur est dégradé',
    de: 'Der Anbieter ist beeinträchtigt',
    ja: 'プロバイダーの性能が低下しています',
    es: 'El proveedor presenta degradación',
  },
  REVIEW_REQUIRED: {
    'zh-Hans': '存在待人工复核项目',
    en: 'Human review is required',
    fr: 'Une révision humaine est requise',
    de: 'Eine menschliche Prüfung ist erforderlich',
    ja: '人による確認が必要です',
    es: 'Se requiere revisión humana',
  },
  BACKEND_REPORTED_WARNING: {
    'zh-Hans': '后端报告了一项需要处理的警告',
    en: 'The backend reported a warning that needs attention',
    fr: 'Le backend a signalé un avertissement à traiter',
    de: 'Das Backend hat eine zu prüfende Warnung gemeldet',
    ja: 'バックエンドから確認が必要な警告が報告されました',
    es: 'El backend informó de una advertencia que requiere atención',
  },
  BACKEND_REPORTED_ERROR: {
    'zh-Hans': '后端报告了一项错误，可按能力重试或恢复',
    en: 'The backend reported an error; retry or recover when available',
    fr: 'Le backend a signalé une erreur ; réessayez ou restaurez si disponible',
    de: 'Das Backend hat einen Fehler gemeldet; ggf. wiederholen oder wiederherstellen',
    ja: 'バックエンドでエラーが発生しました。可能なら再試行または復元してください',
    es: 'El backend informó de un error; reintente o recupere si está disponible',
  },
};

const DISPLAY_VALUES: Record<string, Record<UiLocale, string>> = {
  project_ready: { 'zh-Hans': '项目已就绪', en: 'Project ready', fr: 'Projet prêt', de: 'Projekt bereit', ja: 'プロジェクト準備完了', es: 'Proyecto listo' },
  empty: { 'zh-Hans': '等待打开项目', en: 'Waiting for a project', fr: 'En attente d’un projet', de: 'Warten auf ein Projekt', ja: 'プロジェクト待ち', es: 'Esperando un proyecto' },
  loading_project: { 'zh-Hans': '正在打开项目', en: 'Opening project', fr: 'Ouverture du projet', de: 'Projekt wird geöffnet', ja: 'プロジェクトを開いています', es: 'Abriendo proyecto' },
  importing: { 'zh-Hans': '正在导入文档', en: 'Importing documents', fr: 'Importation des documents', de: 'Dokumente werden importiert', ja: '文書を読み込み中', es: 'Importando documentos' },
  queued: { 'zh-Hans': '任务已进入队列', en: 'Task queued', fr: 'Tâche en attente', de: 'Aufgabe eingereiht', ja: 'タスク待機中', es: 'Tarea en cola' },
  batch_queued: { 'zh-Hans': '批次已进入队列', en: 'Batch queued', fr: 'Lot en attente', de: 'Stapel eingereiht', ja: 'バッチ待機中', es: 'Lote en cola' },
  ocr_running: { 'zh-Hans': '正在识别文字', en: 'Text recognition in progress', fr: 'Reconnaissance en cours', de: 'Texterkennung läuft', ja: '文字認識中', es: 'Reconocimiento en curso' },
  structure_rebuilding: { 'zh-Hans': '正在重建文档结构', en: 'Rebuilding document structure', fr: 'Reconstruction de la structure', de: 'Dokumentstruktur wird erstellt', ja: '文書構造を再構築中', es: 'Reconstruyendo la estructura' },
  translation_running: { 'zh-Hans': '翻译进行中', en: 'Translation in progress', fr: 'Traduction en cours', de: 'Übersetzung läuft', ja: '翻訳中', es: 'Traducción en curso' },
  exporting: { 'zh-Hans': '正在导出', en: 'Exporting', fr: 'Exportation', de: 'Export läuft', ja: '書き出し中', es: 'Exportando' },
  paused: { 'zh-Hans': '已暂停', en: 'Paused', fr: 'En pause', de: 'Pausiert', ja: '一時停止中', es: 'En pausa' },
  recovering: { 'zh-Hans': '正在恢复', en: 'Recovering', fr: 'Restauration', de: 'Wiederherstellung', ja: '復元中', es: 'Recuperando' },
  completed: { 'zh-Hans': '已完成', en: 'Completed', fr: 'Terminé', de: 'Abgeschlossen', ja: '完了', es: 'Completado' },
  cancelled: { 'zh-Hans': '已取消', en: 'Cancelled', fr: 'Annulé', de: 'Abgebrochen', ja: 'キャンセル済み', es: 'Cancelado' },
  warning: { 'zh-Hans': '需要注意', en: 'Needs attention', fr: 'Attention requise', de: 'Aufmerksamkeit nötig', ja: '要確認', es: 'Requiere atención' },
  failed: { 'zh-Hans': '处理失败', en: 'Failed', fr: 'Échec', de: 'Fehlgeschlagen', ja: '失敗', es: 'Fallido' },
  backend_disconnected: { 'zh-Hans': '后端连接已断开', en: 'Backend disconnected', fr: 'Backend déconnecté', de: 'Backend getrennt', ja: 'バックエンド切断', es: 'Backend desconectado' },
  command_pending: { 'zh-Hans': '正在提交操作', en: 'Submitting operation', fr: 'Envoi de l’opération', de: 'Vorgang wird gesendet', ja: '操作を送信中', es: 'Enviando operación' },
  connected: { 'zh-Hans': '已连接', en: 'Connected', fr: 'Connecté', de: 'Verbunden', ja: '接続済み', es: 'Conectado' },
  disconnected: { 'zh-Hans': '连接已断开', en: 'Disconnected', fr: 'Déconnecté', de: 'Getrennt', ja: '切断', es: 'Desconectado' },
  connecting: { 'zh-Hans': '正在连接', en: 'Connecting', fr: 'Connexion', de: 'Verbindung wird hergestellt', ja: '接続中', es: 'Conectando' },
  ready: { 'zh-Hans': '已就绪', en: 'Ready', fr: 'Prêt', de: 'Bereit', ja: '準備完了', es: 'Listo' },
  degraded: { 'zh-Hans': '性能下降', en: 'Degraded', fr: 'Dégradé', de: 'Beeinträchtigt', ja: '性能低下', es: 'Degradado' },
  offline: { 'zh-Hans': '离线', en: 'Offline', fr: 'Hors ligne', de: 'Offline', ja: 'オフライン', es: 'Sin conexión' },
  unavailable: { 'zh-Hans': '不可用', en: 'Unavailable', fr: 'Indisponible', de: 'Nicht verfügbar', ja: '利用不可', es: 'No disponible' },
  error: { 'zh-Hans': '发生错误', en: 'Error', fr: 'Erreur', de: 'Fehler', ja: 'エラー', es: 'Error' },
  unknown: { 'zh-Hans': '状态未知', en: 'Unknown', fr: 'Inconnu', de: 'Unbekannt', ja: '不明', es: 'Desconocido' },
  idle: { 'zh-Hans': '已就绪', en: 'Ready', fr: 'Prête', de: 'Bereit', ja: '準備完了', es: 'Lista' },
  thinking: { 'zh-Hans': '思考中', en: 'Thinking', fr: 'Réflexion', de: 'Denkt nach', ja: '考え中', es: 'Pensando' },
  working: { 'zh-Hans': '工作中', en: 'Working', fr: 'Travail en cours', de: 'Arbeitet', ja: '作業中', es: 'Trabajando' },
  reviewing: { 'zh-Hans': '复核中', en: 'Reviewing', fr: 'Révision', de: 'Prüft', ja: '確認中', es: 'Revisando' },
  sleeping: { 'zh-Hans': '休息中', en: 'Resting', fr: 'Repos', de: 'Ruht', ja: '休憩中', es: 'Descansando' },
  partial_success: { 'zh-Hans': '部分完成', en: 'Partially completed', fr: 'Partiellement terminé', de: 'Teilweise abgeschlossen', ja: '一部完了', es: 'Parcialmente completado' },
};

export function localizeDisplayValue(value: string, locale: UiLocale): string {
  return DISPLAY_VALUES[value]?.[locale] ?? DISPLAY_VALUES.unknown[locale];
}

export function localizeStatusCode(
  code: string | null,
  locale: UiLocale,
): string | null {
  if (code === null) return null;
  return STATUS_MESSAGES[code]?.[locale] ?? DISPLAY_VALUES.unknown[locale];
}
