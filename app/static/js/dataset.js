// ============================================================================
// ДИНАМІЧНИЙ РЕНДЕРИНГ МЕТОДИКИ (/api/methodology)
// ============================================================================

function renderMethodologySection() {
    const container = document.getElementById('methodologyDynamicContainer');
    if (!container) return;
    const meth = APP_STATE.methodology;
    if (!meth) {
        container.innerHTML = `<div class="text-slate-500">Методика завантажується...</div>`;
        return;
    }

    const metrics = meth.metrics_v2 || [];
    const statMethods = meth.statistical_methods || [];
    container.innerHTML = `
        <div class="flex flex-wrap gap-2 text-[11px] text-slate-600 pb-1">
            <span class="px-2 py-0.5 rounded bg-white border border-slate-200">Версія методики: <b>v${escapeHtml(meth.methodology_version)}</b></span>
            <span class="px-2 py-0.5 rounded bg-white border border-slate-200">Версія датасету: <b>${escapeHtml(meth.dataset_version)}</b> (<code>${escapeHtml(meth.dataset_hash)}</code>)</span>
            <span class="px-2 py-0.5 rounded bg-white border border-slate-200">Хеш коду: <code>${escapeHtml(meth.code_version)}</code></span>
            <span class="px-2 py-0.5 rounded bg-white border border-slate-200">Рубрика: <b>v${escapeHtml(meth.rubric_version)}</b> (<code>${escapeHtml(meth.rubric_hash)}</code>)</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            ${metrics.map((m) => `
                <div class="p-3.5 rounded-lg bg-white border border-slate-200 space-y-1.5">
                    <div class="flex items-center justify-between gap-2">
                        <span class="font-bold text-blue-900">${escapeHtml(m.name_uk)}</span>
                        <span class="text-[10px] font-mono bg-slate-100 px-1.5 py-0.5 rounded text-slate-600">${escapeHtml(m.range)}</span>
                    </div>
                    <div class="p-2 rounded bg-slate-900 text-emerald-300 font-mono text-[11px]">
                        ${escapeHtml(m.formula_latex)}
                    </div>
                    <p class="text-slate-600 leading-relaxed">${escapeHtml(m.description_uk)}</p>
                </div>
            `).join('')}
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2">
            ${statMethods.map(sm => `
                <div class="p-3 rounded-lg bg-indigo-50/50 border border-indigo-200 space-y-1">
                    <div class="font-bold text-indigo-950">${escapeHtml(sm.name_uk)}</div>
                    <p class="text-slate-700 leading-relaxed text-[11px]">${escapeHtml(sm.details_uk)}</p>
                </div>
            `).join('')}
        </div>
    `;
}

// ============================================================================
// БАЗА ЗАПИТІВ ТА ШАБЛОНИ ПРОМПТІВ (P1-P5)
// ============================================================================

function renderPromptCards() {
    document.getElementById('promptCardsGrid').innerHTML = APP_STATE.prompts.map(p => `
        <div class="p-4 rounded-xl bg-slate-50 border border-slate-200 text-xs space-y-2.5 flex flex-col justify-between">
            <div class="space-y-2">
                <div class="flex justify-between items-center">
                    <div class="flex items-center gap-2">
                        <span class="px-2.5 py-0.5 rounded bg-blue-600 text-white font-bold">${escapeHtml(p.code)}</span>
                        <span class="font-bold text-slate-900 text-sm">${escapeHtml(p.name_uk)}</span>
                        <span class="text-[10px] font-mono bg-slate-200 px-1.5 py-0.5 rounded text-slate-700">v${p.version || 1} (${escapeHtml(p.template_hash || '')})</span>
                    </div>
                    <button onclick="openPromptEditModal('${p.code}')" class="px-2.5 py-1 rounded bg-indigo-100 text-indigo-800 hover:bg-indigo-200 font-semibold text-[11px]">
                        Редагувати шаблон
                    </button>
                </div>
                <p class="text-slate-600">${escapeHtml(p.description_uk)}</p>

                <div>
                    <div class="font-bold text-slate-700 mb-1">SYSTEM PROMPT (Системна інструкція):</div>
                    <pre class="p-2.5 rounded-lg bg-slate-900 text-slate-100 font-mono text-[11px] whitespace-pre-wrap">${escapeHtml(p.system_template)}</pre>
                </div>

                <div>
                    <div class="font-bold text-slate-700 mb-1">USER PROMPT TEMPLATE (Шаблон формування запиту):</div>
                    <pre class="p-2.5 rounded-lg bg-slate-900 text-emerald-300 font-mono text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">${escapeHtml(p.user_template)}</pre>
                </div>
            </div>
        </div>
    `).join('');
}

function openPromptEditModal(promptCode) {
    const p = APP_STATE.prompts.find(x => x.code === promptCode);
    if (!p) return;

    document.getElementById('editPromptCode').value = p.code;
    const stInput = document.getElementById('editPromptStrategyType');
    if (stInput) stInput.value = p.strategy_type || 'custom';
    document.getElementById('promptEditModalTitle').textContent = `Редагування шаблону промпту ${p.code} (${p.strategy_type}, v${p.version || 1})`;
    document.getElementById('editPromptNameUk').value = p.name_uk || '';
    document.getElementById('editPromptDescUk').value = p.description_uk || '';
    document.getElementById('editPromptSystemTemplate').value = p.system_template || '';
    document.getElementById('editPromptUserTemplate').value = p.user_template || '';

    const modal = document.getElementById('promptEditModal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

function closePromptEditModal() {
    const modal = document.getElementById('promptEditModal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

async function savePromptTemplateChanges() {
    const code = document.getElementById('editPromptCode').value;
    const found = APP_STATE.prompts.find(x => x.code === code);
    const strategyType = document.getElementById('editPromptStrategyType')?.value || found?.strategy_type || 'custom';
    const payload = {
        code,
        name_uk: document.getElementById('editPromptNameUk').value.trim(),
        strategy_type: strategyType,
        description_uk: document.getElementById('editPromptDescUk').value.trim(),
        system_template: document.getElementById('editPromptSystemTemplate').value,
        user_template: document.getElementById('editPromptUserTemplate').value,
    };

    await fetch('/api/prompts', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });

    closePromptEditModal();
    await refreshMetadataAndData();
}

function renderDatasetTable() {
    const filterDom = document.getElementById('datasetDomainFilter')?.value || 'all';
    const filterCat = document.getElementById('datasetCategoryFilter')?.value || 'all';
    const meta = APP_STATE.metadata;

    const filtered = APP_STATE.queries.filter(q => {
        const qDom = q.domain || 'fintech';
        const matchDom = (filterDom === 'all' || qDom === filterDom);
        const matchCat = (filterCat === 'all' || q.category === filterCat);
        return matchDom && matchCat;
    });

    const domainLabels = {
        fintech: 'Фінтех',
        it_helpdesk: 'ІТ / SaaS',
        ecommerce: 'E-Commerce',
        telecom: 'Телеком',
    };

    document.getElementById('datasetTableBody').innerHTML = filtered.map(q => {
        const catInfo = meta?.categories?.[q.category] || { name_uk: q.category };
        const qDom = q.domain || 'fintech';
        const domBadge = `<span class="px-2 py-0.5 rounded bg-blue-50 text-blue-800 border border-blue-200 text-[10px] font-semibold">${escapeHtml(domainLabels[qDom] || qDom)}</span>`;
        const kwBadges = (q.required_keywords || []).map(k =>
            `<span class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 text-[11px] font-mono inline-block mb-0.5">${escapeHtml(formatKeywordEntry(k))}</span>`
        ).join(' ');
        const escBadge = q.expected_escalation
            ? `<span class="ml-1 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-bold">Ескалація</span>`
            : '';
        return `
            <tr class="hover:bg-slate-50">
                <td class="py-2 px-2.5 font-mono font-bold text-blue-700 whitespace-nowrap">${escapeHtml(q.code)}${escBadge}</td>
                <td class="py-2 px-2.5 whitespace-nowrap">${domBadge}</td>
                <td class="py-2 px-2.5 font-medium">${escapeHtml(catInfo.name_uk)}</td>
                <td class="py-2 px-2.5 font-medium text-slate-900">${escapeHtml(q.user_query)}</td>
                <td class="py-2 px-2.5 text-slate-600">${escapeHtml(q.reference_answer)}</td>
                <td class="py-2 px-2.5">${kwBadges}</td>
            </tr>
        `;
    }).join('');
}

function openImportQueriesModal() {
    const modal = document.getElementById('importQueriesModal');
    const area = document.getElementById('importQueriesJsonTextarea');
    const msg = document.getElementById('importQueriesStatusMsg');
    if (msg) msg.classList.add('hidden');
    if (area && !area.value.trim()) {
        area.value = JSON.stringify([
            {
                code: "CUST-01",
                category: "informational",
                user_query: "Який графік роботи служби технічної підтримки та час реакції на звернення?",
                context_fact: "Служба підтримки працює цілодобово 24/7, а середній час першої відповіді оператора становить до 5 хвилин.",
                reference_answer: "Підтримка працює цілодобово 24/7, середній час відповіді становить до 5 хвилин.",
                required_keywords: ["24/7", "5"],
                expected_escalation: false,
                complexity: 1
            }
        ], null, 2);
    }
    if (modal) {
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    }
}

function closeImportQueriesModal() {
    const modal = document.getElementById('importQueriesModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
}

async function submitImportQueriesJson() {
    const area = document.getElementById('importQueriesJsonTextarea');
    const domSel = document.getElementById('importQueriesDomainSelect');
    const msg = document.getElementById('importQueriesStatusMsg');
    if (!area || !msg) return;

    try {
        const parsed = JSON.parse(area.value);
        const items = Array.isArray(parsed) ? parsed : (parsed.items || [parsed]);
        const domain = domSel ? domSel.value : 'fintech';
        const resp = await fetch('/api/queries/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domain, items }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || 'Помилка імпорту');
        }
        msg.className = 'p-2.5 rounded-lg font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200 block';
        msg.textContent = `Успішно імпортовано запитів: ${data.imported_count}`;
        await refreshMetadataAndData();
        setTimeout(() => closeImportQueriesModal(), 1200);
    } catch (e) {
        msg.className = 'p-2.5 rounded-lg font-semibold bg-rose-50 text-rose-800 border border-rose-200 block';
        msg.textContent = `Помилка розбору JSON: ${e.message}`;
    }
}
