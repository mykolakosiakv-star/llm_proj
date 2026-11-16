// ============================================================================
// ЗБЕРЕЖЕННЯ КЛЮЧІВ ТА ОПИТУВАННЯ СТАТУСІВ МОДЕЛЕЙ
// ============================================================================

async function saveSystemSettings() {
    const enabledModels = (APP_STATE.metadata?.models || []).filter(m => m.enabled).map(m => m.id);
    const probeStartupEl = document.getElementById('settingsProbeOnStartup');

    const payload = {
        groq_api_key: document.getElementById('settingsGroqKey').value.trim() || undefined,
        openai_api_key: document.getElementById('settingsOpenaiKey').value.trim() || undefined,
        gemini_api_key: document.getElementById('settingsGeminiKey').value.trim() || undefined,
        anthropic_api_key: document.getElementById('settingsAnthropicKey').value.trim() || undefined,
        openrouter_api_key: document.getElementById('settingsOpenrouterKey').value.trim() || undefined,
        ollama_base_url: document.getElementById('settingsOllamaUrl').value.trim() || undefined,
        ollama_model_name: document.getElementById('settingsOllamaModel').value.trim() || undefined,
        temperature: Number(document.getElementById('settingsTemperature').value),
        confidence_threshold: Number(document.getElementById('settingsConfThreshold').value),
        probe_on_startup: probeStartupEl ? Boolean(probeStartupEl.checked) : true,
        enabled_models: enabledModels,
    };

    await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    ['settingsGroqKey', 'settingsOpenaiKey', 'settingsGeminiKey', 'settingsAnthropicKey', 'settingsOpenrouterKey'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });

    // Сервер сам запускає перевірку після зміни ключів, тут лише чекаємо її завершення
    await waitForProbeToFinish();
    await refreshMetadataAndData();
    await pollLogsAndStatus();

    const notice = document.getElementById('settingsSavedNotice');
    if (notice) {
        notice.classList.remove('hidden');
        setTimeout(() => notice.classList.add('hidden'), 3000);
    }
}

async function syncModelsSnapshotFromServer(options = {}) {
    const snap = await fetch('/api/models/status').then(r => r.json());
    if (APP_STATE.metadata) {
        APP_STATE.metadata.probe = snap;
        APP_STATE.metadata.models = Object.values(snap.models || {});
        const snapProvs = snap.providers || {};
        (APP_STATE.metadata.providers || []).forEach(p => {
            const sp = snapProvs[p.id];
            if (sp) {
                p.status = sp.status;
                p.status_label = sp.status_label;
                p.checked_at = sp.checked_at;
                p.checked_ago_uk = sp.checked_ago_uk;
                p.models_count = sp.models_count;
                p.enabled_count = sp.enabled_count;
            }
        });
    }
    renderKeysQuickStatusPills();
    renderProvidersTilesGrid();
    renderActiveModelsSummary();
    // Форми вибору моделей перебудовуються лише після перевірки, інакше під час опитування збивалися б відмічені значення
    if (options.refreshControls !== false) populateControls();
    if (APP_STATE.activeModalProviderId) {
        renderProviderModalListContent(APP_STATE.activeModalProviderId);
    }
    return snap;
}

const PROBE_WAIT_LIMIT_MS = 5 * 60 * 1000;

function describeProbeProgress(probe) {
    if (!probe || !probe.running) return 'Перевірка...';
    if (probe.total) return `Перевірено ${probe.done} з ${probe.total}`;
    return probe.phase || 'Перевірка...';
}

// Кінець перевірки визначає сервер, а не кількість опитувань: перевірка може тривати довго
async function waitForProbeToFinish(onProgress) {
    const deadline = Date.now() + PROBE_WAIT_LIMIT_MS;
    while (Date.now() < deadline) {
        const snap = await syncModelsSnapshotFromServer({ refreshControls: false });
        if (onProgress) onProgress(snap.probe);
        if (!snap.is_probing) return snap;
        await new Promise(resolve => setTimeout(resolve, 800));
    }
    return null;
}

function setProbeBanner(text, isError = false) {
    const banner = document.getElementById('probeFeedbackBanner');
    if (!banner) return;
    banner.className = `p-3 rounded-lg text-xs border block ${isError ? 'border-rose-300 bg-rose-50 text-rose-800' : 'border-slate-300 bg-slate-50 text-slate-800'}`;
    banner.textContent = text;
}

function hideProbeBannerLater() {
    setTimeout(() => document.getElementById('probeFeedbackBanner')?.classList.add('hidden'), 5000);
}

async function probeApiLimits(options = {}) {
    const silent = Boolean(options.silent);
    const onlyEnabled = options.only_enabled !== undefined ? Boolean(options.only_enabled) : true;
    const idleLabel = 'Оновити статуси';
    const btn = document.getElementById('probeApiLimitsBtn');
    const setButton = text => {
        if (!btn) return;
        btn.textContent = text;
        btn.disabled = text !== idleLabel;
    };

    setButton('Перевірка...');
    if (!silent) setProbeBanner('Перевірка запущена. Статуси оновлюються в міру отримання відповідей.');
    APP_STATE.probingScope = options.model_key
        ? { type: 'model', id: options.model_key }
        : (options.provider ? { type: 'provider', id: options.provider } : { type: 'all' });
    if (APP_STATE.activeModalProviderId) renderProviderModalListContent(APP_STATE.activeModalProviderId);
    try {
        const resp = await fetch('/api/models/status/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_key: options.model_key || null, only_enabled: onlyEnabled, force: true, provider: options.provider || null }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const snap = await waitForProbeToFinish(probe => setButton(describeProbeProgress(probe)));
        await refreshMetadataAndData();
        if (!silent) {
            setProbeBanner(snap
                ? `Статуси оновлено. У реєстрі моделей: ${(APP_STATE.metadata?.models || []).length}.`
                : 'Перевірка триває довше за очікуване. Результати з\'являться в списку, коли вона завершиться.');
            hideProbeBannerLater();
        }
    } catch (e) {
        if (!silent) setProbeBanner(`Не вдалося запустити перевірку: ${e.message || e}`, true);
    } finally {
        APP_STATE.probingScope = null;
        setButton(idleLabel);
        if (APP_STATE.activeModalProviderId) renderProviderModalListContent(APP_STATE.activeModalProviderId);
    }
}

function probeProviderModels(providerId) {
    return probeApiLimits({ silent: false, provider: providerId, only_enabled: false });
}

async function probeSingleModelStatus(modelKey) {
    await probeApiLimits({ silent: false, model_key: modelKey, only_enabled: false });
}

function openCustomModelModal() {
    const modal = document.getElementById('customModelModal');
    const fb = document.getElementById('customModelTestFeedback');
    if (fb) fb.classList.add('hidden');
    if (modal) {
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    }
}

function closeCustomModelModal() {
    const modal = document.getElementById('customModelModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
}

function onCustomModelProviderChange() {
    const prov = document.getElementById('customModelProvider')?.value || 'custom';
    const baseInp = document.getElementById('customModelBaseUrl');
    const envInp = document.getElementById('customModelKeyEnv');
    const defaults = {
        custom: { url: 'http://localhost:8080/v1', env: 'CUSTOM_API_KEY' },
        openai: { url: 'https://api.openai.com/v1', env: 'OPENAI_API_KEY' },
        groq: { url: 'https://api.groq.com/openai/v1', env: 'GROQ_API_KEY' },
        openrouter: { url: 'https://openrouter.ai/api/v1', env: 'OPENROUTER_API_KEY' },
        gemini: { url: 'https://generativelanguage.googleapis.com/v1beta', env: 'GEMINI_API_KEY' },
        anthropic: { url: 'https://api.anthropic.com/v1', env: 'ANTHROPIC_API_KEY' },
        ollama: { url: 'http://localhost:11434/v1', env: '' },
    };
    const cfg = defaults[prov] || defaults.custom;
    if (baseInp) baseInp.value = cfg.url;
    if (envInp) envInp.value = cfg.env;
}

async function testCustomModelFromModal() {
    const base_url = document.getElementById('customModelBaseUrl')?.value.trim() || 'https://api.openai.com/v1';
    const model_id = document.getElementById('customModelId')?.value.trim() || '';
    const api_key_env = document.getElementById('customModelKeyEnv')?.value.trim() || '';
    const api_key = document.getElementById('customModelApiKey')?.value.trim() || '';
    const fb = document.getElementById('customModelTestFeedback');
    if (!fb) return;

    if (!model_id) {
        fb.className = 'p-2.5 rounded border text-xs bg-amber-50 border-amber-200 text-amber-900 block';
        fb.textContent = 'Вкажіть ID моделі перед перевіркою.';
        return;
    }

    fb.className = 'p-2.5 rounded border text-xs bg-blue-50 border-blue-200 text-blue-900 block';
    fb.textContent = "Перевірка з'єднання (max_tokens=1)...";

    try {
        const res = await fetch('/api/models/custom/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_url, model_id, api_key_env, api_key: api_key || undefined }),
        }).then(r => r.json());

        const ok = res.status === 'ok';
        fb.className = `p-2.5 rounded border text-xs block ${ok ? 'bg-emerald-50 border-emerald-200 text-emerald-900' : 'bg-rose-50 border-rose-200 text-rose-900'}`;
        fb.textContent = `Статус: ${res.status_label || res.status} | Затримка: ${res.latency_ms ?? '-'} мс | ${res.message || ''}`;
    } catch (e) {
        fb.className = 'p-2.5 rounded border text-xs bg-rose-50 border-rose-200 text-rose-900 block';
        fb.textContent = `Помилка: ${e.message}`;
    }
}

async function submitCustomModel() {
    const provider = document.getElementById('customModelProvider')?.value || 'custom';
    const base_url = document.getElementById('customModelBaseUrl')?.value.trim() || 'https://api.openai.com/v1';
    const model_id = document.getElementById('customModelId')?.value.trim() || '';
    const display_name = document.getElementById('customModelDisplayName')?.value.trim() || model_id;
    const api_key_env = document.getElementById('customModelKeyEnv')?.value.trim() || '';
    const api_key = document.getElementById('customModelApiKey')?.value.trim() || '';
    const pInRaw = document.getElementById('customModelPriceIn')?.value;
    const pOutRaw = document.getElementById('customModelPriceOut')?.value;
    const ctxRaw = document.getElementById('customModelContext')?.value;

    const payload = {
        provider,
        base_url,
        model_id,
        display_name,
        api_key_env: api_key_env || undefined,
        api_key: api_key || undefined,
        input_price_per_1m: pInRaw !== '' ? Number(pInRaw) : null,
        output_price_per_1m: pOutRaw !== '' ? Number(pOutRaw) : null,
        context_window: ctxRaw !== '' ? Number(ctxRaw) : null,
        enabled: true,
    };

    const resp = await fetch('/api/models/custom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (resp.ok) {
        const keyInput = document.getElementById('customModelApiKey');
        if (keyInput) keyInput.value = '';
        closeCustomModelModal();
        await refreshMetadataAndData();
    }
}

async function promptEditModelPricingAndLimits(modelKey) {
    const m = (APP_STATE.metadata?.models || []).find(x => x.id === modelKey);
    if (!m) return;
    const curIn = m.cost_per_1m_input_usd != null ? String(m.cost_per_1m_input_usd) : '';
    const curOut = m.cost_per_1m_output_usd != null ? String(m.cost_per_1m_output_usd) : '';
    const curRpm = (m.limits?.rpm ?? m.rpm) != null ? String(m.limits?.rpm ?? m.rpm) : '';
    const curRpd = (m.limits?.rpd ?? m.rpd) != null ? String(m.limits?.rpd ?? m.rpd) : '';

    const inVal = window.prompt(`Вхідний тариф ($ за 1M токенів) для ${m.name} (порожньо = не змінювати):`, curIn);
    if (inVal === null) return;
    const outVal = window.prompt(`Вихідний тариф ($ за 1M токенів) для ${m.name}:`, curOut);
    if (outVal === null) return;
    const rpmVal = window.prompt(`Ліміт запитів на хвилину (RPM) для ${m.name}:`, curRpm);
    if (rpmVal === null) return;
    const rpdVal = window.prompt(`Денний ліміт запитів (RPD) для ${m.name}:`, curRpd);
    if (rpdVal === null) return;

    const patch = {};
    if (inVal.trim() !== '') patch.input_price_per_1m = Number(inVal);
    if (outVal.trim() !== '') patch.output_price_per_1m = Number(outVal);
    if (rpmVal.trim() !== '') patch.rpm = Number(rpmVal);
    if (rpdVal.trim() !== '') patch.rpd = Number(rpdVal);

    await fetch(`/api/models/${encodeURIComponent(modelKey)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
    });
    await refreshMetadataAndData();
    if (APP_STATE.activeModalProviderId) {
        renderProviderModalListContent(APP_STATE.activeModalProviderId);
    }
}

async function deleteCustomModelEntry(modelKey) {
    await fetch(`/api/models/${encodeURIComponent(modelKey)}`, { method: 'DELETE' });
    await refreshMetadataAndData();
    if (APP_STATE.activeModalProviderId) {
        renderProviderModalListContent(APP_STATE.activeModalProviderId);
    }
}

const MIN_QUERIES_PER_DOMAIN = 30;
let _estimateRequestSeq = 0;

const LIMIT_SOURCE_LABELS = { header: 'за даними провайдера', manual: 'вказано вручну', catalog: 'припущення, не перевірено' };

function readExpForm() {
    const checked = name => Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map(el => el.value);
    const contextModes = checked('expContextMode');
    return {
        domains: getSelectedExpDomains(),
        modelIds: checked('expModel'),
        promptCodes: checked('expPrompt'),
        categories: checked('expCategory'),
        contextModes: contextModes.length ? contextModes : ['gold_context'],
        maxQueries: Number(document.getElementById('expMaxQueriesInput')?.value) || 2,
        repeats: Number(document.getElementById('expRepeatsInput')?.value) || 1,
        judgeModelId: document.getElementById('expJudgeModelSelect')?.value || null,
        equalDomains: document.getElementById('expEqualDomainSample')?.checked ?? true,
    };
}

function estimateSelectionHtml(sel) {
    const lacking = sel.by_domain.filter(d => d.selected < MIN_QUERIES_PER_DOMAIN);
    const rows = sel.by_domain.map(d => `<li>${escapeHtml(d.name)}: ${d.selected} з ${d.available} доступних</li>`).join('');
    const note = lacking.length
        ? `Для висновків за гіпотезами потрібно не менше ${MIN_QUERIES_PER_DOMAIN} запитів на сферу, а в ${lacking.map(d => escapeHtml(d.name)).join(', ')} їх менше. Додайте й затвердіть запити на вкладці «Дані».`
        : 'Запитів достатньо для висновків за гіпотезами.';
    return `<div>
        <div class="text-2xl font-semibold text-slate-900">${sel.total}</div>
        <div class="text-xs text-slate-500">запитів у серії (вибір випадковий, зерно ${sel.seed})</div>
        <ul class="text-xs text-slate-600 mt-1 space-y-0.5 list-disc pl-4">${rows}</ul>
        <p class="text-xs mt-1 ${lacking.length ? 'text-amber-800' : 'text-emerald-800'}">${note}</p>
    </div>`;
}

function estimateQuotaHtml(res) {
    const rows = (res.models || []).map(m => {
        const remaining = m.remaining_rpd === null || m.remaining_rpd === undefined ? 'ліміт невідомий' : `залишок ${m.remaining_rpd} з ${m.rpd}`;
        const source = m.rpd ? ` (${LIMIT_SOURCE_LABELS[m.rpd_source] || m.rpd_source})` : '';
        const bad = m.quota_exceeded || m.exhausted_now || m.blocked_status;
        const limitText = m.blocked_status ? 'немає доступу' : (m.exhausted_now ? 'ліміт на сьогодні вичерпано' : remaining + source);
        const today = m.blocked_status ? '0' : `${m.runnable_today} з ${m.calls}`;
        return `<tr class="border-b border-slate-100"><td class="py-1 pr-2">${escapeHtml(m.name)}</td><td class="py-1 pr-2">${today}</td>
            <td class="py-1 ${bad ? 'text-amber-800' : 'text-slate-600'}">${limitText}</td></tr>`;
    }).join('');
    const total = res.total_generation_calls || 0;
    const partial = res.runnable_calls_today < total;
    const days = res.days_needed > 1 ? ` Повна серія займе близько ${res.days_needed} діб: щодня вона продовжується з того ж місця.` : '';
    const summary = partial
        ? `<p class="text-xs text-amber-800 mt-2">Сьогодні вийде виконати ${res.runnable_calls_today} з ${total} відповідей. Модель, у якої закінчився ліміт, серія пропускає, решта працюють далі, а недоробку можна продовжити пізніше.${escapeHtml(days)}</p>`
        : '';
    return `<div>
        <div class="text-xs font-medium text-slate-700 mb-1">Що вийде виконати сьогодні</div>
        <table class="w-full text-xs text-left"><thead class="text-slate-500"><tr><th class="py-1 pr-2 font-medium">Модель</th><th class="py-1 pr-2 font-medium">Сьогодні з потрібних</th><th class="py-1 font-medium">Ліміт</th></tr></thead><tbody>${rows}</tbody></table>
        ${summary}
        ${res.quota_warnings?.length ? `<p class="text-xs text-amber-800 mt-1">${res.quota_warnings.map(escapeHtml).join('. ')}.</p>` : ''}
    </div>`;
}

function renderEstimate(box, res, form) {
    const combos = form.modelIds.length * form.promptCodes.length * form.contextModes.length;
    const costText = res.has_unknown_price
        ? (res.est_total_cost_usd > 0 ? `$${res.est_total_cost_usd.toFixed(4)} і ціна не задана для частини моделей` : 'ціна не задана')
        : `$${Number(res.est_total_cost_usd || 0).toFixed(4)}`;
    const mins = Math.floor((res.est_duration_sec || 0) / 60);
    const timeText = mins > 0 ? `близько ${mins} хв` : `близько ${res.est_duration_sec || 0} с`;
    box.innerHTML = `
        ${estimateSelectionHtml(res.selection)}
        <div class="border-t border-slate-200 pt-2">
            <div class="text-2xl font-semibold text-slate-900">${res.total_generation_calls}${res.total_judge_calls ? ` + ${res.total_judge_calls}` : ''}</div>
            <div class="text-xs text-slate-500">викликів${res.total_judge_calls ? ' (відповіді + суддя)' : ''}</div>
            <p class="text-xs text-slate-600 mt-1">${res.queries_count} запитів × ${form.modelIds.length} моделей × ${form.promptCodes.length} промптів × ${form.contextModes.length} режимів контексту × ${form.repeats} повторів. Комбінацій на запит: ${combos * form.repeats}.</p>
        </div>
        <div class="border-t border-slate-200 pt-2 text-xs text-slate-700 space-y-0.5">
            <div>Час: ${timeText}</div>
            <div>Вартість: ${escapeHtml(costText)}</div>
        </div>
        <div class="border-t border-slate-200 pt-2">${estimateQuotaHtml(res)}</div>`;
}

async function updateBatchExperimentEstimate() {
    const box = document.getElementById('expEstimateContent');
    if (!box) return;
    // Відповідь на давніший запит не повинна перезаписати новіший
    const requestId = ++_estimateRequestSeq;
    const form = readExpForm();

    if (!form.domains.length || !form.modelIds.length || !form.promptCodes.length || !form.categories.length) {
        box.innerHTML = '<span class="text-slate-500">Оберіть сфери, моделі, промпти та категорії, щоб побачити підсумок.</span>';
        return;
    }
    try {
        const res = await fetch('/api/experiments/estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                domain: form.domains[0],
                domains: form.domains,
                equal_domain_sample: form.equalDomains,
                model_ids: form.modelIds,
                prompt_codes: form.promptCodes,
                categories: form.categories,
                context_modes: form.contextModes,
                max_queries_per_category: form.maxQueries,
                repeats: form.repeats,
                use_judge: Boolean(form.judgeModelId),
                judge_model_id: form.judgeModelId,
            }),
        }).then(r => r.json());
        if (requestId !== _estimateRequestSeq) return;
        renderEstimate(box, res, form);
    } catch (_) {
        box.textContent = 'Не вдалося розрахувати підсумок.';
    }
}

async function openExportModal() {
    const runId = APP_STATE.selectedAnalyticsRunIds[0] || APP_STATE.currentRunId;
    if (!runId) return;
    const res = await fetch(`/api/experiments/${runId}/export`).then(r => r.json());
    APP_STATE.exportCache = res;
    document.getElementById('exportModal').classList.remove('hidden');
    document.getElementById('exportModal').classList.add('flex');
    switchExportFormat('markdown');
}

function closeExportModal() {
    document.getElementById('exportModal').classList.add('hidden');
    document.getElementById('exportModal').classList.remove('flex');
}

function switchExportFormat(fmt) {
    ['markdown', 'csv', 'latex'].forEach(k => {
        const btn = document.getElementById(`expFmtBtn-${k}`);
        if (btn) {
            btn.className = k === fmt
                ? 'px-3 py-1.5 rounded bg-blue-600 text-white font-semibold text-xs'
                : 'px-3 py-1.5 rounded bg-slate-200 text-slate-700 font-semibold text-xs';
        }
    });
    const area = document.getElementById('exportModalTextarea');
    if (fmt === 'markdown') area.value = APP_STATE.exportCache.markdown || '';
    if (fmt === 'csv') area.value = APP_STATE.exportCache.csv_models || '';
    if (fmt === 'latex') area.value = APP_STATE.exportCache.latex || '';
}

function copyExportText() {
    const area = document.getElementById('exportModalTextarea');
    area.select();
    navigator.clipboard.writeText(area.value);
}

function downloadExportFile(fmt) {
    const runId = APP_STATE.selectedAnalyticsRunIds[0] || APP_STATE.currentRunId;
    if (!runId) return;
    window.open(`/api/experiments/${runId}/download/${fmt}`, '_blank');
}
