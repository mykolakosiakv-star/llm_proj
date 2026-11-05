function buildJudgeSelectOptionsHtml(enabledModels, selectedValue, includeNoneOption = true) {
    const noneOpt = includeNoneOption
        ? `<option value="">Без ШІ-судді (детермінована оцінка)</option>`
        : '';
    if (!enabledModels || enabledModels.length === 0) {
        return noneOpt + `<option value="" disabled>Немає увімкнених моделей (увімкніть у вкладці Провайдери та моделі)</option>`;
    }
    const opts = enabledModels.map(m => {
        const viaOpenRouter = m.can_openrouter && m.route === 'openrouter';
        const routeTag = viaOpenRouter ? 'OpenRouter' : 'Прямий API';
        const statusStr = m.status_label || 'Працює';
        const inCtx = formatTokenNum(m.context_in || m.input_limit);
        const rpdVal = m.rpd || '-';
        const famStr = m.family || m.provider || '';
        const isSel = m.id === selectedValue ? 'selected' : '';
        return `<option value="${m.id}" ${isSel}>${escapeHtml(m.name)} | ${escapeHtml(statusStr)} | RPD: ${escapeHtml(String(rpdVal))} | Контекст: ${inCtx} | ${routeTag} [${escapeHtml(famStr)}]</option>`;
    }).join('');
    return noneOpt + opts;
}

function populateSandboxQueryPresets() {
    const meta = APP_STATE.metadata;
    if (!meta) return;
    const presetSel = document.getElementById('sandboxQueryPreset');
    if (!presetSel) return;
    const domSel = document.getElementById('sandboxDomainFilter');
    const selectedDomain = domSel ? domSel.value : 'fintech';
    const prevPreset = presetSel.value || '';

    const filteredQueries = selectedDomain === 'all'
        ? APP_STATE.queries
        : APP_STATE.queries.filter(q => (q.domain || 'fintech') === selectedDomain);

    presetSel.innerHTML = `<option value="">Ввести довільний запит користувача вручну</option>` +
        filteredQueries.map(q => {
            const catName = meta.categories[q.category]?.name_uk || q.category;
            return `<option value="${q.id}">[${q.code} | ${catName}] ${escapeHtml(q.user_query)}</option>`;
        }).join('');

    if (prevPreset && filteredQueries.some(q => String(q.id) === String(prevPreset))) {
        presetSel.value = String(prevPreset);
    } else if (filteredQueries.length > 0) {
        presetSel.value = String(filteredQueries[0].id);
        document.getElementById('sandboxUserQuery').value = filteredQueries[0].user_query;
    }
}

function onSandboxDomainChange() {
    populateSandboxQueryPresets();
}

function updateSandboxPromptPreview() {
    const promptSel = document.getElementById('sandboxPromptSelect');
    if (!promptSel) return;
    const pCode = promptSel.value || 'P3';
    const found = APP_STATE.prompts.find(p => p.code === pCode);
    if (!found) return;

    const codeEl = document.getElementById('sandboxPromptPreviewCode');
    const sysEl = document.getElementById('sandboxSystemTemplatePreview');
    const usrEl = document.getElementById('sandboxUserTemplatePreview');
    const descEl = document.getElementById('sandboxPromptStrategyDesc');

    if (codeEl) codeEl.textContent = `${found.code} (${found.strategy_type}, v${found.version || 1})`;
    if (sysEl) sysEl.textContent = found.system_template || '';
    if (usrEl) usrEl.textContent = found.user_template || '';
    if (descEl) {
        descEl.innerHTML = `<b>Стратегія ${escapeHtml(found.code)}:</b> ${escapeHtml(found.description_uk)}`;
    }
}

function onSelectPresetQuery() {
    const qId = Number(document.getElementById('sandboxQueryPreset').value);
    if (!qId) return;
    const found = APP_STATE.queries.find(q => q.id === qId);
    if (found) {
        document.getElementById('sandboxUserQuery').value = found.user_query;
    }
}

// ============================================================================
// ВИКОНАННЯ ОДИНОЧНОГО ЗАПИТУ ДО LLM
// ============================================================================

async function runSandboxPipeline() {
    const userQuery = document.getElementById('sandboxUserQuery').value.trim();
    if (!userQuery) return;

    const presetVal = document.getElementById('sandboxQueryPreset').value;
    const queryId = presetVal ? Number(presetVal) : null;
    const modelId = document.getElementById('sandboxModelSelect').value;
    const promptCode = document.getElementById('sandboxPromptSelect').value;
    const judgeModel = document.getElementById('sandboxJudgeSelect')?.value || null;

    const errBox = document.getElementById('sandboxErrorBox');
    errBox.classList.add('hidden');

    const btn = document.getElementById('runSandboxBtn');
    btn.disabled = true;
    btn.textContent = 'Виконується запит до LLM...';

    try {
        const resp = await fetch('/api/automate/single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_query: userQuery,
                model_id: modelId,
                prompt_code: promptCode,
                query_id: queryId,
                judge_model_id: judgeModel,
            })
        });
        const data = await resp.json();
        await pollLogsAndStatus();

        if (!resp.ok) {
            errBox.textContent = data.detail || 'Помилка виконання запиту';
            errBox.classList.remove('hidden');
            return;
        }

        renderSingleSandboxResult(data);
    } catch (err) {
        errBox.textContent = `Помилка з'єднання: ${err.message}`;
        errBox.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Виконати запит до LLM';
    }
}

function renderSingleSandboxResult(res) {
    document.getElementById('sandboxResultContainer').classList.remove('hidden');
    const cls = res.classification;
    const gen = res.generation;
    const ev = res.evaluation;

    document.getElementById('resModelAndPromptTitle').textContent =
        `${res.model_name} (${gen.provider_used}) / ${res.prompt_name_uk}`;

    const gateBadge = document.getElementById('resAutomationGateBadge');
    if (ev.requires_escalation) {
        gateBadge.innerHTML = `<span class="px-2.5 py-1 rounded-full text-xs font-bold bg-amber-100 text-amber-800 border border-amber-300">
            Шлюз: Ескалація оператору (${escapeHtml(ev.escalation_method || 'regex')})
        </span>`;
    } else {
        gateBadge.innerHTML = `<span class="px-2.5 py-1 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">
            Шлюз: Відповідь автоматизовано
        </span>`;
    }

    document.getElementById('resGeneratedAnswer').textContent = gen.generated_answer;
    document.getElementById('resMatchedQueryCode').textContent =
        `${cls.matched_query_code} (${cls.category_name_uk}, Jaccard=${cls.confidence})`;
    document.getElementById('resReferenceAnswer').textContent = res.grounding.reference_answer;

    const matchedBadges = (ev.matched_keywords || []).map(k =>
        `<span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 text-xs font-semibold">+ ${escapeHtml(k)}</span>`
    );
    const missingBadges = (ev.missing_keywords || []).map(k =>
        `<span class="px-2 py-0.5 rounded bg-rose-100 text-rose-800 text-xs font-semibold">- ${escapeHtml(k)}</span>`
    );
    const hallucBadges = (ev.hallucinated_items || []).map(h =>
        `<span class="px-2 py-0.5 rounded bg-rose-200 text-rose-900 text-xs font-bold">Галюцинація: ${escapeHtml(h)}</span>`
    );
    document.getElementById('resKeywordsCheckList').innerHTML =
        `<span class="text-xs font-semibold text-slate-600">Ключові факти:</span>` +
        matchedBadges.concat(missingBadges).concat(hallucBadges).join('');

    document.getElementById('resFullRenderedPrompt').textContent = res.prompt_details.full_rendered_prompt;
    document.getElementById('resIntegralScoreBig').textContent = `${ev.integral_efficiency_pct}%`;

    const rubricBadge = document.getElementById('resRubricModeBadge');
    if (rubricBadge) {
        if (ev.rubric_evaluated) {
            rubricBadge.className = 'text-[10px] px-2 py-0.5 rounded bg-indigo-100 text-indigo-800 font-bold';
            rubricBadge.textContent = 'Оцінено LLM-суддею за рубрикою';
        } else {
            rubricBadge.className = 'text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-600 font-semibold';
            rubricBadge.textContent = 'детермінована оцінка (без LLM-судді)';
        }
    }

    const pctBars = [
        { label: 'Фактична повнота (Fact Coverage FC)', val: ev.fact_coverage_pct, color: 'bg-emerald-600' },
        { label: 'Лексико-семантична схожість (SS = Cosine + ROUGE-L)', val: ev.semantic_similarity_pct, color: 'bg-blue-600' },
        { label: 'Дотримання формату за чек-листом (FMT)', val: ev.format_compliance_pct, color: 'bg-indigo-600' },
        { label: 'Бал рубрики якості (1-5)', val: ev.nist_composite_pct, color: 'bg-violet-600' },
    ];
    document.getElementById('resPercentMetricsBars').innerHTML = pctBars.map(b => `
        <div>
            <div class="flex justify-between mb-1">
                <span class="text-slate-700 font-medium">${b.label}</span>
                <span class="font-bold text-slate-900">${b.val}%</span>
            </div>
            <div class="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
                <div class="${b.color} h-full rounded-full" style="width: ${Math.min(100, b.val)}%"></div>
            </div>
        </div>
    `).join('');

    const nistItems = [
        { name: 'Релевант.', val: ev.relevance_score },
        { name: 'Коректн.', val: ev.factuality_score },
        { name: 'Повнота', val: ev.completeness_score },
        { name: 'Зрозуміл.', val: ev.clarity_score },
        { name: 'Політика', val: ev.policy_adherence_score ?? ev.format_score },
    ];
    document.getElementById('resNistFiveBoxes').innerHTML = nistItems.map(n => `
        <div class="p-1.5 rounded bg-slate-50 border border-slate-200">
            <div class="text-[10px] text-slate-500">${n.name}</div>
            <div class="text-sm font-bold text-slate-900">${n.val}</div>
        </div>
    `).join('');

    document.getElementById('resOperationalBoxes').innerHTML = `
        <div class="p-2 rounded bg-slate-50 border border-slate-200">
            <div class="text-[11px] text-slate-500">Час (call / wall)</div>
            <div class="font-bold text-slate-900">${gen.call_ms || gen.latency_ms} / ${gen.wall_ms || gen.latency_ms} мс</div>
        </div>
        <div class="p-2 rounded bg-slate-50 border border-slate-200">
            <div class="text-[11px] text-slate-500">Токени (Вх/Вих)</div>
            <div class="font-bold text-slate-900">${gen.input_tokens} / ${gen.output_tokens}</div>
        </div>
        <div class="p-2 rounded bg-slate-50 border border-slate-200">
            <div class="text-[11px] text-slate-500">Вартість / 1M</div>
            <div class="font-bold text-slate-900 font-mono">$${Number(gen.cost_per_1m_usd ?? ((gen.cost_per_1k_usd || 0) * 1000)).toFixed(2)}</div>
        </div>
    `;
}
