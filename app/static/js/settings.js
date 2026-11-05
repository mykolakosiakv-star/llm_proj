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
    try {
        const resp = await fetch('/api/models/status/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_key: options.model_key || null, only_enabled: onlyEnabled, force: true }),
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
        setButton(idleLabel);
    }
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

// Скільки різних запитів реально піде в серію по кожній сфері і чи вистачає для висновків
function sampleSizeHintHtml(domain, categories, maxPerCategory, equalDomains) {
    const domainCodes = domain === 'all' ? Object.keys(APP_STATE.metadata?.domains || {}) : [domain];
    const perDomain = {};
    domainCodes.forEach(code => {
        perDomain[code] = categories.reduce((sum, cat) => {
            const available = (APP_STATE.queries || []).filter(q => (q.domain || 'fintech') === code && q.category === cat).length;
            return sum + Math.min(maxPerCategory, available);
        }, 0);
    });
    const counts = Object.values(perDomain);
    if (!counts.length) return '';
    const effective = equalDomains && domain === 'all' ? Math.min(...counts) : null;
    const lacking = Object.entries(perDomain).filter(([, n]) => (effective ?? n) < MIN_QUERIES_PER_DOMAIN);
    const names = APP_STATE.metadata?.domains || {};
    const summary = effective !== null
        ? `У кожній сфері піде по ${effective} запитів.`
        : Object.entries(perDomain).map(([code, n]) => `${names[code]?.name_uk || code}: ${n}`).join('; ') + '.';
    const warning = lacking.length
        ? `Для висновків за гіпотезами потрібно не менше ${MIN_QUERIES_PER_DOMAIN} запитів на сферу. Поки цього не вистачає, результат буде попереднім. Додайте запити на вкладці «Дані».`
        : 'Запитів достатньо для висновків за гіпотезами.';
    return `<div class="mb-1.5 ${lacking.length ? 'text-amber-800' : 'text-emerald-800'}">${escapeHtml(summary)} ${escapeHtml(warning)}</div>`;
}

async function updateBatchExperimentEstimate() {
    const box = document.getElementById('expEstimateContent');
    if (!box) return;

    const domain = document.getElementById('expDomainSelect')?.value || 'fintech';
    const modelIds = Array.from(document.querySelectorAll('input[name="expModel"]:checked')).map(el => el.value);
    const promptCodes = Array.from(document.querySelectorAll('input[name="expPrompt"]:checked')).map(el => el.value);
    const categories = Array.from(document.querySelectorAll('input[name="expCategory"]:checked')).map(el => el.value);
    const contextModes = Array.from(document.querySelectorAll('input[name="expContextMode"]:checked')).map(el => el.value);
    const maxQueries = Number(document.getElementById('expMaxQueriesInput')?.value) || 2;
    const repeats = Number(document.getElementById('expRepeatsInput')?.value) || 1;
    const judgeModelId = document.getElementById('expJudgeModelSelect')?.value || null;
    const equalDomains = document.getElementById('expEqualDomainSample')?.checked ?? true;

    if (!modelIds.length || !promptCodes.length || !categories.length) {
        box.innerHTML = `<span class="text-slate-500">Оберіть принаймні одну модель, промпт і категорію для розрахунку прогнозу.</span>`;
        return;
    }

    try {
        const res = await fetch('/api/experiments/estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                domain,
                model_ids: modelIds,
                prompt_codes: promptCodes,
                categories,
                context_modes: contextModes.length ? contextModes : ['gold_context'],
                max_queries_per_category: maxQueries,
                repeats,
                use_judge: Boolean(judgeModelId),
                judge_model_id: judgeModelId,
            }),
        }).then(r => r.json());

        const costText = res.has_unknown_price
            ? (res.est_total_cost_usd > 0 ? `$${res.est_total_cost_usd.toFixed(4)} + ціна не задана` : 'ціна не задана')
            : `$${Number(res.est_total_cost_usd || 0).toFixed(4)}`;
        const mins = Math.floor((res.est_duration_sec || 0) / 60);
        const secs = (res.est_duration_sec || 0) % 60;
        const timeStr = mins > 0 ? `~${mins} хв ${secs} с` : `~${secs} с`;

        let warnHtml = '';
        if (res.quota_warnings && res.quota_warnings.length > 0) {
            warnHtml = `<div class="mt-1.5 p-2 rounded bg-amber-50 border border-amber-300 text-amber-900 font-semibold">
                Попередження RPD: ${res.quota_warnings.map(w => escapeHtml(w)).join('; ')}
            </div>`;
        }

        box.innerHTML = `
            ${sampleSizeHintHtml(domain, categories, maxQueries, equalDomains)}
            <div class="flex flex-wrap justify-between gap-2">
                <span>Викликів генерації: <b>${res.total_generation_calls}</b>${res.total_judge_calls > 0 ? ` + суддя: <b>${res.total_judge_calls}</b>` : ''}</span>
                <span>Орієнтовний час: <b>${timeStr}</b></span>
            </div>
            <div class="flex flex-wrap justify-between gap-2 mt-0.5">
                <span>Оцінка вартості: <b class="font-mono">${escapeHtml(costText)}</b></span>
                <span>Денна квота: <b class="${res.quota_sufficient ? 'text-emerald-700' : 'text-amber-800'}">${res.quota_sufficient ? 'достатньо' : 'перевищення RPD'}</b></span>
            </div>
            ${warnHtml}
        `;
    } catch (_) {
        box.textContent = 'Не вдалося розрахувати прогноз.';
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
