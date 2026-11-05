const VERDICT_CLASSES = {
    supported: { card: 'border-emerald-600', label: 'text-emerald-800' },
    not_supported: { card: 'border-slate-400', label: 'text-slate-800' },
    insufficient: { card: 'border-amber-500', label: 'text-amber-800' },
};

function hypothesisCardHtml(h) {
    const style = VERDICT_CLASSES[h.verdict] || VERDICT_CLASSES.insufficient;
    const evidence = (h.evidence || [])
        .map(e => `<li><span class="text-slate-500">${escapeHtml(e.label_uk)}:</span> ${escapeHtml(e.value_uk)}</li>`)
        .join('');
    const preliminary = h.preliminary_uk
        ? `<p class="text-xs text-slate-500 mt-2">Попередньо, це ще не висновок: ${escapeHtml(h.preliminary_uk)}</p>` : '';
    const missing = h.missing_uk ? `<p class="text-xs text-slate-500 mt-1">${escapeHtml(h.missing_uk)}</p>` : '';
    const basis = evidence
        ? `<details class="mt-2 text-xs text-slate-700"><summary class="cursor-pointer text-slate-600">На чому це ґрунтується</summary><ul class="mt-1 space-y-1">${evidence}</ul></details>`
        : '';
    return `<div class="card-box p-4 border-l-4 ${style.card}">
        <div class="text-xs font-semibold text-slate-500">${escapeHtml(h.id)}</div>
        <div class="text-sm text-slate-900 mt-0.5">${escapeHtml(h.statement_uk)}</div>
        <div class="mt-2 text-sm font-semibold ${style.label}">${escapeHtml(h.verdict_label_uk)}</div>
        <p class="text-sm text-slate-700 mt-1">${escapeHtml(h.summary_uk)}</p>
        ${preliminary}${missing}${basis}
    </div>`;
}

function renderHypotheses(h) {
    const box = document.getElementById('hypothesesStrip');
    if (!box) return;
    box.innerHTML = h ? ['h1', 'h2', 'h3'].map(k => hypothesisCardHtml(h[k])).join('') : '';
}

function renderSampleBar(data) {
    const line = document.getElementById('analyticsSampleLine');
    const badge = document.getElementById('analyticsReliabilityBadge');
    if (!line || !badge) return;
    const h = data.hypotheses || {};
    const queries = h.n_queries || 0;
    const runs = (data.run?.run_ids || []).length;
    line.textContent = `Запитів у вибірці: ${queries}. Моделей: ${(data.by_model || []).length}. Серій: ${runs}.`;
    const enough = queries >= (h.min_queries || 30);
    badge.textContent = enough ? 'Вибірка достатня для висновків' : `Вибірка мала, для висновків потрібно не менше ${h.min_queries || 30} запитів`;
    badge.className = `text-xs px-2.5 py-1 rounded-lg border ${enough ? 'border-emerald-300 text-emerald-800' : 'border-amber-300 text-amber-800'}`;
}

// Матриця «рядок x стовпець» зі значенням якості й розміром вибірки в кожній клітинці
function matrixTableHtml(rows, columns, matrix) {
    const head = columns.map(c => `<th class="p-2 text-center border">${escapeHtml(c.name)}</th>`).join('');
    const body = rows.map(r => {
        const cells = columns.map(c => {
            const cell = matrix[r.id]?.[c.id];
            if (!cell) return '<td class="p-2 text-center border text-slate-300">-</td>';
            const thin = cell.insufficient_sample ? ' (замало)' : '';
            return `<td class="p-2 text-center border ${getHeatClass(cell.integral_efficiency_pct)}" title="Повнота фактів ${cell.fact_coverage_pct}%">
                <div>${cell.integral_efficiency_pct}%${thin}</div>
                <div class="text-[10px] font-normal opacity-80">запитів ${cell.n}</div></td>`;
        }).join('');
        return `<tr><td class="p-2 font-semibold border">${escapeHtml(r.name)}</td>${cells}</tr>`;
    }).join('');
    return `<table class="w-full text-xs border-collapse"><thead><tr class="bg-slate-100"><th class="p-2 text-left border">Модель</th>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderDomainSection(data) {
    const box = document.getElementById('matrixModelDomainContainer');
    if (!box) return;
    const domains = data.by_domain || [];
    if (domains.length < 2) {
        box.innerHTML = '<p class="text-sm text-slate-600">У вибраних серіях лише одна сфера. Щоб перевірити, чи залежить найкраща модель від сфери, потрібна серія щонайменше по двох сферах.</p>';
        return;
    }
    const rows = (data.by_model || []).map(m => ({ id: m.model_id, name: m.model_name }));
    const columns = domains.map(d => ({ id: d.domain_code, name: d.domain_name_uk }));
    box.innerHTML = matrixTableHtml(rows, columns, data.matrix_model_domain || {});
}

function renderContextSection(data) {
    const box = document.getElementById('contextModesContainer');
    if (!box) return;
    const modes = data.by_context_mode || [];
    if (modes.length < 2) {
        box.innerHTML = '<p class="text-sm text-slate-600">Серії виконані в одному режимі контексту. Щоб побачити вплив контексту, запустіть серію з кількома режимами.</p>';
        return;
    }
    const pct = v => (v === null || v === undefined ? '-' : `${v}%`);
    const rows = modes.map(m => `<tr class="border-b border-slate-100">
        <td class="py-2 pr-3">${escapeHtml(m.context_mode_name_uk)}</td>
        <td class="py-2 pr-3">${pct(m.integral_efficiency_pct)}</td>
        <td class="py-2 pr-3">${pct(m.fact_coverage_pct)}</td>
        <td class="py-2 pr-3">${pct(m.hallucination_rate_pct)}</td>
        <td class="py-2">${pct(m.retrieval_hit_at_1_pct)} / ${pct(m.retrieval_hit_at_3_pct)}</td></tr>`).join('');
    const cmp = data.context_mode_comparison || {};
    const notes = [];
    if (cmp.retrieval_loss_q_det_pct !== null && cmp.retrieval_loss_q_det_pct !== undefined) {
        notes.push(`Пошук програє еталонному контексту ${cmp.retrieval_loss_q_det_pct} п.п. якості.`);
    }
    if (cmp.rag_gain_over_no_context_fc_pct !== null && cmp.rag_gain_over_no_context_fc_pct !== undefined) {
        notes.push(`Порівняно з відповіддю без контексту пошук додає ${cmp.rag_gain_over_no_context_fc_pct} п.п. повноти фактів.`);
    }
    box.innerHTML = `<table class="w-full text-sm text-left"><thead class="text-xs text-slate-500 border-b border-slate-200"><tr>
        <th class="py-2 pr-3 font-medium">Режим контексту</th><th class="py-2 pr-3 font-medium">Якість</th>
        <th class="py-2 pr-3 font-medium">Повнота фактів</th><th class="py-2 pr-3 font-medium">Галюцинації</th>
        <th class="py-2 font-medium">Пошук знайшов потрібне (перше / серед трьох)</th></tr></thead><tbody>${rows}</tbody></table>
        ${notes.length ? `<p class="text-sm text-slate-700 mt-3">${notes.map(escapeHtml).join(' ')}</p>` : ''}`;
}

function renderResultsOverview(data) {
    renderHypotheses(data.hypotheses);
    renderSampleBar(data);
    renderDomainSection(data);
    renderContextSection(data);
}
