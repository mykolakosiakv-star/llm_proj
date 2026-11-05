function onExpDomainSelectChange() {
    const domSel = document.getElementById('expDomainSelect');
    const descEl = document.getElementById('expDomainRiskDesc');
    if (!domSel || !descEl) return;
    const domId = domSel.value || 'fintech';
    const domInfo = APP_STATE.metadata?.domains?.[domId];
    if (domInfo) {
        descEl.textContent = `${domInfo.description_uk} ${domInfo.risk_profile_uk || ''}`;
    } else {
        descEl.textContent = 'Комплексне тестування одночасно за всіма 4 предметними сферами служби підтримки.';
    }
}

function populateControls() {
    const meta = APP_STATE.metadata;
    if (!meta) return;

    const enabledModels = getEnabledModelsList();

    // Вибір типового запиту у пісочниці з фільтром за сферою
    populateSandboxQueryPresets();
    populateAssistantControls();
    populateKbControls();

    // Вибір моделі (показуємо тільки увімкнені моделі) та промпту на вкладці пісочниці
    const modelSel = document.getElementById('sandboxModelSelect');
    if (modelSel) {
        if (enabledModels.length === 0) {
            modelSel.innerHTML = `<option value="">У вкладці налаштувань не обрано моделей</option>`;
        } else {
            const prevVal = modelSel.value;
            modelSel.innerHTML = enabledModels.map(m => {
                const routeTag = (m.can_openrouter && m.route === 'openrouter') ? ' [OpenRouter]' : '';
                return `<option value="${m.id}">${escapeHtml(m.name)}${routeTag} (${escapeHtml(m.status_label || 'Працює')})</option>`;
            }).join('');
            if (prevVal && enabledModels.some(m => m.id === prevVal)) {
                modelSel.value = prevVal;
            }
        }
    }

    const promptSel = document.getElementById('sandboxPromptSelect');
    const prevPrompt = promptSel.value || 'P3';
    promptSel.innerHTML = APP_STATE.prompts.map(p =>
        `<option value="${p.code}" ${p.code === prevPrompt ? 'selected' : ''}>${escapeHtml(p.name_uk)}</option>`
    ).join('');
    updateSandboxPromptPreview();

    // Селектор незалежного LLM-судді для пісочниці (тільки увімкнені моделі з інформативними лімітами)
    const sbJudgeSel = document.getElementById('sandboxJudgeSelect');
    if (sbJudgeSel) {
        const prevSbJudge = sbJudgeSel.value || '';
        sbJudgeSel.innerHTML = buildJudgeSelectOptionsHtml(enabledModels, prevSbJudge, true);
    }

    // Чекбокси для пакетного експерименту (зберігаємо поточний вибір користувача)
    const expModelsBox = document.getElementById('expModelsCheckboxes');
    if (expModelsBox) {
        if (enabledModels.length === 0) {
            expModelsBox.innerHTML = `
                <div class="p-4 text-center text-amber-900 bg-amber-50 rounded-lg border border-amber-200 text-xs space-y-2">
                    <div class="font-bold">Не обрано жодної активної моделі</div>
                    <div class="text-[11px] text-slate-600">Для запуску експерименту необхідно увімкнути принаймні одну модель у налаштуваннях.</div>
                    <button type="button" onclick="switchTab('settings')" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded font-semibold text-xs transition">
                        Перейти до налаштувань моделей
                    </button>
                </div>
            `;
        } else {
            const existingInputs = Array.from(document.querySelectorAll('input[name="expModel"]'));
            const isFirstRender = existingInputs.length === 0;
            const currentlyChecked = new Set(
                existingInputs.filter(el => el.checked).map(el => el.value)
            );

            expModelsBox.innerHTML = enabledModels.map(m => {
                const isChecked = isFirstRender ? true : currentlyChecked.has(m.id);
                const statusBadge = getStatusBadgeHtml(m.status_code, m.status_label);
                const inCtx = formatTokenNum(m.context_in || m.input_limit);
                const pIn = (m.price_known && m.cost_per_1m_input_usd != null) ? `$${m.cost_per_1m_input_usd}/1M` : 'ціна не задана';

                return `
                    <label class="flex items-center justify-between gap-2 cursor-pointer p-2 rounded-lg hover:bg-white border border-transparent hover:border-slate-300 bg-white/70 transition">
                        <div class="flex items-center gap-2.5">
                            <input type="checkbox" name="expModel" value="${m.id}" onchange="updateBatchExperimentEstimate()" ${isChecked ? 'checked' : ''} class="w-4 h-4 rounded text-blue-600 focus:ring-blue-500 cursor-pointer">
                            <div>
                                <div class="font-semibold text-slate-800 text-xs flex items-center gap-1.5">
                                    <span>${escapeHtml(m.name)}</span>
                                    <span class="text-[10px] text-slate-400 font-mono">(${escapeHtml(m.id)})</span>
                                </div>
                                <div class="text-[10px] text-slate-500 font-mono">Контекст: ${inCtx} т. | Вх: ${pIn}</div>
                            </div>
                        </div>
                        ${statusBadge}
                    </label>
                `;
            }).join('');
        }
    }

    // Селектор незалежного LLM-судді для серійного експерименту (тільки увімкнені моделі)
    const judgeSel = document.getElementById('expJudgeModelSelect');
    if (judgeSel) {
        const prevJudge = judgeSel.value || '';
        judgeSel.innerHTML = buildJudgeSelectOptionsHtml(enabledModels, prevJudge, true);
    }

    // Селектор судді у модальному вікні дооцінювання
    const judgeRunSel = document.getElementById('judgeRunModelSelect');
    if (judgeRunSel) {
        const prevRunJudge = judgeRunSel.value || (enabledModels[0]?.id || '');
        judgeRunSel.innerHTML = buildJudgeSelectOptionsHtml(enabledModels, prevRunJudge, false);
    }

    const expPromptsBox = document.getElementById('expPromptsCheckboxes');
    if (expPromptsBox) {
        const existingPromptInputs = Array.from(document.querySelectorAll('input[name="expPrompt"]'));
        const isFirstPromptsRender = existingPromptInputs.length === 0;
        const checkedPrompts = new Set(existingPromptInputs.filter(el => el.checked).map(el => el.value));

        expPromptsBox.innerHTML = APP_STATE.prompts.map(p => {
            const isChecked = isFirstPromptsRender ? (p.code === 'P1' || p.code === 'P3') : checkedPrompts.has(p.code);
            return `
                <label class="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" name="expPrompt" value="${p.code}" onchange="updateBatchExperimentEstimate()" ${isChecked ? 'checked' : ''} class="rounded text-blue-600">
                    <span class="font-medium text-slate-800">${escapeHtml(p.name_uk)}</span>
                </label>
            `;
        }).join('');
    }

    const expCatsBox = document.getElementById('expCategoriesCheckboxes');
    if (expCatsBox) {
        const existingCatInputs = Array.from(document.querySelectorAll('input[name="expCategory"]'));
        const isFirstCatsRender = existingCatInputs.length === 0;
        const checkedCats = new Set(existingCatInputs.filter(el => el.checked).map(el => el.value));

        expCatsBox.innerHTML = Object.values(meta.categories).map(c => {
            const isChecked = isFirstCatsRender ? true : checkedCats.has(c.code);
            return `
                <label class="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" name="expCategory" value="${c.code}" onchange="updateBatchExperimentEstimate()" ${isChecked ? 'checked' : ''} class="rounded text-blue-600">
                    <span class="font-medium text-slate-800">${escapeHtml(c.name_uk)}</span>
                </label>
            `;
        }).join('');
    }

    // Фільтри журналу замірів (зберігаємо обраний фільтр)
    const logModEl = document.getElementById('logFilterModel');
    if (logModEl) {
        const prevLogMod = logModEl.value || 'all';
        logModEl.innerHTML = `<option value="all">Усі моделі</option>` +
            meta.models.map(m => `<option value="${m.id}" ${m.id === prevLogMod ? 'selected' : ''}>${escapeHtml(m.name)}</option>`).join('');
    }
    const logPrEl = document.getElementById('logFilterPrompt');
    if (logPrEl) {
        const prevLogPr = logPrEl.value || 'all';
        logPrEl.innerHTML = `<option value="all">Усі промпти</option>` +
            APP_STATE.prompts.map(p => `<option value="${p.code}" ${p.code === prevLogPr ? 'selected' : ''}>${escapeHtml(p.code)}</option>`).join('');
    }
}

// Готові набори параметрів під кожну гіпотезу: що змінюється, те й порівнюється
const EXPERIMENT_PRESETS = {
    h1: {
        title: 'H1: залежність від сфери',
        domain: 'all', prompts: ['P3'], contexts: ['retrieved_context'], models: 3,
        note: 'Усі сфери, однакова кількість запитів у кожній, пошук по базі знань. Взято до трьох моделей, що відповідають.',
    },
    h2: {
        title: 'H2: вплив промпту й контексту',
        domain: null, prompts: ['P1', 'P3', 'P5'], contexts: ['no_context', 'retrieved_context', 'gold_context'], models: 2,
        note: 'Одна сфера, три стратегії промпту, усі три режими контексту. Взято дві моделі, що відповідають.',
    },
    h3: {
        title: 'H3: чи досить дешевої моделі',
        domain: 'all', prompts: ['P3'], contexts: ['retrieved_context'], models: 3,
        note: 'Усі сфери. Бажано, щоб серед моделей були дешева й сильніша, і щоб для них була задана ціна. Змініть вибір моделей нижче.',
    },
};

function largestDomainCode() {
    const counts = {};
    (APP_STATE.queries || []).forEach(q => { counts[q.domain || 'fintech'] = (counts[q.domain || 'fintech'] || 0) + 1; });
    return Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || 'fintech';
}

function setCheckedByValue(inputName, values) {
    document.querySelectorAll(`input[name="${inputName}"]`).forEach(el => { el.checked = values.includes(el.value); });
}

function applyExperimentPreset(code) {
    const preset = EXPERIMENT_PRESETS[code];
    if (!preset) return;
    document.getElementById('expTitleInput').value = preset.title;
    document.getElementById('expDomainSelect').value = preset.domain || largestDomainCode();
    document.getElementById('expEqualDomainSample').checked = true;
    document.getElementById('expMaxQueriesInput').value = 12;
    document.getElementById('expRepeatsInput').value = 1;
    document.querySelectorAll('input[name="expCategory"]').forEach(el => { el.checked = true; });
    setCheckedByValue('expPrompt', preset.prompts);
    setCheckedByValue('expContextMode', preset.contexts);

    // Для порівняння потрібні кілька моделей, але не всі: зайві виклики коштують часу й денної квоти
    const working = new Set((APP_STATE.metadata?.models || []).filter(m => m.status_code === 'ok').map(m => m.id));
    const boxes = Array.from(document.querySelectorAll('input[name="expModel"]')).filter(el => working.has(el.value));
    const preferred = boxes.filter(el => el.checked).concat(boxes.filter(el => !el.checked));
    const picked = new Set(preferred.slice(0, preset.models).map(el => el.value));
    document.querySelectorAll('input[name="expModel"]').forEach(el => { el.checked = picked.has(el.value); });
    document.getElementById('expPresetNote').textContent = preset.note;
    onExpDomainSelectChange();
    updateBatchExperimentEstimate();
}

function toggleAllExpModelsCheckboxes(checked) {
    document.querySelectorAll('input[name="expModel"]').forEach(cb => {
        cb.checked = checked;
    });
}

// ============================================================================
// СЕРІЙНІ ЕКСПЕРИМЕНТИ ТА ДЕТАЛІ ЗАМІРУ
// ============================================================================

async function startNewBatchExperiment() {
    const title = document.getElementById('expTitleInput').value.trim() || 'Серія експериментів';
    const domain = document.getElementById('expDomainSelect')?.value || 'fintech';
    const modelIds = Array.from(document.querySelectorAll('input[name="expModel"]:checked')).map(el => el.value);
    const promptCodes = Array.from(document.querySelectorAll('input[name="expPrompt"]:checked')).map(el => el.value);
    const categories = Array.from(document.querySelectorAll('input[name="expCategory"]:checked')).map(el => el.value);
    const contextModes = Array.from(document.querySelectorAll('input[name="expContextMode"]:checked')).map(el => el.value);
    const topK = Number(document.getElementById('expTopKInput')?.value) || 3;
    const maxQueries = Number(document.getElementById('expMaxQueriesInput').value) || 2;
    const repeats = Number(document.getElementById('expRepeatsInput').value) || 1;
    const judgeModelId = document.getElementById('expJudgeModelSelect')?.value || null;
    const equalDomainSample = Boolean(document.getElementById('expEqualDomainSample')?.checked);
    const allowSelfJudge = Boolean(document.getElementById('expAllowSelfJudge')?.checked);

    const errBox = document.getElementById('expErrorBox');
    errBox.classList.add('hidden');

    if (!modelIds.length || !promptCodes.length || !categories.length) {
        errBox.textContent = 'Оберіть щонайменше одну модель, один промпт та одну категорію запитів.';
        errBox.classList.remove('hidden');
        return;
    }

    const effectiveModes = contextModes.length ? contextModes : ['gold_context'];

    const btn = document.getElementById('startBatchExpBtn');
    btn.disabled = true;
    btn.textContent = 'Запуск серії...';

    try {
        const resp = await fetch('/api/experiments/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                description: `Сфера: ${domain}, Режими контексту: ${effectiveModes.join(',')}, Моделей: ${modelIds.length}, Промптів: ${promptCodes.length}`,
                domain,
                equal_domain_sample: equalDomainSample,
                context_mode: effectiveModes[0],
                context_modes: effectiveModes,
                top_k: topK,
                model_ids: modelIds,
                prompt_codes: promptCodes,
                categories,
                max_queries_per_category: maxQueries,
                repeats,
                judge_model_id: judgeModelId,
                allow_self_judge: allowSelfJudge,
            })
        });
        const data = await resp.json();
        await pollLogsAndStatus();

        if (!resp.ok) {
            errBox.textContent = data.detail || 'Помилка під час запуску експерименту';
            errBox.classList.remove('hidden');
            return;
        }

        const runsRes = await fetch('/api/experiments').then(r => r.json());
        APP_STATE.runs = runsRes.runs || [];

        const newRunId = data.run_id || (data.run && data.run.id);
        if (newRunId) {
            APP_STATE.currentRunId = newRunId;
            APP_STATE.selectedAnalyticsRunIds = [newRunId];
        }

        renderExperimentRunsList();
        if (!data.background && newRunId) {
            updateAnalyticsVisibility();
            switchTab('analytics');
        }
    } catch (err) {
        errBox.textContent = `Помилка: ${err.message}`;
        errBox.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Запустити серію';
    }
}

function renderExperimentRunsList() {
    const container = document.getElementById('experimentRunsListContainer');
    const logRunSelect = document.getElementById('logFilterRunSelect');

    if (!APP_STATE.runs.length) {
        container.innerHTML = `<div class="p-4 text-center text-slate-400 bg-slate-50 rounded-lg border border-slate-200">
            Історія порожня. Ви ще не запускали серійних експериментів.
        </div>`;
        if (logRunSelect) logRunSelect.innerHTML = `<option value="">Немає серій</option>`;
        document.getElementById('experimentItemsLogBody').innerHTML = '';
        return;
    }

    if (logRunSelect) {
        logRunSelect.innerHTML = APP_STATE.runs.map(r =>
            `<option value="${r.id}" ${r.id === APP_STATE.currentRunId ? 'selected' : ''}>Серія #${r.id}: ${escapeHtml(r.title)} (${r.completed_items}/${r.total_items} замірів, ${r.status || 'completed'})</option>`
        ).join('');
    }

    container.innerHTML = APP_STATE.runs.map(r => {
        const isCurrent = r.id === APP_STATE.currentRunId;
        const modelsStr = (r.models || []).join(', ');
        const promptsStr = (r.prompts || []).join(', ');
        const statusBadges = getRunStatusBadgeHtml(r);
        const isIncomplete = (r.completed_items || 0) < (r.total_items || 0) || r.status !== 'completed';
        const resumeBtnHtml = isIncomplete
            ? `<button onclick="resumeExperimentRun(${r.id})" id="resumeRunBtn-${r.id}" title="Продовжити виконання серії з місця зупинки" class="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-semibold transition">
                Довиконати (${(r.total_items || 0) - (r.completed_items || 0)})
               </button>`
            : '';
        const judgeBtnHtml = (r.completed_items || 0) > 0
            ? `<button onclick="openJudgeRunModal(${r.id})" title="Оцінити або дооцінити збережені відповіді за допомогою ШІ-судді" class="px-2.5 py-1 rounded bg-indigo-100 hover:bg-indigo-200 text-indigo-800 border border-indigo-200 font-semibold transition">
                Дооцінити суддею
               </button>`
            : '';

        return `
            <div class="p-3.5 rounded-xl border ${isCurrent ? 'border-blue-500 bg-blue-50/40' : 'border-slate-200 bg-slate-50'} flex flex-col gap-2.5 transition">
                <div onclick="selectRunForItemsTable(${r.id})" class="cursor-pointer space-y-1.5">
                    <div class="font-bold text-slate-900 flex flex-wrap items-center gap-1.5">
                        <span class="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono text-xs">#${r.id}</span>
                        <span class="text-xs sm:text-sm">${escapeHtml(r.title)}</span>
                        ${statusBadges}
                        ${isCurrent ? '<span class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800 font-bold">Активна в таблиці</span>' : ''}
                    </div>
                    <div class="text-slate-600 text-[11px] leading-relaxed">
                        Дата: <span class="font-medium">${r.created_at}</span> |
                        Промпти: <b>${escapeHtml(promptsStr)}</b> |
                        Моделі: <span class="font-mono text-[11px] text-slate-700">${escapeHtml(modelsStr)}</span> |
                        Датасет: <code>${escapeHtml(r.dataset_version || '2.0')}</code>
                    </div>
                </div>
                <div class="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-200/80">
                    <div class="flex flex-wrap items-center gap-1.5">
                        ${resumeBtnHtml}
                        ${judgeBtnHtml}
                    </div>
                    <div class="flex flex-wrap items-center gap-1.5 ml-auto">
                        <button onclick="selectRunForItemsTable(${r.id})" class="px-2.5 py-1 rounded ${isCurrent ? 'bg-slate-800 text-white' : 'bg-white border border-slate-300 text-slate-700 hover:bg-slate-100'} font-semibold">
                            Заміри
                        </button>
                        <button onclick="openRunAnalytics(${r.id})" class="px-2.5 py-1 rounded bg-blue-600 text-white font-semibold hover:bg-blue-700">
                            Результати
                        </button>
                        <button onclick="promptDeleteExperimentRun(${r.id})" title="Видалити серію #${r.id}" class="px-2 py-1 rounded bg-rose-100 text-rose-700 hover:bg-rose-200 font-bold">
                            Видалити
                        </button>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    loadExperimentItemsLog();
}

async function resumeExperimentRun(runId) {
    const judgeModelId = document.getElementById('expJudgeModelSelect')?.value || null;
    const errBox = document.getElementById('expErrorBox');
    if (errBox) errBox.classList.add('hidden');

    const btn = document.getElementById(`resumeRunBtn-${runId}`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Довиконується...';
    }

    try {
        const resp = await fetch(`/api/experiments/${runId}/resume`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ judge_model_id: judgeModelId }),
        });
        const data = await resp.json();
        await pollLogsAndStatus();

        if (!resp.ok) {
            if (errBox) {
                errBox.textContent = data.detail || 'Помилка під час довиконання серії';
                errBox.classList.remove('hidden');
            }
            return;
        }

        const runsRes = await fetch('/api/experiments').then(r => r.json());
        APP_STATE.runs = runsRes.runs || [];
        APP_STATE.currentRunId = Number(runId);
        if (!APP_STATE.selectedAnalyticsRunIds.includes(Number(runId))) {
            APP_STATE.selectedAnalyticsRunIds = [Number(runId)];
        }
        renderExperimentRunsList();
        updateAnalyticsVisibility();
    } catch (err) {
        if (errBox) {
            errBox.textContent = `Помилка довиконання: ${err.message}`;
            errBox.classList.remove('hidden');
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Довиконати';
        }
    }
}

function openJudgeRunModal(runId) {
    const run = APP_STATE.runs.find(r => r.id === Number(runId));
    if (!run) return;
    APP_STATE.pendingJudgeRunId = Number(runId);

    const enabledModels = getEnabledModelsList();
    const sel = document.getElementById('judgeRunModelSelect');
    if (sel) {
        const defaultJudge = document.getElementById('expJudgeModelSelect')?.value || (enabledModels[0]?.id || '');
        sel.innerHTML = buildJudgeSelectOptionsHtml(enabledModels, defaultJudge, false);
    }

    const infoBox = document.getElementById('judgeRunModalInfo');
    if (infoBox) {
        const judged = Number(run.judged_items_count || 0);
        const totalComp = Number(run.completed_items || 0);
        const unjudged = Math.max(0, totalComp - judged);
        infoBox.innerHTML = `
            <div class="font-bold">Серія #${run.id}: ${escapeHtml(run.title)}</div>
            <div class="text-xs text-indigo-800 mt-1">
                Виконано замірів: <b>${totalComp}</b> | Вже оцінено ШІ-суддею: <b>${judged}</b> | Залишилось дооцінити: <b>${unjudged}</b>
            </div>
        `;
        const onlyUnjudgedCb = document.getElementById('judgeRunOnlyUnjudged') || document.getElementById('judgeRunOnlyUnjudgedCheckbox');
        if (onlyUnjudgedCb) {
            onlyUnjudgedCb.checked = unjudged > 0;
        }
    }

    const modal = document.getElementById('judgeRunModal');
    if (modal) {
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    }
}

function closeJudgeRunModal() {
    APP_STATE.pendingJudgeRunId = null;
    const modal = document.getElementById('judgeRunModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
}

async function confirmJudgeRunEvaluation() {
    const runId = APP_STATE.pendingJudgeRunId;
    const judgeModelId = document.getElementById('judgeRunModelSelect')?.value;
    const onlyUnjudgedCb = document.getElementById('judgeRunOnlyUnjudged') || document.getElementById('judgeRunOnlyUnjudgedCheckbox');
    const onlyUnjudged = onlyUnjudgedCb ? onlyUnjudgedCb.checked : true;
    const allowSelfJudge = Boolean(document.getElementById('judgeRunAllowSelfJudge')?.checked);
    if (!runId || !judgeModelId) return;

    const btn = document.getElementById('confirmJudgeRunBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Оцінювання...';
    }

    closeJudgeRunModal();
    const errBox = document.getElementById('expErrorBox');
    if (errBox) errBox.classList.add('hidden');

    try {
        const resp = await fetch(`/api/experiments/${runId}/judge`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                judge_model_id: judgeModelId,
                only_unjudged: onlyUnjudged,
                allow_self_judge: allowSelfJudge,
            }),
        });
        const data = await resp.json();
        await pollLogsAndStatus();

        const runsRes = await fetch('/api/experiments').then(r => r.json());
        APP_STATE.runs = runsRes.runs || [];
        APP_STATE.currentRunId = Number(runId);
        renderExperimentRunsList();
        updateAnalyticsVisibility();

        if (!resp.ok) {
            if (errBox) {
                errBox.textContent = data.detail || 'Помилка під час оцінювання суддею';
                errBox.classList.remove('hidden');
            }
            return;
        }
    } catch (err) {
        if (errBox) {
            errBox.textContent = `Помилка оцінювання суддею: ${err.message}`;
            errBox.classList.remove('hidden');
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Запустити оцінювання суддею';
        }
    }
}

async function selectRunForItemsTable(runId) {
    APP_STATE.currentRunId = Number(runId);
    renderExperimentRunsList();
}

async function onChangeLogTableRun() {
    const sel = document.getElementById('logFilterRunSelect');
    if (!sel || !sel.value) return;
    APP_STATE.currentRunId = Number(sel.value);
    renderExperimentRunsList();
}

async function openRunAnalytics(runId) {
    APP_STATE.currentRunId = Number(runId);
    APP_STATE.selectedAnalyticsRunIds = [Number(runId)];
    renderExperimentRunsList();
    updateAnalyticsVisibility();
    switchTab('analytics');
}

function promptDeleteExperimentRun(runId) {
    const run = APP_STATE.runs.find(r => r.id === Number(runId));
    if (!run) return;
    APP_STATE.pendingDeleteRunId = Number(runId);
    const infoBox = document.getElementById('deleteConfirmRunInfo');
    if (infoBox) {
        infoBox.innerHTML = `
            <div>Серія #${run.id}: ${escapeHtml(run.title)}</div>
            <div class="text-xs font-normal text-rose-800 mt-1">Дата: ${run.created_at} | Виконаних замірів: ${run.completed_items}/${run.total_items}</div>
        `;
    }
    const modal = document.getElementById('deleteConfirmModal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

function closeDeleteConfirmModal() {
    APP_STATE.pendingDeleteRunId = null;
    const modal = document.getElementById('deleteConfirmModal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

async function confirmDeleteExperimentRun() {
    const runId = APP_STATE.pendingDeleteRunId;
    if (!runId) return;
    closeDeleteConfirmModal();

    await fetch(`/api/experiments/${runId}`, { method: 'DELETE' });
    const runsRes = await fetch('/api/experiments').then(r => r.json());
    APP_STATE.runs = runsRes.runs || [];

    if (APP_STATE.runs.length > 0) {
        APP_STATE.currentRunId = APP_STATE.runs[0].id;
        APP_STATE.selectedAnalyticsRunIds = [APP_STATE.runs[0].id];
    } else {
        APP_STATE.currentRunId = null;
        APP_STATE.selectedAnalyticsRunIds = [];
    }

    renderExperimentRunsList();
    updateAnalyticsVisibility();
    await pollLogsAndStatus();
}

async function loadExperimentItemsLog() {
    if (!APP_STATE.currentRunId) return;
    const modelId = document.getElementById('logFilterModel').value;
    const promptCode = document.getElementById('logFilterPrompt').value;
    const params = new URLSearchParams({ model_id: modelId, prompt_code: promptCode, limit: '300' });

    const res = await fetch(`/api/experiments/${APP_STATE.currentRunId}/items?${params.toString()}`).then(r => r.json());
    const items = res.items || [];
    const tbody = document.getElementById('experimentItemsLogBody');

    if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" class="py-4 text-center text-slate-400">Немає замірів за обраним фільтром у серії #${APP_STATE.currentRunId}</td></tr>`;
        return;
    }

    tbody.innerHTML = items.map(item => {
        const hallucBadge = item.has_hallucination
            ? `<span class="px-1.5 py-0.5 rounded bg-rose-100 text-rose-800 font-bold text-[10px]">${item.hallucination_count || 1}</span>`
            : `<span class="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 font-semibold text-[10px]">0</span>`;
        const manualBadge = item.manual_scored
            ? `<span class="ml-1 px-1 py-0.2 rounded bg-indigo-100 text-indigo-800 text-[9px] font-bold" title="Ручна експертна оцінка">Експерт</span>`
            : '';
        return `
            <tr onclick="openEvaluationItemDetailModal(${item.id})" class="hover:bg-blue-50/70 cursor-pointer transition group" title="Натисніть, щоб переглянути деталі цього запиту">
                <td class="py-2 px-2 font-mono font-bold text-blue-700">${escapeHtml(item.query_code)}</td>
                <td class="py-2 px-2 font-medium text-slate-800">${escapeHtml(item.model_id)}</td>
                <td class="py-2 px-2 font-semibold text-indigo-700">${escapeHtml(item.prompt_code)}</td>
                <td class="py-2 px-2 text-center font-bold">${item.integral_efficiency_pct}%${manualBadge}</td>
                <td class="py-2 px-2 text-center text-emerald-700 font-semibold">${item.fact_coverage_pct}%</td>
                <td class="py-2 px-2 text-center">${hallucBadge}</td>
                <td class="py-2 px-2 text-center">${item.call_ms || item.latency_ms}</td>
                <td class="py-2 px-2 text-center font-mono text-slate-600">${item.input_tokens}/${item.output_tokens}</td>
                <td class="py-2 px-2 text-center font-mono font-semibold text-slate-800">${item.cost_unknown ? '<span class="text-amber-700">ціна не задана</span>' : ('$' + Number(item.cost_per_1m_usd ?? ((item.cost_per_1k_usd || 0) * 1000)).toFixed(2))}</td>
                <td class="py-2 px-2 text-center">
                    <span class="px-2 py-0.5 rounded bg-blue-100 text-blue-800 group-hover:bg-blue-600 group-hover:text-white font-semibold text-[11px] transition">
                        Деталі
                    </span>
                </td>
            </tr>
        `;
    }).join('');
}

// ============================================================================
// МОДАЛЬНЕ ВІКНО: ПОВНИЙ ПАСПОРТ ОКРЕМОГО ЗАМІРУ ТА РУЧНА ОЦІНКА (1-5)
// ============================================================================

async function openEvaluationItemDetailModal(itemId) {
    const modal = document.getElementById('itemDetailModal');
    const body = document.getElementById('itemDetailModalBody');
    const title = document.getElementById('detailModalTitle');

    modal.classList.remove('hidden');
    modal.classList.add('flex');
    title.textContent = `Завантаження паспорта заміру #${itemId}...`;
    body.innerHTML = `<div class="p-8 text-center text-slate-500">Завантаження інформації про запит, промпт та оцінку...</div>`;

    try {
        const d = await fetch(`/api/evaluations/${itemId}/detail`).then(r => r.json());
        const q = d.query || {};
        const m = d.model || {};
        const p = d.prompt || {};
        const gen = d.generation || {};
        const ev = d.evaluation || {};

        title.textContent = `Серія #${d.run_id} | Запит ${q.code} (${q.category_name_uk}) | Модель: ${m.name} | Промпт: ${p.code} (v${p.prompt_version || 1})`;

        const matchedBadges = (ev.matched_keywords || []).map(k =>
            `<span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 text-xs font-semibold border border-emerald-300">+ ${escapeHtml(k)}</span>`
        );
        const missingBadges = (ev.missing_keywords || []).map(k =>
            `<span class="px-2 py-0.5 rounded bg-rose-100 text-rose-800 text-xs font-semibold border border-rose-300">- ${escapeHtml(k)}</span>`
        );
        const hallucBadges = (ev.hallucinated_items || []).map(h =>
            `<span class="px-2 py-0.5 rounded bg-rose-200 text-rose-900 text-xs font-bold border border-rose-400">Галюцинація: ${escapeHtml(h)}</span>`
        );

        const fmtChecksEntries = Object.entries(ev.format_checks || {}).map(([k, passed]) =>
            `<span class="px-2 py-0.5 rounded text-[11px] font-mono ${passed ? 'bg-emerald-50 text-emerald-800 border border-emerald-200' : 'bg-rose-50 text-rose-800 border border-rose-200'}">${passed ? '+' : '-'} ${escapeHtml(k)}</span>`
        ).join(' ');

        const fewShotUsedStr = (p.few_shot_used && p.few_shot_used.length > 0)
            ? `Використані Few-Shot приклади: ${p.few_shot_used.join(', ')}`
            : 'Few-Shot приклади не використовувалися';

        body.innerHTML = `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div class="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-2">
                    <div class="flex items-center justify-between">
                        <span class="text-xs font-bold uppercase text-blue-700">Вхідний запит користувача</span>
                        <span class="px-2 py-0.5 rounded bg-blue-100 text-blue-900 font-mono text-xs font-bold">${escapeHtml(q.code)}: ${escapeHtml(q.category_name_uk)}</span>
                    </div>
                    <div class="p-3 rounded-lg bg-white border border-slate-200 font-semibold text-slate-900 text-sm">
                        ${escapeHtml(q.user_query)}
                    </div>
                    <div class="text-xs text-slate-600">
                        <b>Нормативний факт із бази знань (Context Fact):</b>
                        <div class="mt-1 p-2.5 rounded bg-white border border-slate-200 text-slate-700">${escapeHtml(q.context_fact)}</div>
                    </div>
                </div>

                <div class="p-4 rounded-xl bg-emerald-50/60 border border-emerald-200 space-y-2">
                    <div class="flex items-center justify-between">
                        <span class="text-xs font-bold uppercase text-emerald-800">Еталон (Ground Truth) та Фактична точність</span>
                        <span class="text-xs font-bold text-emerald-900">Повнота фактів = ${ev.fact_coverage_pct}% | Галюцинацій: ${ev.hallucination_count || 0}</span>
                    </div>
                    <div class="p-3 rounded-lg bg-white border border-emerald-200 text-slate-800 text-xs leading-relaxed">
                        ${escapeHtml(q.reference_answer)}
                    </div>
                    <div>
                        <div class="text-xs font-bold text-emerald-900 mb-1">Верифікація обов'язкових фактів та детектор галюцинацій:</div>
                        <div class="flex flex-wrap gap-1.5">
                            ${matchedBadges.concat(missingBadges).concat(hallucBadges).join('') || '<span class="text-slate-400">Немає ключових слів</span>'}
                        </div>
                    </div>
                </div>
            </div>

            <div class="p-4 rounded-xl bg-indigo-50/40 border border-indigo-200 space-y-2">
                <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="text-xs font-bold uppercase text-indigo-900">
                        Фактично відправлений промпт (${escapeHtml(p.code)}, hash: <code>${escapeHtml(p.template_hash || '-')}</code>)
                    </span>
                    <span class="text-xs font-mono text-indigo-700">${escapeHtml(fewShotUsedStr)}</span>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
                    <div>
                        <div class="text-[11px] font-bold text-slate-600 mb-1">SYSTEM PROMPT:</div>
                        <pre class="p-3 rounded-lg bg-slate-900 text-slate-100 font-mono text-xs whitespace-pre-wrap max-h-44 overflow-y-auto">${escapeHtml(p.system_prompt)}</pre>
                    </div>
                    <div>
                        <div class="text-[11px] font-bold text-slate-600 mb-1">USER PROMPT:</div>
                        <pre class="p-3 rounded-lg bg-slate-900 text-emerald-200 font-mono text-xs whitespace-pre-wrap max-h-44 overflow-y-auto">${escapeHtml(p.user_prompt)}</pre>
                    </div>
                </div>
            </div>

            <div class="p-4 rounded-xl bg-white border-2 border-blue-200 space-y-2">
                <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-2">
                    <div>
                        <span class="text-xs font-bold uppercase text-blue-700">Відповідь мовної моделі</span>
                        <div class="font-bold text-slate-900 text-sm">${escapeHtml(m.name)} (${escapeHtml(m.provider_used)})</div>
                    </div>
                    <div class="flex flex-wrap gap-2 text-xs">
                        <span class="px-2 py-1 rounded bg-slate-100 font-semibold">call: <b>${gen.call_ms} мс</b> / wall: <b>${gen.wall_ms} мс</b> (спроб: ${gen.attempts || 1})</span>
                        <span class="px-2 py-1 rounded bg-slate-100 font-mono">Токени: <b>${gen.input_tokens} вх / ${gen.output_tokens} вих</b> (thinking: ${gen.thinking_tokens || 0})</span>
                        <span class="px-2 py-1 rounded bg-slate-100 font-mono">finish: <b>${escapeHtml(gen.finish_reason || 'stop')}</b>${gen.truncated ? ' [TRUNCATED]' : ''}</span>
                        <span class="px-2 py-1 rounded bg-slate-100 font-mono">Вартість: <b>$${Number(gen.cost_per_1m_usd ?? ((gen.cost_per_1k_usd || 0) * 1000)).toFixed(2)} / 1M</b></span>
                    </div>
                </div>
                <div class="p-3.5 rounded-lg bg-slate-50 border border-slate-200 text-slate-900 whitespace-pre-line leading-relaxed text-sm">
                    ${escapeHtml(gen.generated_answer)}
                </div>
            </div>

            <div class="p-4 rounded-xl bg-slate-100 border border-slate-300 space-y-3">
                <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="text-xs font-bold uppercase text-slate-800">Детерміновані метрики якості та чек-лист</span>
                    <span class="px-3 py-1 rounded-lg bg-blue-600 text-white font-extrabold text-sm">
                        Якість відповіді = ${ev.integral_efficiency_pct}%
                    </span>
                </div>

                <div class="p-3 rounded-lg bg-slate-900 text-emerald-300 font-mono text-xs">
                    ${escapeHtml(ev.formula_breakdown_uk)}
                </div>

                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-center">
                    <div class="p-2.5 rounded-lg bg-white border border-slate-200">
                        <div class="text-[11px] text-slate-500">Фактична повнота (FC)</div>
                        <div class="text-base font-extrabold text-emerald-700">${ev.fact_coverage_pct}%</div>
                    </div>
                    <div class="p-2.5 rounded-lg bg-white border border-slate-200">
                        <div class="text-[11px] text-slate-500">Схожість до еталона (Cosine / ROUGE-L)</div>
                        <div class="text-base font-extrabold text-blue-700">${ev.semantic_similarity_pct}% <span class="text-[10px] font-normal">(${ev.stem_cosine_pct}% / ${ev.rouge_l_f1_pct}%)</span></div>
                    </div>
                    <div class="p-2.5 rounded-lg bg-white border border-slate-200">
                        <div class="text-[11px] text-slate-500">Чек-лист формату (FMT)</div>
                        <div class="text-base font-extrabold text-indigo-700">${ev.format_compliance_pct}%</div>
                    </div>
                    <div class="p-2.5 rounded-lg bg-white border border-slate-200">
                        <div class="text-[11px] text-slate-500">Рубрика якості (1-5)</div>
                        <div class="text-base font-extrabold text-violet-700">${ev.nist_composite_pct}% (${ev.avg_nist_score_5}/5)</div>
                    </div>
                </div>

                <div class="p-2.5 rounded-lg bg-white border border-slate-200 text-xs">
                    <span class="font-bold text-slate-700 mr-2">Перевірки чек-листа формату (${escapeHtml(p.code)}):</span>
                    ${fmtChecksEntries || '<span class="text-slate-400">Немає даних чек-листа</span>'}
                </div>

                <div class="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs text-slate-700">
                    <div>
                        Шлюз ескалації (${escapeHtml(ev.escalation_method || 'regex')}):
                        <b>${ev.requires_escalation ? 'Потребує ескалації на оператора' : 'Автоматична відповідь користувачу'}</b>
                        (Еталон: ${q.expected_escalation ? 'Ескалація' : 'Автоматизація'},
                        <span class="${ev.escalation_correct ? 'text-emerald-700 font-bold' : 'text-rose-700 font-bold'}">
                            ${ev.escalation_correct ? 'Збігається з еталоном' : 'Не збігається'}
                        </span>)
                    </div>
                    <div>
                        Нециклічний статус автоматизації:
                        <span class="px-2 py-0.5 rounded font-bold ${ev.automation_success ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}">
                            ${ev.automation_success ? 'Успішно автоматизовано / маршрутизовано' : 'Потребує участі оператора'}
                        </span>
                    </div>
                </div>
            </div>

            <div class="p-4 rounded-xl bg-indigo-50/70 border border-indigo-300 space-y-3">
                <div class="flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <span class="text-xs font-bold uppercase text-indigo-950">Ручна експертна оцінка за рубрикою (1-5, NIST AI RMF)</span>
                        <p class="text-[11px] text-slate-600">
                            Дослідник може виставити або скоригувати бали за 5 критеріями рубрики. Дані зберігаються у БД (<code>manual_scored=1</code>) для розрахунку узгодженості (Weighted Cohen's κ / Spearman ρ).
                        </p>
                    </div>
                    <span class="px-2.5 py-1 rounded text-xs font-bold ${ev.manual_scored ? 'bg-emerald-100 text-emerald-800 border border-emerald-300' : 'bg-slate-200 text-slate-700'}">
                        ${ev.manual_scored ? `Оцінено експертом (${escapeHtml(ev.manual_annotator || 'researcher')})` : (ev.rubric_evaluated ? `Оцінено LLM-суддею (${escapeHtml(ev.judge_model || '')})` : 'Детермінована оцінка')}
                    </span>
                </div>

                <div class="grid grid-cols-2 sm:grid-cols-5 gap-2.5 text-xs">
                    <div>
                        <label class="block font-semibold text-slate-700 mb-1">Релевантність (1-5):</label>
                        <input id="manualScoreRel" type="number" min="1" max="5" step="0.5" value="${ev.relevance_score}" class="w-full border border-slate-300 rounded px-2 py-1 bg-white font-bold">
                    </div>
                    <div>
                        <label class="block font-semibold text-slate-700 mb-1">Факт. точність (1-5):</label>
                        <input id="manualScoreFact" type="number" min="1" max="5" step="0.5" value="${ev.factuality_score}" class="w-full border border-slate-300 rounded px-2 py-1 bg-white font-bold">
                    </div>
                    <div>
                        <label class="block font-semibold text-slate-700 mb-1">Повнота (1-5):</label>
                        <input id="manualScoreComp" type="number" min="1" max="5" step="0.5" value="${ev.completeness_score}" class="w-full border border-slate-300 rounded px-2 py-1 bg-white font-bold">
                    </div>
                    <div>
                        <label class="block font-semibold text-slate-700 mb-1">Зрозумілість (1-5):</label>
                        <input id="manualScoreClar" type="number" min="1" max="5" step="0.5" value="${ev.clarity_score}" class="w-full border border-slate-300 rounded px-2 py-1 bg-white font-bold">
                    </div>
                    <div>
                        <label class="block font-semibold text-slate-700 mb-1">Політика/Формат (1-5):</label>
                        <input id="manualScorePol" type="number" min="1" max="5" step="0.5" value="${ev.policy_adherence_score ?? ev.format_score}" class="w-full border border-slate-300 rounded px-2 py-1 bg-white font-bold">
                    </div>
                </div>

                <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-xs items-end">
                    <div>
                        <label class="block font-semibold text-slate-700 mb-1">Ідентифікатор експерта:</label>
                        <input id="manualAnnotatorInput" type="text" value="${escapeHtml(ev.manual_annotator || 'expert_1')}" class="w-full border border-slate-300 rounded px-2.5 py-1.5 bg-white">
                    </div>
                    <div>
                        <label class="block font-semibold text-slate-700 mb-1">Коментар експерта (опціонально):</label>
                        <input id="manualNotesInput" type="text" value="${escapeHtml(ev.manual_notes || '')}" placeholder="Нотатки щодо помилок або стилю..." class="w-full border border-slate-300 rounded px-2.5 py-1.5 bg-white">
                    </div>
                    <button onclick="saveManualScoreOverride(${d.item_id})" class="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-bold shadow">
                        Зберегти ручну оцінку
                    </button>
                </div>
                <div id="manualScoreSavedMsg" class="hidden text-xs font-bold text-emerald-800 bg-emerald-100 px-3 py-1.5 rounded"></div>
            </div>
        `;
    } catch (e) {
        body.innerHTML = `<div class="p-4 rounded bg-rose-50 text-rose-800">Помилка завантаження деталей заміру: ${escapeHtml(e.message)}</div>`;
    }
}

async function saveManualScoreOverride(itemId) {
    const rel = Number(document.getElementById('manualScoreRel').value);
    const fact = Number(document.getElementById('manualScoreFact').value);
    const comp = Number(document.getElementById('manualScoreComp').value);
    const clar = Number(document.getElementById('manualScoreClar').value);
    const pol = Number(document.getElementById('manualScorePol').value);
    const annotator = document.getElementById('manualAnnotatorInput').value.trim() || 'expert_1';
    const notes = document.getElementById('manualNotesInput').value.trim();

    const resp = await fetch(`/api/evaluations/${itemId}/manual-score`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            relevance_score: rel,
            factuality_score: fact,
            completeness_score: comp,
            clarity_score: clar,
            format_score: pol,
            policy_adherence_score: pol,
            manual_annotator: annotator,
            manual_notes: notes,
        }),
    });
    const res = await resp.json();
    if (resp.ok) {
        const msg = document.getElementById('manualScoreSavedMsg');
        if (msg) {
            msg.textContent = `Ручну експертну оцінку збережено. Бал рубрики: ${res.rubric_composite_pct}%, Q_det: ${res.integral_efficiency_pct}%`;
            msg.classList.remove('hidden');
        }
        await loadExperimentItemsLog();
        await loadFlexibleAnalytics(false);
    }
}

function closeItemDetailModal() {
    const modal = document.getElementById('itemDetailModal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}
