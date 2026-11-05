function populateAssistantControls() {
    const domSel = document.getElementById('assistantDomain');
    const modelSel = document.getElementById('assistantModel');
    if (!domSel || !modelSel) return;

    const prevDomain = domSel.value || 'fintech';
    const domains = Object.values(APP_STATE.metadata?.domains || {});
    domSel.innerHTML = domains.map(d => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.name_uk)}</option>`).join('');
    if (domains.some(d => d.id === prevDomain)) domSel.value = prevDomain;

    const models = getEnabledModelsList();
    const recommendedId = APP_STATE.latestAnalyticsData?.conclusion?.recommended_model_id;
    const prevModel = modelSel.value;
    modelSel.innerHTML = models.length
        ? models.map(m => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`).join('')
        : '<option value="">Немає увімкнених моделей</option>';
    const preferred = [prevModel, recommendedId].find(id => id && models.some(m => m.id === id));
    if (preferred) modelSel.value = preferred;
    document.getElementById('assistantModelHint').textContent =
        modelSel.value && modelSel.value === recommendedId ? 'Рекомендована за результатами експериментів.' : '';
}

async function askAssistant() {
    const query = document.getElementById('assistantQuery').value.trim();
    const modelId = document.getElementById('assistantModel').value;
    const errBox = document.getElementById('assistantErrorBox');
    const btn = document.getElementById('assistantAskBtn');
    errBox.classList.add('hidden');
    if (query.length < 3 || !modelId) {
        errBox.textContent = 'Введіть запит і оберіть модель.';
        errBox.classList.remove('hidden');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Готуємо відповідь...';
    try {
        const resp = await fetch('/api/assistant/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_query: query,
                model_id: modelId,
                domain: document.getElementById('assistantDomain').value,
            }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Не вдалося отримати відповідь');
        renderAssistantResult(data);
    } catch (err) {
        errBox.textContent = err.message;
        errBox.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Отримати відповідь';
    }
}

function renderAssistantResult(res) {
    document.getElementById('assistantResult').classList.remove('hidden');
    const decision = document.getElementById('assistantDecision');
    decision.className = `border-l-4 pl-3 py-1 text-sm ${res.escalate ? 'border-amber-500' : 'border-emerald-600'}`;
    decision.innerHTML = `<div class="font-semibold text-slate-900">${res.escalate ? 'Передати оператору' : 'Можна відповісти автоматично'}</div>` +
        `<div class="text-slate-600">${escapeHtml(res.reason || '')}</div>`;
    document.getElementById('assistantAnswer').innerHTML =
        escapeHtml(res.answer).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    document.getElementById('assistantSources').innerHTML = res.sources.map(s =>
        `<li class="border border-slate-200 rounded-lg p-2.5"><span class="font-mono text-slate-500">${escapeHtml(s.code)}</span>` +
        `<span class="text-slate-400"> | релевантність ${s.score.toFixed(1)}</span><div class="mt-1">${escapeHtml(s.text)}</div></li>`
    ).join('') || '<li class="text-slate-500">Підходящих фрагментів не знайдено.</li>';
    document.getElementById('assistantMeta').textContent =
        `Категорія: ${res.category_name_uk}. Модель: ${res.model_name}, ${res.latency_ms} мс. Рішення ухвалила модель, тому відповідь варто переглянути перед відправкою клієнту.`;
}
