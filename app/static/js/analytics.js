// ============================================================================
// ГНУЧКЕ ПОРІВНЯННЯ, ДОВІРЧІ ІНТЕРВАЛИ, ПАРЕТО ТА СТАТТЕСТИ
// ============================================================================

function updateAnalyticsVisibility() {
    const emptyState = document.getElementById('analyticsEmptyState');
    const contentWrap = document.getElementById('analyticsContentWrap');

    if (!APP_STATE.runs || APP_STATE.runs.length === 0) {
        emptyState.classList.remove('hidden');
        contentWrap.classList.add('hidden');
        return;
    }

    emptyState.classList.add('hidden');
    contentWrap.classList.remove('hidden');

    if (!APP_STATE.selectedAnalyticsRunIds || APP_STATE.selectedAnalyticsRunIds.length === 0) {
        APP_STATE.selectedAnalyticsRunIds = [APP_STATE.runs[0].id];
    }

    renderAnalyticsRunCheckboxes();
    loadFlexibleAnalytics(true);
}

function renderAnalyticsRunCheckboxes() {
    const box = document.getElementById('analyticsRunsCheckboxes');
    if (!box) return;
    const selectedSet = new Set(APP_STATE.selectedAnalyticsRunIds);

    box.innerHTML = APP_STATE.runs.map(r => {
        const statusShort = r.status === 'completed' ? 'OK' : 'Неповна';
        return `
            <label class="flex items-center justify-between gap-2 p-1.5 rounded bg-white border ${selectedSet.has(r.id) ? 'border-blue-400 bg-blue-50/40' : 'border-slate-200'} cursor-pointer">
                <div class="flex items-center gap-2 truncate">
                    <input type="checkbox" name="anRunId" value="${r.id}" ${selectedSet.has(r.id) ? 'checked' : ''} onchange="onAnalyticsRunsSelectionChange()" class="rounded text-blue-600">
                    <span class="font-semibold text-slate-800 truncate">#${r.id} (${statusShort}): ${escapeHtml(r.title)}</span>
                </div>
                <span class="text-[10px] font-mono bg-slate-100 px-1.5 py-0.5 rounded text-slate-600 shrink-0">${r.completed_items}/${r.total_items}</span>
            </label>
        `;
    }).join('');
}

function selectAllAnalyticsRuns(selectAll) {
    if (!APP_STATE.runs.length) return;
    if (selectAll) {
        APP_STATE.selectedAnalyticsRunIds = APP_STATE.runs.map(r => r.id);
    } else {
        APP_STATE.selectedAnalyticsRunIds = [APP_STATE.runs[0].id];
    }
    renderAnalyticsRunCheckboxes();
    loadFlexibleAnalytics(true);
}

function onAnalyticsRunsSelectionChange() {
    const checked = Array.from(document.querySelectorAll('input[name="anRunId"]:checked')).map(el => Number(el.value));
    if (checked.length === 0 && APP_STATE.runs.length > 0) {
        APP_STATE.selectedAnalyticsRunIds = [APP_STATE.runs[0].id];
        renderAnalyticsRunCheckboxes();
    } else {
        APP_STATE.selectedAnalyticsRunIds = checked;
    }
    loadFlexibleAnalytics(true);
}

function applyFlexibleAnalyticsFilters() {
    loadFlexibleAnalytics(false);
}

function renderAnalyticsSubFilterCheckboxes(availableFilters) {
    APP_STATE.analyticsAvailableFilters = availableFilters;

    const modelsBox = document.getElementById('analyticsModelsFilterBox');
    modelsBox.innerHTML = (availableFilters.models || []).map(m => `
        <label class="flex items-center gap-2 p-1 rounded bg-white border border-slate-200 cursor-pointer">
            <input type="checkbox" name="anModel" value="${m.id}" checked onchange="loadFlexibleAnalytics(false)" class="rounded text-blue-600">
            <span class="font-medium text-slate-800 truncate">${escapeHtml(m.name)}</span>
        </label>
    `).join('');

    const promptsBox = document.getElementById('analyticsPromptsFilterBox');
    promptsBox.innerHTML = (availableFilters.prompts || []).map(p => `
        <label class="flex items-center gap-2 p-1 rounded bg-white border border-slate-200 cursor-pointer">
            <input type="checkbox" name="anPrompt" value="${p.code}" checked onchange="loadFlexibleAnalytics(false)" class="rounded text-blue-600">
            <span class="font-semibold text-indigo-800">${escapeHtml(p.code)}</span>
        </label>
    `).join('');

    const domainsBox = document.getElementById('analyticsDomainsFilterBox');
    if (domainsBox) {
        domainsBox.innerHTML = (availableFilters.domains || []).map(d => `
            <label class="flex items-center gap-2 p-1 rounded bg-white border border-slate-200 cursor-pointer">
                <input type="checkbox" name="anDomain" value="${escapeHtml(d.code)}" checked onchange="loadFlexibleAnalytics(false)" class="rounded text-blue-600">
                <span class="font-medium text-slate-800 truncate">${escapeHtml(d.name_uk)}</span>
            </label>
        `).join('');
    }

    const catsBox = document.getElementById('analyticsCategoriesFilterBox');
    catsBox.innerHTML = (availableFilters.categories || []).map(c => `
        <label class="flex items-center gap-2 p-1 rounded bg-white border border-slate-200 cursor-pointer">
            <input type="checkbox" name="anCategory" value="${c.code}" checked onchange="loadFlexibleAnalytics(false)" class="rounded text-blue-600">
            <span class="font-medium text-slate-800 truncate">${escapeHtml(c.name_uk)}</span>
        </label>
    `).join('');
}

function toggleAllSubFilters(inputName, checkState) {
    document.querySelectorAll(`input[name="${inputName}"]`).forEach(el => {
        el.checked = checkState;
    });
    loadFlexibleAnalytics(false);
}

let _analyticsRequestSeq = 0;

function checkedValues(inputName, rebuild) {
    return rebuild ? null : Array.from(document.querySelectorAll(`input[name="${inputName}"]:checked`)).map(el => el.value);
}

function showAnalyticsError(message) {
    const box = document.getElementById('analyticsNoticesBanner');
    if (box) box.innerHTML = `<div class="p-3 rounded-lg border border-rose-300 bg-rose-50 text-rose-800 text-sm">${escapeHtml(message)}</div>`;
}

async function loadFlexibleAnalytics(rebuildSubFilters = false) {
    if (!APP_STATE.selectedAnalyticsRunIds || APP_STATE.selectedAnalyticsRunIds.length === 0) return;

    // Відповідь на давніший запит не повинна перезаписати новіший, якщо фільтри перемикали швидко
    const requestId = ++_analyticsRequestSeq;
    let data;
    try {
        const resp = await fetch('/api/analytics/flexible', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                run_ids: APP_STATE.selectedAnalyticsRunIds,
                model_ids: checkedValues('anModel', rebuildSubFilters),
                prompt_codes: checkedValues('anPrompt', rebuildSubFilters),
                categories: checkedValues('anCategory', rebuildSubFilters),
                domains: checkedValues('anDomain', rebuildSubFilters),
                balanced_only: document.getElementById('anBalancedOnlyCheckbox')?.checked ?? true,
                include_self_judged: document.getElementById('anIncludeSelfJudgedCheckbox')?.checked ?? false,
            })
        });
        if (!resp.ok) throw new Error(`Сервер повернув помилку ${resp.status}`);
        data = await resp.json();
    } catch (err) {
        if (requestId === _analyticsRequestSeq) showAnalyticsError(`Не вдалося завантажити результати: ${err.message}`);
        return;
    }
    if (requestId !== _analyticsRequestSeq || !data || !data.overall) return;

    if (rebuildSubFilters && data.available_filters) {
        renderAnalyticsSubFilterCheckboxes(data.available_filters);
    }

    document.getElementById('analyticsRunMeta').textContent =
        `Обрано серій: ${APP_STATE.selectedAnalyticsRunIds.length} | Замірів у вибірці: ${data.overall.count} (до балансування: ${data.total_raw_items ?? data.overall.count})`;

    APP_STATE.latestAnalyticsData = data;
    renderAnalyticsBanners(data);
    renderResultsOverview(data);
    renderConclusion(data.conclusion);
    renderCascadeControls(data);
    runCascadeSimulation({ silent: true });
    renderDecisionSupportAdvisor(data);
    initRoiCalculator(data);
    renderMultiRunsTable(data.by_run || []);
    renderAnalyticsCharts(data);
    renderModelsComparisonTable(data.by_model || []);
    renderPromptsComparisonTable(data.by_prompt || []);
    renderHeatmaps(data);
    renderStatisticalTestsAndEscalation(data);
}

function renderConclusion(c) {
    const card = document.getElementById('conclusionCard');
    if (!card) return;
    if (!c || !c.available) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    document.getElementById('conclusionHeadline').textContent = c.headline_uk;

    const t = c.tradeoffs;
    const cost = t.cost_per_1m_usd === null ? 'не задана' : `$${t.cost_per_1m_usd.toFixed(2)} за 1 млн токенів`;
    const items = [
        ['Якість', `${t.quality_pct}% (найкраща у вибірці: ${t.best_quality_pct}%)`],
        ['Ціна', cost],
        ['Безпека', `пропуски ескалації ${t.fn_rate_pct}%, галюцинації ${t.hallucination_rate_pct}%`],
    ];
    document.getElementById('conclusionTradeoffs').innerHTML = items.map(([k, v]) =>
        `<div class="border border-slate-200 rounded-lg p-3"><dt class="text-xs text-slate-500">${k}</dt><dd class="mt-1 text-slate-900">${v}</dd></div>`
    ).join('');

    document.getElementById('conclusionCaveats').innerHTML =
        (c.caveats_uk || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
}

function renderCascadeControls(data) {
    const card = document.getElementById('cascadeCard');
    const models = data.by_model || [];
    if (!card) return;
    card.classList.toggle('hidden', models.length < 2);
    if (models.length < 2) return;

    const keep = id => document.getElementById(id).value;
    const prev = { cheap: keep('cascadeCheap'), strong: keep('cascadeStrong'), prompt: keep('cascadePrompt') };
    const options = models.map(m => `<option value="${escapeHtml(m.model_id)}">${escapeHtml(m.model_name)}</option>`).join('');
    const cheapSel = document.getElementById('cascadeCheap');
    const strongSel = document.getElementById('cascadeStrong');
    cheapSel.innerHTML = options;
    strongSel.innerHTML = options;

    const priced = models.filter(m => m.price_known && m.cost_per_1m_usd !== null);
    const cheapDefault = (priced.length ? priced.reduce((a, b) => (a.cost_per_1m_usd <= b.cost_per_1m_usd ? a : b)) : models[0]).model_id;
    const strongDefault = models.filter(m => m.model_id !== cheapDefault)
        .reduce((a, b) => (a.integral_efficiency_pct >= b.integral_efficiency_pct ? a : b)).model_id;
    cheapSel.value = models.some(m => m.model_id === prev.cheap) ? prev.cheap : cheapDefault;
    strongSel.value = models.some(m => m.model_id === prev.strong) ? prev.strong : strongDefault;

    const filters = data.available_filters || {};
    const promptSel = document.getElementById('cascadePrompt');
    promptSel.innerHTML = '<option value="">Усі стратегії</option>' +
        (filters.prompts || []).map(p => `<option value="${escapeHtml(p.code)}">${escapeHtml(p.name_uk)}</option>`).join('');
    promptSel.value = prev.prompt || '';

    const risky = document.getElementById('cascadeRiskyCategories');
    if (!risky.children.length) {
        risky.innerHTML = (filters.categories || []).map(c =>
            `<label class="flex items-center gap-1.5"><input type="checkbox" name="cascadeRisky" value="${escapeHtml(c.code)}">${escapeHtml(c.name_uk)}</label>`
        ).join('');
    }
}

async function runCascadeSimulation(options = {}) {
    const errBox = document.getElementById('cascadeError');
    const result = document.getElementById('cascadeResult');
    errBox.classList.add('hidden');
    const cheap = document.getElementById('cascadeCheap').value;
    const strong = document.getElementById('cascadeStrong').value;
    if (!cheap || !strong) return;
    if (cheap === strong) {
        if (!options.silent) {
            errBox.textContent = 'Оберіть дві різні моделі.';
            errBox.classList.remove('hidden');
        }
        return;
    }
    try {
        const resp = await fetch('/api/cascade/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                run_ids: APP_STATE.selectedAnalyticsRunIds,
                cheap_model_id: cheap,
                strong_model_id: strong,
                strong_first_categories: Array.from(document.querySelectorAll('input[name="cascadeRisky"]:checked')).map(el => el.value),
                prompt_code: document.getElementById('cascadePrompt').value || null,
                context_mode: document.getElementById('cascadeContext').value || null,
            }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Не вдалося виконати розрахунок');
        if (!data.available) throw new Error(data.reason_uk);
        renderCascadeResult(data);
    } catch (err) {
        result.classList.add('hidden');
        errBox.textContent = err.message;
        errBox.classList.remove('hidden');
    }
}

function renderCascadeResult(res) {
    document.getElementById('cascadeResult').classList.remove('hidden');
    const pct = v => (v === null ? 'немає даних' : `${v}%`);
    const names = { cheap_only: 'Лише дешева модель', strong_only: 'Лише сильна модель', cascade: 'Каскад' };
    document.getElementById('cascadeTableBody').innerHTML = Object.entries(names).map(([key, label]) => {
        const p = res.policies[key];
        const price = p.cost_per_1000_usd === null ? 'ціну не задано' : `$${p.cost_per_1000_usd.toFixed(2)}`;
        const cls = key === 'cascade' ? 'font-semibold' : '';
        return `<tr class="border-b border-slate-100 ${cls}"><td class="py-2 pr-3">${label}</td><td class="py-2 pr-3">${p.auto_pct}%</td>` +
            `<td class="py-2 pr-3">${p.operator_pct}%</td><td class="py-2 pr-3">${pct(p.missed_escalation_pct)}</td>` +
            `<td class="py-2 pr-3">${pct(p.quality_of_auto_answers_pct)}</td><td class="py-2 pr-3">${pct(p.hallucination_in_auto_pct)}</td>` +
            `<td class="py-2">${price}</td></tr>`;
    }).join('');

    const c = res.policies.cascade, s = res.policies.strong_only;
    const parts = [`Розрахунок на ${res.n_queries} спільних запитах.`];
    const saving = res.cost_saving_vs_strong_pct;
    if (saving !== null) {
        parts.push(saving >= 0
            ? `Каскад дешевший за сильну модель на ${saving}%.`
            : `Каскад дорожчий за сильну модель на ${Math.abs(saving)}%: обрана «дешева» модель не дешевша за сильну.`);
    }
    if (c.missed_escalation_pct !== null && s.missed_escalation_pct !== null) {
        parts.push(`Пропущені ескалації: ${c.missed_escalation_pct}% проти ${s.missed_escalation_pct}% у сильної моделі.`);
    }
    document.getElementById('cascadeSummary').textContent = parts.join(' ');
    document.getElementById('cascadeCaveats').innerHTML = (res.caveats_uk || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
}

function selectDeploymentPreset(presetId) {
    APP_STATE.activeDeploymentPreset = presetId;
    ['balanced', 'safety_first', 'high_speed', 'cost_effective'].forEach(pid => {
        const btn = document.getElementById(`presetBtn-${pid}`);
        if (btn) {
            btn.className = pid === presetId
                ? 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 text-white shadow-sm transition'
                : 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-700 hover:bg-slate-100 transition';
        }
    });
    if (APP_STATE.latestAnalyticsData) {
        renderDecisionSupportAdvisor(APP_STATE.latestAnalyticsData);
    }
}

function renderDecisionSupportAdvisor(data) {
    const card = document.getElementById('decisionSupportCard');
    const body = document.getElementById('decisionSupportBody');
    if (!card || !body) return;

    const ds = data.decision_support;
    if (!ds || !ds.available || !ds.presets) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');

    const presetId = APP_STATE.activeDeploymentPreset || 'balanced';
    const preset = ds.presets[presetId] || ds.presets.balanced;
    if (!preset) return;

    const wm = preset.winner_model;
    const wp = preset.winner_prompt;
    const rankedModels = preset.ranked_models || [];
    const warnings = ds.risk_warnings || [];

    const leaderboardRows = rankedModels.map((m, idx) => {
        const rankBadge = idx === 0
            ? `<span class="px-2 py-0.5 rounded bg-blue-600 text-white font-bold text-[10px]">Рекомендовано (#1)</span>`
            : `<span class="px-2 py-0.5 rounded bg-slate-100 text-slate-700 font-mono text-[10px]">#${idx + 1}</span>`;
        const paretoTag = m.is_pareto
            ? `<span class="ml-1 px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 text-[10px] font-semibold">Парето-опт.</span>`
            : '';
        return `
            <tr class="${idx === 0 ? 'bg-blue-50/60 font-semibold' : 'hover:bg-slate-50'}">
                <td class="py-2 px-2.5">${rankBadge} <span class="ml-1 font-bold text-slate-900">${escapeHtml(m.model_name)}</span>${paretoTag}</td>
                <td class="py-2 px-2 text-center">
                    <div class="flex items-center justify-center gap-2">
                        <span class="font-extrabold text-blue-800">${m.deployment_score_pct}%</span>
                        <div class="w-16 h-2 bg-slate-200 rounded-full overflow-hidden hidden sm:block">
                            <div class="bg-blue-600 h-full rounded-full" style="width: ${Math.min(100, m.deployment_score_pct)}%"></div>
                        </div>
                    </div>
                </td>
                <td class="py-2 px-2 text-center font-semibold text-emerald-700">${m.fact_coverage_pct}%</td>
                <td class="py-2 px-2 text-center">${m.integral_efficiency_pct}%</td>
                <td class="py-2 px-2 text-center ${m.hallucination_rate_pct > 0 ? 'text-rose-700 font-bold' : 'text-emerald-700'}">${m.hallucination_rate_pct}%</td>
                <td class="py-2 px-2 text-center font-semibold text-blue-700">${m.automation_rate_pct}%</td>
                <td class="py-2 px-2 text-center font-mono">${m.latency_mean_ms} мс</td>
                <td class="py-2 px-2 text-center font-mono">$${Number(m.cost_per_1m_usd || 0).toFixed(2)}</td>
            </tr>
        `;
    }).join('');

    body.innerHTML = `
        <div class="p-3 rounded-lg bg-white border border-blue-200 text-slate-700 flex flex-wrap items-center justify-between gap-2">
            <div>
                <span class="font-bold text-blue-950">Активний профіль критеріїв: ${escapeHtml(preset.name_uk)}</span>
                <div class="text-[11px] text-slate-500 mt-0.5">${escapeHtml(preset.description_uk)}</div>
            </div>
            <div class="text-[11px] font-mono bg-blue-50 px-2.5 py-1 rounded border border-blue-200 text-blue-900">
                Ваги: Якість=${Math.round(preset.weights.quality * 100)}% | Повнота=${Math.round(preset.weights.fact_coverage * 100)}% | Безпека=${Math.round(preset.weights.safety * 100)}% | Швидкість=${Math.round(preset.weights.speed * 100)}% | Ціна=${Math.round(preset.weights.cost * 100)}%
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3.5">
            <div class="p-4 rounded-xl bg-white border-2 border-emerald-300 shadow-sm space-y-2">
                <div class="flex items-center justify-between">
                    <span class="text-[11px] font-bold uppercase tracking-wider text-emerald-800">Оптимальна модель для впровадження</span>
                    <span class="px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-900 font-extrabold text-xs">
                        Індекс придатності: ${wm ? wm.deployment_score_pct : 0}%
                    </span>
                </div>
                <div class="text-base font-extrabold text-slate-900">${wm ? escapeHtml(wm.model_name) : '-'}</div>
                <p class="text-slate-600 leading-relaxed">${escapeHtml(preset.model_justification_uk)}</p>
            </div>

            <div class="p-4 rounded-xl bg-white border-2 border-indigo-200 shadow-sm space-y-2">
                <div class="flex items-center justify-between">
                    <span class="text-[11px] font-bold uppercase tracking-wider text-indigo-800">Рекомендована стратегія промпту</span>
                    <span class="px-2.5 py-0.5 rounded-full bg-indigo-100 text-indigo-900 font-extrabold text-xs">
                        Індекс стратегії: ${wp ? wp.deployment_score_pct : 0}%
                    </span>
                </div>
                <div class="text-base font-extrabold text-slate-900">${wp ? escapeHtml(wp.prompt_name_uk) : '-'}</div>
                <p class="text-slate-600 leading-relaxed">${escapeHtml(preset.prompt_justification_uk)}</p>
            </div>
        </div>

        <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
            <div class="px-3.5 py-2 bg-slate-100 border-b border-slate-200 font-bold text-slate-800">
                Підсумковий рейтинг моделей за обраним профілем впровадження
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse text-xs">
                    <thead>
                        <tr class="bg-slate-50 text-slate-600 border-b border-slate-200">
                            <th class="py-2 px-2.5">Місце та модель</th>
                            <th class="py-2 px-2 text-center">Індекс придатності</th>
                            <th class="py-2 px-2 text-center">Повнота FC</th>
                            <th class="py-2 px-2 text-center">Якість Q<sub>det</sub></th>
                            <th class="py-2 px-2 text-center">Галюцинації</th>
                            <th class="py-2 px-2 text-center">Автоматиз. AR</th>
                            <th class="py-2 px-2 text-center">Затримка</th>
                            <th class="py-2 px-2 text-center">Ціна ($/1M)</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-200">${leaderboardRows}</tbody>
                </table>
            </div>
        </div>

        <div class="p-3.5 rounded-xl bg-amber-50/70 border border-amber-200 space-y-1.5">
            <div class="font-bold text-amber-950">Аудит ризиків та практичні зауваження перед впровадженням:</div>
            <ul class="list-disc list-inside text-slate-700 space-y-1">
                ${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}
            </ul>
        </div>
    `;
}

function initRoiCalculator(data) {
    const card = document.getElementById('roiCalculatorCard');
    const sel = document.getElementById('roiModelSelect');
    if (!card || !sel) return;

    const byModel = data.by_model || [];
    if (!byModel.length) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');

    const prevId = sel.value;
    const dsWinner = data.decision_support?.presets?.[APP_STATE.activeDeploymentPreset || 'balanced']?.winner_model?.model_id;
    const targetId = (prevId && byModel.some(m => m.model_id === prevId))
        ? prevId
        : (dsWinner || byModel[0].model_id);

    sel.innerHTML = byModel.map(m =>
        `<option value="${m.model_id}" ${m.model_id === targetId ? 'selected' : ''}>${escapeHtml(m.model_name)} (AR=${m.automation_rate_pct}%, $${Number(m.cost_per_1m_usd || 0).toFixed(2)}/1M)</option>`
    ).join('');

    recalculateRoiWidget();
}

function recalculateRoiWidget() {
    const data = APP_STATE.latestAnalyticsData;
    const grid = document.getElementById('roiCalculatorResultsGrid');
    if (!data || !grid) return;

    const byModel = data.by_model || [];
    const modelId = document.getElementById('roiModelSelect')?.value;
    const m = byModel.find(x => x.model_id === modelId) || byModel[0];
    if (!m) return;

    const monthlyVolume = Math.max(100, Number(document.getElementById('roiMonthlyVolumeInput')?.value) || 10000);
    const opMinutesPerTicket = Math.max(0.5, Number(document.getElementById('roiOperatorMinutesInput')?.value) || 6);
    const opHourlyUsd = Math.max(0.5, Number(document.getElementById('roiOperatorHourlyUsdInput')?.value) || 8);

    const arRatio = Math.min(1.0, Math.max(0.0, Number(m.automation_rate_pct || 0) / 100.0));
    const automatedTickets = Math.round(monthlyVolume * arRatio);
    const escalatedTickets = monthlyVolume - automatedTickets;

    const totalManualHours = (monthlyVolume * opMinutesPerTicket) / 60.0;
    const savedOperatorHours = (automatedTickets * opMinutesPerTicket) / 60.0;
    const remainingOperatorHours = (escalatedTickets * opMinutesPerTicket) / 60.0;
    const savedFte = (savedOperatorHours / 160.0).toFixed(1);

    const manualTotalCostUsd = totalManualHours * opHourlyUsd;
    const remainingOpCostUsd = remainingOperatorHours * opHourlyUsd;

    const avgTokensPerTicket = Math.max(150, Number(m.avg_input_tokens || 320) + Number(m.avg_output_tokens || 180));
    const totalMonthlyTokens = monthlyVolume * avgTokensPerTicket;
    const costPer1m = Number(m.cost_per_1m_usd ?? ((m.cost_per_1k_usd || 0) * 1000));
    const llmApiMonthlyCostUsd = (totalMonthlyTokens / 1_000_000.0) * costPer1m;

    const hybridTotalCostUsd = remainingOpCostUsd + llmApiMonthlyCostUsd;
    const netMonthlySavingsUsd = Math.max(0.0, manualTotalCostUsd - hybridTotalCostUsd);
    const savingsPct = manualTotalCostUsd > 0 ? Math.round((netMonthlySavingsUsd / manualTotalCostUsd) * 100) : 0;

    grid.innerHTML = `
        <div class="p-3.5 rounded-xl bg-blue-50/60 border border-blue-200 space-y-1">
            <div class="text-[11px] text-slate-600 font-medium">Автоматично закриті запити</div>
            <div class="text-lg font-extrabold text-blue-900">${automatedTickets.toLocaleString('uk-UA')} / міс</div>
            <div class="text-[11px] text-slate-500">Автоматизація AR = <b>${m.automation_rate_pct}%</b> (оператору: ${escalatedTickets.toLocaleString('uk-UA')})</div>
        </div>
        <div class="p-3.5 rounded-xl bg-indigo-50/60 border border-indigo-200 space-y-1">
            <div class="text-[11px] text-slate-600 font-medium">Вивільнений час операторів</div>
            <div class="text-lg font-extrabold text-indigo-900">${Math.round(savedOperatorHours).toLocaleString('uk-UA')} люд.-год / міс</div>
            <div class="text-[11px] text-slate-500">Еквівалент <b>${savedFte} FTE</b> штатних спеціалістів (із ${Math.round(totalManualHours)} год)</div>
        </div>
        <div class="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1">
            <div class="text-[11px] text-slate-600 font-medium">Витрати на API LLM (${escapeHtml(m.model_name)})</div>
            <div class="text-lg font-extrabold text-slate-900 font-mono">$${llmApiMonthlyCostUsd.toFixed(2)} / міс</div>
            <div class="text-[11px] text-slate-500">Обсяг: ~${(totalMonthlyTokens / 1_000_000).toFixed(2)}M токенів по $${costPer1m.toFixed(2)}/1M</div>
        </div>
        <div class="p-3.5 rounded-xl bg-emerald-50 border-2 border-emerald-300 space-y-1">
            <div class="text-[11px] text-emerald-900 font-bold">Чиста економія бюджету підтримки</div>
            <div class="text-lg font-extrabold text-emerald-800 font-mono">$${netMonthlySavingsUsd.toFixed(0)} / міс (-${savingsPct}%)</div>
            <div class="text-[11px] text-emerald-700">Зі <b>$${manualTotalCostUsd.toFixed(0)}</b> (вручну) до <b>$${hybridTotalCostUsd.toFixed(0)}</b> (LLM + оператори)</div>
        </div>
    `;
}

function exportManualSampleCsv() {
    const runId = APP_STATE.selectedAnalyticsRunIds?.[0] || APP_STATE.currentRunId;
    if (!runId) return;
    window.open(`/api/experiments/${runId}/manual-sample/export?max_items=60`, '_blank');
}

function triggerImportManualSampleCsv() {
    const inp = document.getElementById('manualSampleCsvInput');
    if (inp) {
        inp.value = '';
        inp.click();
    }
}

async function handleImportManualSampleCsv(event) {
    const file = event?.target?.files?.[0];
    const runId = APP_STATE.selectedAnalyticsRunIds?.[0] || APP_STATE.currentRunId;
    if (!file || !runId) return;
    try {
        const csvText = await file.text();
        const resp = await fetch(`/api/experiments/${runId}/manual-sample/import`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ csv_text: csvText, annotator: 'expert' }),
        });
        const res = await resp.json();
        if (!resp.ok) {
            alert(res.detail || 'Помилка імпорту CSV');
            return;
        }
        await loadFlexibleAnalytics(false);
    } catch (e) {
        alert(`Помилка завантаження файлу: ${e.message}`);
    }
}

function renderAnalyticsBanners(data) {
    const box = document.getElementById('analyticsNoticesBanner');
    if (!box) return;
    const notices = [];

    const selectedRuns = APP_STATE.runs.filter(r => APP_STATE.selectedAnalyticsRunIds.includes(r.id));
    const hasIncomplete = selectedRuns.some(r => r.status !== 'completed');

    if (hasIncomplete) {
        notices.push(`
            <div class="p-3 rounded-lg bg-amber-50 border border-amber-300 text-amber-900 text-xs flex items-center justify-between gap-2">
                <div>
                    <b>Увага щодо вибірки:</b> Обрана вибірка містить неповні серії, перервані через ліміти API.
                    Детерміновані метрики перераховуються автоматично, а опція збалансованої вибірки залишає лише спільні запити, протестовані на всіх умовах (${data.balanced_pairs_count ?? data.balance_info?.common_queries_count ?? 0} спільних запитів).
                </div>
            </div>
        `);
    }

    const rDiag = data.rubric_diagnostics || {};
    if (rDiag.multiple_judges_warning_uk) {
        notices.push(`
            <div class="p-2.5 rounded-lg bg-amber-50 border border-amber-300 text-amber-950 text-xs flex items-center justify-between gap-2">
                <span><b>Неоднорідність судді:</b> ${escapeHtml(rDiag.multiple_judges_warning_uk)}</span>
                <span class="px-2 py-0.5 rounded bg-amber-200 text-amber-900 font-semibold text-[11px]">основний суддя: ${escapeHtml(rDiag.dominant_judge_model || '')}</span>
            </div>
        `);
    }

    if (rDiag.ceiling_effect_detected && rDiag.ceiling_warning_uk) {
        notices.push(`
            <div class="p-2.5 rounded-lg bg-rose-50 border border-rose-300 text-rose-900 text-xs flex items-center justify-between gap-2">
                <span><b>Ефект стелі рубрики:</b> ${escapeHtml(rDiag.ceiling_warning_uk)}</span>
                <span class="px-2 py-0.5 rounded bg-rose-100 text-rose-800 font-semibold text-[11px]">ефект стелі (${rDiag.top_10pct_share_pct}%)</span>
            </div>
        `);
    }

    if ((rDiag.skipped_no_independent_judge_count || 0) > 0 || (rDiag.self_judged_count || 0) > 0) {
        notices.push(`
            <div class="p-2.5 rounded-lg bg-slate-100 border border-slate-300 text-slate-800 text-xs flex flex-wrap items-center justify-between gap-2">
                <span>
                    <b>Політика незалежності судді:</b>
                    незалежно оцінено: <b>${rDiag.independent_judged_count ?? 0}</b> |
                    не оцінено (немає незалежного судді): <b>${rDiag.skipped_no_independent_judge_count ?? 0}</b> |
                    самооцінювання (self_judged=1): <b>${rDiag.self_judged_count ?? 0}</b> (${rDiag.include_self_judged ? 'включено' : 'виключено з рубрики'}).
                </span>
            </div>
        `);
    }

    const ov = data.overall || {};
    if ((ov.rubric_evaluated_count || 0) === 0 && (ov.manual_scored_count || 0) === 0) {
        notices.push(`
            <div class="p-2.5 rounded-lg bg-indigo-50/70 border border-indigo-200 text-indigo-900 text-xs flex flex-wrap items-center justify-between gap-2">
                <span><b>Режим оцінювання рубрики (1-5):</b> у поточній вибірці бали 1-5 обчислені лінійним відображенням із детермінованих метрик без виклику незалежного LLM-судді.</span>
                <span class="px-2 py-0.5 rounded bg-indigo-100 text-indigo-800 font-semibold text-[11px]">детермінована оцінка</span>
            </div>
        `);
    }

    box.innerHTML = notices.join('');
}

function renderMultiRunsTable(byRun) {
    const card = document.getElementById('multiRunsComparisonCard');
    const tbody = document.getElementById('tableMultiRunsBody');
    if (!card || !tbody) return;

    if (!byRun || byRun.length < 2) {
        card.classList.add('hidden');
        return;
    }

    card.classList.remove('hidden');
    tbody.innerHTML = byRun.map(r => `
        <tr class="hover:bg-slate-50">
            <td class="py-2 px-3 font-bold text-slate-900">Серія #${r.run_id}: ${escapeHtml(r.run_title)} <span class="text-[11px] font-normal text-slate-500">(${r.created_at})</span></td>
            <td class="py-2 px-2 text-center"><span class="px-1.5 py-0.5 rounded ${r.status === 'completed' ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'} text-[10px] font-bold">${escapeHtml(r.status || 'completed')}</span></td>
            <td class="py-2 px-2 text-center font-mono">${r.count}</td>
            <td class="py-2 px-2 text-center"><span class="px-2 py-0.5 rounded ${getHeatClass(r.integral_efficiency_pct)}">${r.integral_efficiency_pct}%</span></td>
            <td class="py-2 px-2 text-center font-semibold text-emerald-700">${r.fact_coverage_pct}%</td>
            <td class="py-2 px-2 text-center">${r.semantic_similarity_pct}%</td>
            <td class="py-2 px-2 text-center">${r.nist_composite_pct}%</td>
            <td class="py-2 px-2 text-center font-bold text-blue-700">${r.automation_rate_pct}%</td>
            <td class="py-2 px-2 text-center">${r.latency_mean_ms} мс</td>
            <td class="py-2 px-2 text-center font-mono font-semibold">$${Number(r.cost_per_1m_usd ?? ((r.cost_per_1k_usd || 0) * 1000)).toFixed(2)}</td>
        </tr>
    `).join('');
}

function getHeatClass(pct) {
    if (pct >= 85) return 'heat-high';
    if (pct >= 72) return 'heat-good';
    if (pct >= 58) return 'heat-mid';
    return 'heat-low';
}

function formatCostCellHtml(item) {
    if (item.cost_unknown || item.price_known === false || item.cost_per_1m_usd == null) {
        return `<span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px] font-semibold">ціна не задана</span>`;
    }
    const val = Number(item.price_list_cost_per_1m_usd ?? item.cost_per_1m_usd ?? 0).toFixed(2);
    const freeBadge = item.is_free_tier
        ? `<div class="text-[9px] text-emerald-700 font-normal">безкоштовний тариф</div>`
        : '';
    return `<div class="font-mono font-semibold text-slate-800">$${val}</div>${freeBadge}`;
}

function renderAnalyticsCharts(data) {
    const byModel = data.by_model || [];
    const byPrompt = data.by_prompt || [];
    const paretoLat = data.pareto_fronts?.quality_vs_latency || [];

    const ctx1 = document.getElementById('chartModelsBar').getContext('2d');
    if (APP_STATE.charts.modelsBar) APP_STATE.charts.modelsBar.destroy();
    APP_STATE.charts.modelsBar = new Chart(ctx1, {
        type: 'bar',
        data: {
            labels: byModel.map(m => `${m.model_name} (n=${m.count})`),
            datasets: [
                { label: 'Композитна якість Q_det (%)', data: byModel.map(m => m.integral_efficiency_pct), backgroundColor: '#2563eb' },
                { label: 'Повнота FC (%)', data: byModel.map(m => m.fact_coverage_pct), backgroundColor: '#10b981' },
                { label: 'Схожість SS (%)', data: byModel.map(m => m.semantic_similarity_pct), backgroundColor: '#6366f1' },
                { label: 'Автоматизація AR (%)', data: byModel.map(m => m.automation_rate_pct), backgroundColor: '#f59e0b' },
            ]
        },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100 } } }
    });

    const ctx2 = document.getElementById('chartNistRadar').getContext('2d');
    if (APP_STATE.charts.nistRadar) APP_STATE.charts.nistRadar.destroy();
    APP_STATE.charts.nistRadar = new Chart(ctx2, {
        type: 'radar',
        data: {
            labels: ['Релевантність', 'Факт. точність', 'Повнота', 'Зрозумілість', 'Політика/Формат'],
            datasets: byModel.map((m, i) => {
                const col = MODEL_COLORS[i % MODEL_COLORS.length];
                return {
                    label: m.model_name,
                    data: [m.relevance_score, m.factuality_score, m.completeness_score, m.clarity_score, m.policy_adherence_score ?? m.format_score],
                    borderColor: col.border,
                    backgroundColor: col.bg.replace('0.78', '0.15'),
                };
            })
        },
        options: { responsive: true, maintainAspectRatio: false, scales: { r: { min: 1, max: 5 } } }
    });

    const ctx3 = document.getElementById('chartPromptsLine').getContext('2d');
    if (APP_STATE.charts.promptsLine) APP_STATE.charts.promptsLine.destroy();
    APP_STATE.charts.promptsLine = new Chart(ctx3, {
        type: 'bar',
        data: {
            labels: byPrompt.map(p => `${p.prompt_code} (n=${p.count})`),
            datasets: [
                { label: 'Композитна якість Q_det (%)', data: byPrompt.map(p => p.integral_efficiency_pct), backgroundColor: '#4f46e5' },
                { label: 'Повнота FC (%)', data: byPrompt.map(p => p.fact_coverage_pct), backgroundColor: '#10b981' },
                { label: 'Формат FMT (%)', data: byPrompt.map(p => p.format_compliance_pct), backgroundColor: '#0ea5e9' },
            ]
        },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100 } } }
    });

    const ctx4 = document.getElementById('chartLatencyVsQuality').getContext('2d');
    if (APP_STATE.charts.latencyScatter) APP_STATE.charts.latencyScatter.destroy();

    const paretoIds = new Set(paretoLat.filter(p => p.pareto_optimal).map(p => p.model_id));
    const scatterDatasets = byModel.map((m, i) => {
        const col = MODEL_COLORS[i % MODEL_COLORS.length];
        const isPareto = paretoIds.has(m.model_id);
        return {
            label: `${m.model_name}${isPareto ? ' (Парето-опт.)' : ''}`,
            data: [{ x: m.latency_mean_ms, y: m.integral_efficiency_pct, fc: m.fact_coverage_pct, n: m.count }],
            backgroundColor: col.bg,
            borderColor: isPareto ? '#0f172a' : col.border,
            borderWidth: isPareto ? 3 : 1.5,
            pointRadius: isPareto ? 8 : 6,
            pointHoverRadius: 10,
        };
    });

    APP_STATE.charts.latencyScatter = new Chart(ctx4, {
        type: 'scatter',
        data: { datasets: scatterDatasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    title: { display: true, text: 'Середня затримка відповіді (мс, менше = краще)' },
                    beginAtZero: true,
                },
                y: {
                    title: { display: true, text: 'Композитна якість Q_det (%, більше = краще)' },
                    min: 0,
                    max: 100,
                }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const raw = ctx.raw || {};
                            return `${ctx.dataset.label}: Q_det=${raw.y}%, FC=${raw.fc}%, Час=${raw.x} мс (n=${raw.n})`;
                        }
                    }
                }
            }
        }
    });
}

function renderModelsComparisonTable(byModel) {
    document.getElementById('tableModelsBody').innerHTML = byModel.map(m => {
        const qLow = m.efficiency_ci95?.[0] ?? m.stats?.integral_efficiency_pct?.ci95_low ?? m.integral_efficiency_pct;
        const qHigh = m.efficiency_ci95?.[1] ?? m.stats?.integral_efficiency_pct?.ci95_high ?? m.integral_efficiency_pct;
        const fcLow = m.fact_coverage_ci95?.[0] ?? m.stats?.fact_coverage_pct?.ci95_low ?? m.fact_coverage_pct;
        const fcHigh = m.fact_coverage_ci95?.[1] ?? m.stats?.fact_coverage_pct?.ci95_high ?? m.fact_coverage_pct;
        const lowNBadge = m.insufficient_sample
            ? `<span class="ml-1 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-bold" title="Мала вибірка (n < 6)">n&lt;6</span>`
            : '';
        return `
            <tr class="hover:bg-slate-50">
                <td class="py-2.5 px-3 font-bold text-slate-900">${escapeHtml(m.model_name)}${lowNBadge}</td>
                <td class="py-2.5 px-2 text-center font-mono font-semibold text-slate-700">${m.count}</td>
                <td class="py-2.5 px-2 text-center">
                    <span class="px-2 py-0.5 rounded ${getHeatClass(m.integral_efficiency_pct)}">${m.integral_efficiency_pct}%</span>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">[${qLow}; ${qHigh}]</div>
                </td>
                <td class="py-2.5 px-2 text-center">
                    <span class="font-semibold text-emerald-700">${m.fact_coverage_pct}%</span>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">[${fcLow}; ${fcHigh}]</div>
                </td>
                <td class="py-2.5 px-2 text-center">${m.semantic_similarity_pct}%</td>
                <td class="py-2.5 px-2 text-center">${m.format_compliance_pct}%</td>
                <td class="py-2.5 px-2 text-center ${m.hallucination_rate_pct > 0 ? 'text-rose-700 font-bold' : 'text-emerald-700'}">${m.hallucination_rate_pct ?? 0}%</td>
                <td class="py-2.5 px-2 text-center font-semibold">${m.avg_nist_score_5 ?? '-'} <span class="text-[10px] text-slate-400">(${m.nist_composite_pct}%)</span></td>
                <td class="py-2.5 px-2 text-center font-mono text-[11px]">${m.latency_mean_ms} / ${m.latency_median_ms} <span class="text-slate-400">(${m.latency_p95_ms})</span></td>
                <td class="py-2.5 px-2 text-center font-bold text-emerald-700">${m.automation_rate_pct}%</td>
                <td class="py-2.5 px-2 text-center">${formatCostCellHtml(m)}</td>
            </tr>
        `;
    }).join('');
}

function renderPromptsComparisonTable(byPrompt) {
    document.getElementById('tablePromptsBody').innerHTML = byPrompt.map(p => {
        const qLow = p.efficiency_ci95?.[0] ?? p.stats?.integral_efficiency_pct?.ci95_low ?? p.integral_efficiency_pct;
        const qHigh = p.efficiency_ci95?.[1] ?? p.stats?.integral_efficiency_pct?.ci95_high ?? p.integral_efficiency_pct;
        const fcLow = p.fact_coverage_ci95?.[0] ?? p.stats?.fact_coverage_pct?.ci95_low ?? p.fact_coverage_pct;
        const fcHigh = p.fact_coverage_ci95?.[1] ?? p.stats?.fact_coverage_pct?.ci95_high ?? p.fact_coverage_pct;
        const escMat = p.escalation_matrix || {};
        const lowNBadge = p.insufficient_sample
            ? `<span class="ml-1 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-bold">n&lt;6</span>`
            : '';
        return `
            <tr class="hover:bg-slate-50">
                <td class="py-2.5 px-3 font-bold text-slate-900">${escapeHtml(p.prompt_name_uk)}${lowNBadge}</td>
                <td class="py-2.5 px-2 text-center font-mono font-semibold text-slate-700">${p.count}</td>
                <td class="py-2.5 px-2 text-center">
                    <span class="px-2 py-0.5 rounded ${getHeatClass(p.integral_efficiency_pct)}">${p.integral_efficiency_pct}%</span>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">[${qLow}; ${qHigh}]</div>
                </td>
                <td class="py-2.5 px-2 text-center">
                    <span class="font-semibold text-emerald-700">${p.fact_coverage_pct}%</span>
                    <div class="text-[10px] font-mono text-slate-500 mt-0.5">[${fcLow}; ${fcHigh}]</div>
                </td>
                <td class="py-2.5 px-2 text-center">${p.format_compliance_pct}%</td>
                <td class="py-2.5 px-2 text-center ${p.hallucination_rate_pct > 0 ? 'text-rose-700 font-bold' : 'text-emerald-700'}">${p.hallucination_rate_pct ?? 0}%</td>
                <td class="py-2.5 px-2 text-center font-semibold text-indigo-800">${escMat.f1_pct ?? p.escalation_accuracy_pct}% <span class="text-[10px] text-rose-700">(FN: ${escMat.fn_rate_pct ?? 0}%)</span></td>
                <td class="py-2.5 px-2 text-center font-mono text-[11px]">${p.latency_mean_ms} / ${p.latency_median_ms} мс</td>
                <td class="py-2.5 px-2 text-center font-mono">${p.avg_input_tokens} / ${p.avg_output_tokens}</td>
                <td class="py-2.5 px-2 text-center">${formatCostCellHtml(p)}</td>
            </tr>
        `;
    }).join('');
}

function renderHeatmaps(data) {
    const models = (data.by_model || []).map(m => ({ id: m.model_id, name: m.model_name }));
    const prompts = (data.by_prompt || []).map(p => ({ id: p.prompt_code, name: p.prompt_code }));
    const categories = (data.by_category || []).map(c => ({ id: c.category_code, name: c.category_name_uk }));
    document.getElementById('matrixModelPromptContainer').innerHTML =
        matrixTableHtml(models, prompts, data.matrix_model_prompt || {});
    document.getElementById('matrixModelCategoryContainer').innerHTML =
        matrixTableHtml(models, categories, data.matrix_model_category || {});
}

function renderStatisticalTestsAndEscalation(data) {
    const statsBox = document.getElementById('statsTestsContainer');
    const escBox = document.getElementById('escalationAndSensitivityContainer');
    if (!statsBox || !escBox) return;

    const stModels = data.statistical_tests?.models_fact_coverage || {};
    const stPrompts = data.statistical_tests?.prompts_fact_coverage || {};

    const formatPairwiseRows = (compObj, titleUk) => {
        const pairs = compObj.pairwise || [];
        const omnibus = compObj.omnibus_test || compObj.omnibus;
        let html = `<div class="font-bold text-slate-800">${titleUk}:</div>`;
        if (omnibus) {
            const kCond = omnibus.k_conditions || (omnibus.df != null ? omnibus.df + 1 : 0);
            const nBlocks = omnibus.n_blocks || omnibus.n_paired || 0;
            if (nBlocks < 6 || omnibus.applicable === false) {
                html += `<div class="p-2 rounded bg-amber-50 border border-amber-200 text-[11px] text-amber-900">
                    <b>Критерій Фрідмана (${kCond} умов, n=${nBlocks} блоків):</b> недостатньо даних для критерію (потрібно n &ge; 6).
                </div>`;
            } else {
                const warnLine = omnibus.warning_uk
                    ? `<div class="text-amber-800 mt-0.5">${escapeHtml(omnibus.warning_uk)}</div>`
                    : '';
                html += `<div class="p-2 rounded bg-indigo-50 border border-indigo-200 text-[11px] text-indigo-950">
                    <b>Критерій Фрідмана (${kCond} умов, n=${nBlocks} спільних запитів):</b>
                    χ² = ${omnibus.statistic ?? '-'}, p = ${omnibus.p_value ?? '-'}
                    (${(nBlocks >= 6 && omnibus.significant_005) ? '<b class="text-emerald-700">статистично значуща різниця p &lt; 0.05</b>' : 'немає значущої різниці за α=0.05'})
                    ${warnLine}
                </div>`;
            }
        }
        if (compObj.status === 'insufficient_data') {
            html += `<div class="text-amber-900 text-[11px] bg-amber-50 p-2 rounded border border-amber-200">${escapeHtml(compObj.message_uk || 'недостатньо даних для критерію (потрібно n >= 6 спільних запитів).')}</div>`;
        }
        if (!pairs.length) {
            const reason = compObj.message_uk || 'Недостатньо парних умов для статистичного критерію (потрібно n >= 6).';
            html += `<div class="text-slate-500 text-xs italic bg-slate-50 p-2 rounded border border-slate-200">${escapeHtml(reason)}</div>`;
            return html;
        }
        html += `<div class="overflow-x-auto"><table class="w-full text-[11px] border-collapse border border-slate-200">
            <thead><tr class="bg-slate-100 text-slate-700">
                <th class="p-1.5 border text-left">Пара (A vs B)</th>
                <th class="p-1.5 border text-center">n пар</th>
                <th class="p-1.5 border text-center">Δ Mean (95% ДІ)</th>
                <th class="p-1.5 border text-center">p (Wilcoxon)</th>
                <th class="p-1.5 border text-center">p_Holm</th>
                <th class="p-1.5 border text-center">Cliff's δ</th>
            </tr></thead><tbody>`;
        pairs.forEach(p => {
            const condA = p.condition_a || p.group_a || '-';
            const condB = p.condition_b || p.group_b || '-';
            const nPairs = p.n_pairs ?? p.n_paired ?? 0;
            const ciStr = `[${p.diff_ci95_low ?? 0}; ${p.diff_ci95_high ?? 0}]%`;
            const isSig = nPairs >= 6 && p.significant_005 && p.p_holm != null && p.p_holm < 0.05;
            const pRaw = nPairs >= 6 ? (p.p_value_raw ?? p.p_raw ?? '-') : 'н/д (n<6)';
            const pHolm = nPairs >= 6 ? (p.p_value_holm ?? p.p_holm ?? '-') : 'недостатньо даних';
            const effMag = p.effect_size_label_uk || p.effect_magnitude_uk || '-';
            const sigCls = isSig ? 'bg-emerald-50 font-bold text-emerald-800' : '';
            html += `<tr class="${sigCls}">
                <td class="p-1.5 border font-mono">${escapeHtml(condA)} vs ${escapeHtml(condB)}</td>
                <td class="p-1.5 border text-center font-mono">${nPairs}</td>
                <td class="p-1.5 border text-center font-mono">${p.mean_diff > 0 ? '+' : ''}${p.mean_diff}% <span class="text-[10px] text-slate-500">${ciStr}</span></td>
                <td class="p-1.5 border text-center font-mono">${pRaw}</td>
                <td class="p-1.5 border text-center font-mono">${pHolm} ${isSig ? '(статистично значуща різниця)' : ''}</td>
                <td class="p-1.5 border text-center">${p.cliffs_delta} (${escapeHtml(effMag)})</td>
            </tr>`;
        });
        html += `</tbody></table></div>`;
        return html;
    };

    const corrObj = data.metrics_correlation_matrix || {};
    const corrMetrics = corrObj.metrics || [];
    const corrMat = corrObj.matrix || {};
    let corrHtml = '';
    if (corrMetrics.length) {
        corrHtml = `<div class="pt-2 border-t border-slate-200 space-y-1.5">
            <div class="font-bold text-slate-800">Матриця рангових кореляцій Спірмена (ρ) між метриками (n=${corrObj.n_units ?? 0}):</div>
            <div class="overflow-x-auto"><table class="w-full text-[10px] border-collapse border border-slate-200 font-mono">
                <thead><tr class="bg-slate-100 text-slate-700">
                    <th class="p-1 border text-left">Метрика</th>
                    ${corrMetrics.map(m => `<th class="p-1 border text-center">${escapeHtml(m.key)}</th>`).join('')}
                </tr></thead><tbody>
                ${corrMetrics.map(m1 => `<tr>
                    <td class="p-1 border font-semibold text-slate-800">${escapeHtml(m1.label_uk)}</td>
                    ${corrMetrics.map(m2 => {
                        const cell = corrMat[m1.key]?.[m2.key];
                        const rho = cell?.rho;
                        return `<td class="p-1 border text-center ${m1.key === m2.key ? 'bg-slate-100 font-bold' : ''}">${rho != null ? rho : '-'}</td>`;
                    }).join('')}
                </tr>`).join('')}
                </tbody></table></div>
        </div>`;
    }

    statsBox.innerHTML = `
        <div class="space-y-2">${formatPairwiseRows(stModels, 'Парні порівняння моделей LLM (за повнотою фактів FC)')}</div>
        <div class="space-y-2 pt-2 border-t border-slate-200">${formatPairwiseRows(stPrompts, 'Парні порівняння стратегій промптів P1-P5 (за FC)')}</div>
        ${corrHtml}
    `;

    const escMat = data.overall?.confusion_matrix || data.overall?.escalation_matrix || {};
    const sens = data.weight_sensitivity || {};
    const stab = data.repeat_stability || data.overall?.stability_info || {};
    const rDiag = data.rubric_diagnostics || {};
    const jVal = data.judge_validation || {};

    const sensHtml = sens.available
        ? `<p class="text-[11px] text-slate-700">
            Перевірено комбінацій ваг (w_FC, w_SS) та штрафів за галюцинації (5..30 в.п.): <b>${sens.grid_points_tested ?? sens.grid_size ?? 0}</b> |
            Середня рангова кореляція Кендалла τ: <b>${sens.mean_kendall_tau ?? '-'}</b> (мін. τ = ${sens.min_kendall_tau ?? '-'}) |
            ${sens.top1_model_name ? ('Топ-1 модель: <b>' + escapeHtml(sens.top1_model_name) + '</b> | ') : ''}
            Стійкість лідера: <b>${sens.top1_model_stability_pct ?? sens.top1_stability_pct ?? 100}%</b>.
          </p>`
        : `<p class="text-[11px] text-slate-500 italic">${escapeHtml(sens.message_uk || 'Для аналізу чутливості ранжування моделей потрібно щонайменше 2 моделі у вибірці.')}</p>`;

    const stabHtml = stab.available
        ? `<p class="text-[11px] text-slate-700">
            Комірок із повторами (repeats >= 2): <b>${stab.multi_repeat_cells_count ?? stab.MultiRepeatPairs ?? 0}</b> |
            Середнє міжповторне СКВ (SD): <b>${stab.mean_within_sd ?? stab.mean_inter_repeat_sd ?? '-'}%</b> |
            Коефіцієнт кореляції ICC: <b>${stab.icc ?? '-'}</b> |
            Узгодженість ескалації: <b>${stab.escalation_consistency_pct ?? '-'}%</b>
          </p>`
        : `<p class="text-[11px] text-slate-500 italic">${escapeHtml(stab.label_uk || stab.reason_uk || 'Для оцінки міжповторної дисперсії запустіть експеримент із параметром повторів >= 2.')}</p>`;

    const buckets = rDiag.distribution_buckets || {};
    const distEntries = Object.entries(buckets).map(([k, v]) => `<span class="px-1.5 py-0.5 rounded bg-white border border-slate-200 font-mono text-[10px]">${k}: <b>${v}</b></span>`).join(' ');
    const rubricDistHtml = `
        <div class="p-3 rounded-lg bg-indigo-50/40 border border-indigo-200 space-y-1.5">
            <div class="flex flex-wrap items-center justify-between gap-1">
                <span class="font-bold text-indigo-950">Розподіл оцінок судді та індикатор ефекту стелі:</span>
                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${rDiag.ceiling_effect_detected ? 'bg-rose-100 text-rose-800' : 'bg-emerald-100 text-emerald-800'}">
                    ${rDiag.ceiling_effect_detected ? `Ефект стелі (${rDiag.top_10pct_share_pct}% у топ-10%)` : `Без ефекту стелі (${rDiag.top_10pct_share_pct ?? 0}% у топ-10%)`}
                </span>
            </div>
            <div class="flex flex-wrap gap-1.5">${distEntries || '<span class="text-slate-500 text-[11px]">Немає оцінок судді</span>'}</div>
        </div>
    `;

    const jValCritRows = (jVal.by_criterion || []).map(c =>
        `<span class="px-1.5 py-0.5 rounded bg-white border border-slate-200 text-[10px]">${escapeHtml(c.label_uk)}: κ<sub>w</sub>=<b>${c.weighted_kappa ?? '-'}</b>, ρ=<b>${c.spearman_rho ?? '-'}</b></span>`
    ).join(' ');
    const judgeValidationHtml = `
        <div class="p-3 rounded-lg bg-emerald-50/50 border border-emerald-200 space-y-1.5">
            <div class="flex flex-wrap items-center justify-between gap-1">
                <span class="font-bold text-emerald-950">Валідація судді та метрик на ручній розмітці (n=${jVal.manual_scored_count ?? 0}):</span>
                <span class="text-[10px] font-mono text-emerald-900">κ<sub>w</sub>=${jVal.mean_weighted_kappa ?? '-'} | ρ(суддя, людина)=${jVal.overall_spearman_rho ?? '-'} | ρ(FC, ручна повнота)=${jVal.fc_vs_human_completeness_rho ?? '-'}</span>
            </div>
            <p class="text-[11px] text-slate-700">${escapeHtml(jVal.interpretation_uk || '')}</p>
            ${jValCritRows ? `<div class="flex flex-wrap gap-1">${jValCritRows}</div>` : ''}
        </div>
    `;

    escBox.innerHTML = `
        <div class="p-3 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
            <div class="font-bold text-slate-800">Матриця плутанини шлюзу ескалації (Confusion Matrix):</div>
            <div class="grid grid-cols-4 gap-2 text-center font-mono">
                <div class="p-2 rounded bg-emerald-50 border border-emerald-200">
                    <div class="text-[10px] text-slate-500">TP (Вірна ескал.)</div>
                    <div class="font-bold text-emerald-800 text-sm">${escMat.tp ?? 0}</div>
                </div>
                <div class="p-2 rounded bg-emerald-50 border border-emerald-200">
                    <div class="text-[10px] text-slate-500">TN (Вірна автом.)</div>
                    <div class="font-bold text-emerald-800 text-sm">${escMat.tn ?? 0}</div>
                </div>
                <div class="p-2 rounded bg-amber-50 border border-amber-200">
                    <div class="text-[10px] text-slate-500">FP (Хибна тривога)</div>
                    <div class="font-bold text-amber-800 text-sm">${escMat.fp ?? 0}</div>
                </div>
                <div class="p-2 rounded bg-rose-50 border border-rose-300">
                    <div class="text-[10px] text-rose-700 font-bold">FN (Пропуск ризику)</div>
                    <div class="font-bold text-rose-800 text-sm">${escMat.fn ?? 0}</div>
                </div>
            </div>
            <div class="flex flex-wrap justify-between text-[11px] pt-1">
                <span>Precision: <b>${escMat.precision_pct ?? 0}%</b></span>
                <span>Recall: <b>${escMat.recall_pct ?? 0}%</b></span>
                <span>F1-міра: <b>${escMat.f1_pct ?? 0}%</b></span>
                <span class="text-rose-700 font-bold">Критичний FN-rate: ${escMat.critical_fn_rate_pct ?? escMat.fn_rate_pct ?? 0}%</span>
            </div>
        </div>

        <div class="p-3 rounded-lg bg-blue-50/50 border border-blue-200 space-y-1">
            <div class="font-bold text-blue-950">Аналіз чутливості композитного індексу Q_det до ваг і штрафів:</div>
            ${sensHtml}
        </div>

        ${rubricDistHtml}
        ${judgeValidationHtml}

        <div class="p-3 rounded-lg bg-slate-50 border border-slate-200 space-y-1">
            <div class="font-bold text-slate-800">Міжповторна стабільність генерації:</div>
            ${stabHtml}
        </div>
    `;
}
