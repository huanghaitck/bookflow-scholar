let token = '';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const option = new URLSearchParams(location.search).get('option') || document.body.dataset.option || 'A';
const names = {A: 'Bookflow Scholar Desktop', B: 'Bookflow Modern Console', C: 'Bookflow Editorial Workstation'};
document.body.className = `theme-${option.toLowerCase()}`;
document.body.dataset.option = option;
document.title = names[option];
document.querySelector('.brand').childNodes[0].textContent = names[option];
document.querySelector('.tag').textContent = `OPTION ${option}`;

async function api(path, data) {
  if (!token) token = (await fetch('/api/session').then(r => r.json())).token;
  const response = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Bookflow-Token': token},
    body: JSON.stringify(data || {})
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || result.error || 'Request failed');
  return result;
}

function formData() {
  return {
    workspace: $('#workspace').value.trim(), source_pdf: $('#sourcePdf').value.trim(),
    output_directory: $('#outputDirectory').value.trim(), source_language: $('#sourceLanguage').value,
    target_language: $('#targetLanguage').value, output_role: $('#outputRole').value,
    book_title: $('#bookTitle').value.trim(), author: $('#bookAuthor').value.trim(),
    layout_mode: $('#layoutMode').value, bilingual_layout: $('#bilingualLayout').value,
    provider_config: $('#providerConfig').value.trim(), renderer_config: $('#rendererConfig').value.trim() || null
  };
}

function show(view) {
  $$('.view').forEach(item => item.hidden = item.dataset.view !== view);
  $$('[data-view-target]').forEach(item => item.classList.toggle('active', item.dataset.viewTarget === view));
  history.replaceState(null, '', `?option=${option}&view=${view}`);
}

function report(result, label = 'Completed') {
  $('#activityTitle').textContent = label;
  $('#activityOutput').textContent = JSON.stringify(result, null, 2);
  $('#activity').classList.add('open');
}

async function run(path, extra = {}, label) {
  try { report(await api(path, {...formData(), ...extra}), label); }
  catch (error) { report({status: 'failed', message: error.message}, 'Action blocked'); }
}

async function refreshStatus() {
  const workspace = $('#workspace').value.trim();
  if (!workspace) return;
  try {
    const status = await fetch(`/api/status?workspace=${encodeURIComponent(workspace)}`).then(r => r.json());
    $('#stageValue').textContent = status.stage || 'not_generated';
    $('#callsValue').textContent = status.provider_calls ?? 0;
    $('#cacheValue').textContent = status.cached ?? 0;
    $('#pendingValue').textContent = status.pending ?? 0;
    report(status, 'Workspace status');
  } catch (error) { report({status: 'not_generated', message: error.message}, 'Workspace unavailable'); }
}

$$('[data-view-target]').forEach(button => button.addEventListener('click', () => show(button.dataset.viewTarget)));
$$('[data-api]').forEach(button => button.addEventListener('click', () => run(button.dataset.api, {}, button.textContent.trim())));
$('#createWorkspace').addEventListener('click', () => run('/api/workspace/create', {}, 'Workspace created'));
$('#statusRefresh').addEventListener('click', refreshStatus);
$('#saveProvider').addEventListener('click', () => {
  const id = $('#providerId').value.trim();
  const payload = {allow_real_api: $('#allowRealApi').checked, active_text_provider: id,
    active_vision_provider: $('#visionProviderId').value.trim() || id, providers: {}};
  payload.providers[id] = {provider_type: 'openai-compatible', model: $('#providerModel').value.trim(),
    base_url: $('#providerBaseUrl').value.trim(), api_key_alias: $('#credentialAlias').value.trim(),
    capabilities: ['text']};
  const visionId = $('#visionProviderId').value.trim();
  if (visionId) payload.providers[visionId] = {provider_type: 'openai-compatible',
    model: $('#visionModel').value.trim(), base_url: $('#visionBaseUrl').value.trim(),
    api_key_alias: $('#visionCredentialAlias').value.trim(), capabilities: ['vision', 'structure', 'ocr']};
  run('/api/providers/save', {config: $('#providerConfig').value.trim(), payload}, 'Provider profile saved');
});
$('#setCredential').addEventListener('click', async () => {
  const secret = $('#credentialSecret').value;
  await run('/api/credentials/set', {alias: $('#credentialAliasEdit').value.trim(), secret}, 'Credential stored');
  $('#credentialSecret').value = '';
});
$('#deleteCredential').addEventListener('click', () => run('/api/credentials/delete', {alias: $('#credentialAliasEdit').value.trim()}, 'Credential deleted'));
$('#testCredential').addEventListener('click', () => run('/api/credentials/test', {alias: $('#credentialAliasEdit').value.trim()}, 'Credential checked'));
$('#exportReview').addEventListener('click', () => run('/api/review/export', {output: $('#reviewExport').value.trim()}, 'Review package exported'));
$('#validatePatch').addEventListener('click', () => run('/api/review/validate', {patch: $('#reviewPatch').value.trim()}, 'Patch validated'));
$('#dryRunPatch').addEventListener('click', () => run('/api/review/import', {patch: $('#reviewPatch').value.trim(), dry_run: true}, 'Dry run complete'));
$('#importPatch').addEventListener('click', () => run('/api/review/import', {patch: $('#reviewPatch').value.trim(), dry_run: false}, 'Patch imported'));
$('#closeActivity').addEventListener('click', () => $('#activity').classList.remove('open'));

show(new URLSearchParams(location.search).get('view') || 'overview');
fetch('/api/health').then(r => r.json()).then(() => { $('#connection').textContent = 'Local service connected'; });
