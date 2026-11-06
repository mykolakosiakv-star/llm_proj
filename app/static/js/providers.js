function getEnabledModelsList() {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return [];
    return meta.models.filter(m => m.enabled);
}

function renderEnvKeyStatus() {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.settings) return;
    const ks = meta.settings.key_status || {};
    const enabledCount = (meta.models || []).filter(m => m.enabled).length;

    const activeList = [];
    if (ks.gemini_configured) activeList.push('Gemini');
    if (ks.groq_configured) activeList.push('Groq');
    if (ks.openai_configured) activeList.push('OpenAI');
    if (ks.openrouter_configured) activeList.push('OpenRouter');
    if (ks.anthropic_configured) activeList.push('Anthropic');

    const headerBadge = document.getElementById('envHeaderStatus');
    if (headerBadge) {
        if (activeList.length > 0) {
            headerBadge.className = 'cursor-pointer text-xs px-2.5 py-1 rounded-lg border border-emerald-300 bg-emerald-50 text-emerald-800 font-semibold';
            headerBadge.textContent = `Ключі: ${activeList.join(', ')} | Обрано моделей: ${enabledCount}`;
        } else {
            headerBadge.className = 'cursor-pointer text-xs px-2.5 py-1 rounded-lg border border-amber-300 bg-amber-50 text-amber-800 font-semibold';
            headerBadge.textContent = 'Налаштуйте API-ключі';
        }
    }

    const envEl = document.getElementById('envFilePathLabel') || document.getElementById('envFileBadge');
    if (envEl) {
        envEl.textContent = meta.settings.env_file_path || '.env';
    }

    const setKeyCardState = (statusId, inputId, cardId, configured, masked) => {
        const el = document.getElementById(statusId);
        const inp = document.getElementById(inputId);
        const card = cardId ? document.getElementById(cardId) : null;
        if (el) {
            if (configured) {
                el.className = 'px-2 py-0.5 rounded font-semibold text-[10px] bg-emerald-100 text-emerald-800 border border-emerald-300';
                el.textContent = `Підключено (${masked})`;
            } else {
                el.className = 'px-2 py-0.5 rounded font-medium text-[10px] bg-slate-100 text-slate-500 border border-slate-300';
                el.textContent = 'Не підключено';
            }
        }
        if (inp && configured && masked) {
            inp.placeholder = `Збережено (${masked}) - введіть новий для заміни`;
        }
        if (card) {
            if (configured) {
                card.classList.remove('border-slate-200');
                card.classList.add('border-emerald-300', 'bg-emerald-50/20');
            } else {
                card.classList.remove('border-emerald-300', 'bg-emerald-50/20');
                card.classList.add('border-slate-200');
            }
        }
    };

    setKeyCardState('statusGeminiKey', 'settingsGeminiKey', 'cardGeminiKey', ks.gemini_configured, ks.gemini_masked);
    setKeyCardState('statusGroqKey', 'settingsGroqKey', 'cardGroqKey', ks.groq_configured, ks.groq_masked);
    setKeyCardState('statusOpenrouterKey', 'settingsOpenrouterKey', 'cardOpenrouterKey', ks.openrouter_configured, ks.openrouter_masked);
    setKeyCardState('statusOpenaiKey', 'settingsOpenaiKey', 'cardOpenaiKey', ks.openai_configured, ks.openai_masked);
    setKeyCardState('statusAnthropicKey', 'settingsAnthropicKey', 'cardAnthropicKey', ks.anthropic_configured, ks.anthropic_masked);

    const sOllamaUrl = document.getElementById('settingsOllamaUrl');
    if (sOllamaUrl) sOllamaUrl.value = meta.settings.ollama_base_url || 'http://localhost:11434';

    const sOllamaModel = document.getElementById('settingsOllamaModel');
    if (sOllamaModel) sOllamaModel.value = meta.settings.ollama_model_name || 'llama3.2';

    const sTemp = document.getElementById('settingsTemperature');
    if (sTemp) sTemp.value = meta.settings.temperature ?? 0.0;

    const sConf = document.getElementById('settingsConfThreshold');
    if (sConf) sConf.value = meta.settings.confidence_threshold ?? 0.75;

    const sProbeStartup = document.getElementById('settingsProbeOnStartup');
    if (sProbeStartup) sProbeStartup.checked = meta.settings.probe_on_startup !== false;
}

function toggleKeysConfigDrawer() {
    const drawer = document.getElementById('keysConfigDrawer');
    const arrow = document.getElementById('keysDrawerArrow');
    if (!drawer) return;
    const isHidden = drawer.classList.contains('hidden');
    if (isHidden) {
        drawer.classList.remove('hidden');
        if (arrow) arrow.textContent = '▴';
    } else {
        drawer.classList.add('hidden');
        if (arrow) arrow.textContent = '▾';
    }
}

function formatTokenNum(n) {
    if (!n && n !== 0) return '-';
    if (n >= 1000000) {
        const m = n / 1000000;
        return `${m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)}M`;
    }
    if (n >= 1000) {
        const k = n / 1000;
        return `${k % 1 === 0 ? k.toFixed(0) : k.toFixed(1)}k`;
    }
    return String(n);
}

function renderKeysQuickStatusPills() {
    const meta = APP_STATE.metadata;
    if (!meta) return;
    const ks = meta.settings?.key_status || {};
    const probeProvs = meta.probe?.providers || {};
    const pillsContainer = document.getElementById('keysQuickStatusPills');
    if (!pillsContainer) return;

    const provDefs = [
        { id: 'gemini', label: 'Google', configured: ks.gemini_configured },
        { id: 'groq', label: 'Groq', configured: ks.groq_configured },
        { id: 'openrouter', label: 'OpenRouter', configured: ks.openrouter_configured },
        { id: 'openai', label: 'OpenAI', configured: ks.openai_configured },
        { id: 'anthropic', label: 'Anthropic', configured: ks.anthropic_configured },
        { id: 'ollama', label: 'Ollama', configured: true, isLocal: true },
    ];

    pillsContainer.innerHTML = provDefs.map(p => {
        const pr = probeProvs[p.id] || {};
        const stCode = pr.status || (p.configured ? 'unchecked' : 'no_key');
        const stLabel = pr.status_label || {
            ok: 'Працює',
            no_credit: 'Нульовий баланс',
            rate_limited: 'Ліміт запитів',
            daily_quota: 'Денний ліміт вичерпано',
            offline: "Немає зв'язку",
            no_key: 'Ключ не задано',
            invalid_key: 'Невірний ключ',
            model_missing: 'Модель недоступна',
            unchecked: 'Не перевірено',
        }[stCode] || 'Не перевірено';

        let cls = 'bg-slate-800 text-slate-400 border-slate-700';
        let dot = 'bg-slate-500';
        if (stCode === 'ok') {
            cls = 'bg-emerald-950/80 text-emerald-300 border-emerald-700';
            dot = 'bg-emerald-400';
        } else if (stCode === 'no_credit' || stCode === 'invalid_key') {
            cls = 'bg-rose-950/80 text-rose-300 border-rose-700';
            dot = 'bg-rose-400';
        } else if (stCode === 'rate_limited' || stCode === 'daily_quota') {
            cls = 'bg-amber-950/80 text-amber-300 border-amber-700';
            dot = 'bg-amber-400';
        }

        return `<span class="px-2 py-0.5 rounded border ${cls} flex items-center gap-1.5 font-mono text-[10px]">
            <span class="w-1.5 h-1.5 rounded-full ${dot}"></span>
            <span class="font-semibold text-white">${p.label}:</span>
            <span>${escapeHtml(stLabel)}</span>
        </span>`;
    }).join('');
}

function updateActiveModelsCounter() {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;
    const enabledCount = meta.models.filter(m => m.enabled).length;
    const badge = document.getElementById('activeModelsCountBadge');
    if (badge) {
        badge.textContent = `Увімкнено ${enabledCount} з ${meta.models.length}`;
    }
}

function renderActiveModelsSummary() {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;

    const countEl = document.getElementById('activeModelsSummaryCount');
    const listEl = document.getElementById('activeModelsChipsList');
    if (!countEl || !listEl) return;

    const activeModels = meta.models.filter(m => m.enabled);
    countEl.textContent = `Увімкнено ${activeModels.length} з ${meta.models.length}`;

    if (activeModels.length === 0) {
        listEl.innerHTML = `<div class="text-slate-400 italic text-xs py-1">Жодної моделі не обрано. Відкрийте потрібного провайдера або натисніть кнопку вибору робочих моделей.</div>`;
        return;
    }

    const provColorDot = {
        gemini: 'bg-blue-500',
        groq: 'bg-amber-500',
        openrouter: 'bg-purple-500',
        openai: 'bg-emerald-500',
        anthropic: 'bg-rose-500',
        ollama: 'bg-slate-500',
        custom: 'bg-indigo-500',
    };

    listEl.innerHTML = activeModels.map(m => {
        const dot = provColorDot[m.provider] || 'bg-slate-400';
        const stCode = m.status_code || 'unchecked';
        const stLabel = m.status_label || 'Не перевірено';
        const stDot = stCode === 'ok' ? 'text-emerald-600' : (stCode === 'unchecked' ? 'text-slate-500' : 'text-rose-600');
        return `
            <div class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-white border-slate-300 text-xs hover:border-slate-400 transition">
                <span class="w-2 h-2 rounded-full ${dot}"></span>
                <span class="font-semibold text-slate-800">${escapeHtml(m.name)}</span>
                <span class="text-[10px] ${stDot} font-mono">(${escapeHtml(stLabel)})</span>
                <button onclick="toggleSingleModelEnabled('${m.id}', false)" title="Вимкнути" class="text-slate-400 hover:text-rose-600 font-bold ml-1 transition">
                    &times;
                </button>
            </div>
        `;
    }).join('');
}

function renderProvidersTilesGrid() {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;
    const container = document.getElementById('providersTilesGrid');
    if (!container) return;

    const provList = meta.providers || [];
    const probeProvs = meta.probe?.providers || {};

    updateActiveModelsCounter();
    renderActiveModelsSummary();

    const provShortCode = {
        gemini: 'GM',
        groq: 'GQ',
        openrouter: 'OR',
        openai: 'OA',
        anthropic: 'AN',
        ollama: 'OL',
        custom: 'CU',
    };

    container.innerHTML = provList.map(p => {
        const pModels = meta.models.filter(m => m.provider === p.id);
        const enabledCount = pModels.filter(m => m.enabled).length;
        const pr = probeProvs[p.id] || {};
        const shortTag = provShortCode[p.id] || 'API';

        const stCode = pr.status || p.status || (p.configured ? 'unchecked' : 'no_key');
        const stLabel = pr.status_label || p.status_label || 'Не перевірено';
        const statusBadge = getStatusBadgeHtml(stCode, stLabel);
        const checkedAgo = pr.checked_ago_uk || p.checked_ago_uk || 'Не перевірено';

        const keyDisplay = p.is_local
            ? `<span class="text-slate-600 font-mono text-[11px]">${escapeHtml(p.key_masked || 'http://localhost:11434')}</span>`
            : (p.configured
                ? `<span class="text-slate-800 font-mono text-[11px]">${escapeHtml(p.key_masked || 'Налаштовано')}</span>`
                : `<span class="text-slate-400 font-mono text-[11px]">Ключ не задано</span>`);

        return `
            <div class="rounded-xl border border-slate-200 bg-white p-4 flex flex-col justify-between space-y-3 hover:border-slate-400 transition">
                <div class="space-y-2.5">
                    <div class="flex items-start justify-between gap-2 border-b border-slate-100 pb-2.5">
                        <div class="flex items-center gap-2.5">
                            <span class="w-8 h-8 rounded-lg bg-slate-100 border border-slate-200 text-slate-700 flex items-center justify-center font-bold text-xs font-mono">${shortTag}</span>
                            <div>
                                <h3 class="font-bold text-slate-900 text-sm leading-tight">${escapeHtml(p.name)}</h3>
                                <div class="text-[10px] text-slate-400 font-mono">${escapeHtml(p.env_key || '')}</div>
                            </div>
                        </div>
                        <div>${statusBadge}</div>
                    </div>

                    <div class="space-y-1.5 text-xs">
                        <div class="flex items-center justify-between text-slate-600">
                            <span>Ключ / адреса:</span>
                            <span>${keyDisplay}</span>
                        </div>
                        <div class="flex items-center justify-between text-slate-600">
                            <span>Моделі:</span>
                            <span class="font-semibold text-slate-900">Увімкнено ${enabledCount} з ${pModels.length}</span>
                        </div>
                        <div class="flex items-center justify-between text-slate-500 text-[11px]">
                            <span>Остання перевірка:</span>
                            <span class="font-mono">${escapeHtml(checkedAgo)}</span>
                        </div>
                    </div>
                </div>

                <div class="pt-2 border-t border-slate-100">
                    <button onclick="openProviderModelsModal('${p.id}')" class="w-full py-2 px-3 rounded-lg bg-slate-900 hover:bg-slate-800 text-white font-semibold text-xs flex items-center justify-between transition">
                        <span>Керувати моделями</span>
                        <span class="text-slate-400 font-mono">&rarr;</span>
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function filterProviderModalModels() {
    if (APP_STATE.activeModalProviderId) {
        renderProviderModalListContent(APP_STATE.activeModalProviderId);
    }
}

function openProviderModelsModal(providerId) {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;

    const isSameProvider = APP_STATE.activeModalProviderId === providerId;
    APP_STATE.activeModalProviderId = providerId;

    const modal = document.getElementById('providerModelsModal');
    if (!modal) return;

    const searchInp = document.getElementById('provModalSearchInput');
    if (searchInp && !isSameProvider) {
        searchInp.value = '';
    }

    renderProviderModalListContent(providerId);

    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

function describeStatusSource(m) {
    if (!m.checked_at) return 'Не перевірено';
    const how = { ping: 'перевірено запитом', list: 'за списком провайдера', provider: 'за станом провайдера' }[m.check_kind] || '';
    const parts = [m.checked_ago_uk, how];
    if (m.is_stale) parts.push('застаріло, варто оновити');
    return parts.filter(Boolean).join(', ');
}

function renderProviderModalListContent(providerId) {
    const activeProvId = providerId || APP_STATE.activeModalProviderId;
    if (!activeProvId) return;
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;

    const p = (meta.providers || []).find(x => x.id === activeProvId) || { id: activeProvId, name: activeProvId, env_key: activeProvId };
    const allProviderModels = meta.models.filter(m => m.provider === activeProvId);
    const searchQuery = (document.getElementById('provModalSearchInput')?.value || '').trim().toLowerCase();
    const pModels = searchQuery
        ? allProviderModels.filter(m =>
            (m.name || '').toLowerCase().includes(searchQuery) ||
            (m.id || '').toLowerCase().includes(searchQuery) ||
            (m.api_model_name || '').toLowerCase().includes(searchQuery)
        )
        : allProviderModels;

    const probe = meta.probe || {};
    const pr = (probe.providers || {})[activeProvId] || {};

    const provShortCode = {
        gemini: 'GM',
        groq: 'GQ',
        openrouter: 'OR',
        openai: 'OA',
        anthropic: 'AN',
        ollama: 'OL',
        custom: 'CU',
    };

    const iconEl = document.getElementById('provModalIcon');
    const titleEl = document.getElementById('provModalTitle');
    const subtitleEl = document.getElementById('provModalSubtitle');
    const badgeEl = document.getElementById('provModalCountBadge');
    const listEl = document.getElementById('provModalModelsList');
    const routeCtrlEl = document.getElementById('provModalRouteControl');

    if (iconEl) iconEl.textContent = provShortCode[activeProvId] || 'API';
    if (titleEl) titleEl.textContent = p.name;
    if (subtitleEl) {
        const stLabel = pr.status_label || p.status_label || 'Не перевірено';
        const checkedAgo = pr.checked_ago_uk || p.checked_ago_uk || 'Не перевірено';
        subtitleEl.textContent = `${p.env_key || ''} | Статус: ${stLabel} | ${checkedAgo}`;
    }

    const enabledCount = allProviderModels.filter(m => m.enabled).length;
    if (badgeEl) badgeEl.textContent = `Увімкнено ${enabledCount} з ${allProviderModels.length}`;

    // Єдиний перемикач маршруту на рівні провайдера всередині модального вікна (лише коли OpenRouter налаштовано і провайдер має еквіваленти)
    const hasOpenRouterKey = Boolean(meta.settings?.key_status?.openrouter_configured);
    const canRouteProvider = hasOpenRouterKey && ['groq', 'openai', 'anthropic'].includes(activeProvId) && allProviderModels.some(m => m.can_openrouter);
    if (routeCtrlEl) {
        if (canRouteProvider) {
            const anyOpenRouter = allProviderModels.some(m => m.can_openrouter && m.route === 'openrouter');
            routeCtrlEl.innerHTML = `
                <div class="inline-flex items-center gap-1.5 bg-slate-800 border border-slate-700 rounded-lg p-1 text-[11px]">
                    <span class="text-slate-300 px-1.5">Маршрут:</span>
                    <button onclick="toggleProviderRoute('${activeProvId}', 'native')" class="px-2 py-0.5 rounded font-semibold transition ${!anyOpenRouter ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white'}">
                        Прямий API
                    </button>
                    <button onclick="toggleProviderRoute('${activeProvId}', 'openrouter')" class="px-2 py-0.5 rounded font-semibold transition ${anyOpenRouter ? 'bg-purple-600 text-white' : 'text-slate-300 hover:text-white'}">
                        OpenRouter
                    </button>
                </div>
            `;
        } else {
            routeCtrlEl.innerHTML = '';
        }
    }

    if (pModels.length === 0) {
        listEl.innerHTML = `<div class="p-6 text-center text-slate-400 italic">Моделей за цим запитом не знайдено.</div>`;
        return;
    }

    listEl.innerHTML = pModels.map(m => {
        const statusCode = m.status_code || 'unchecked';
        const statusLabel = m.status_label || 'Не перевірено';
        const statusBadge = getStatusBadgeHtml(statusCode, statusLabel);
        const checkedAgo = describeStatusSource(m);

        const inCtx = formatTokenNum(m.context_in || m.input_limit);
        const priceKnown = Boolean(m.price_known) && m.cost_per_1m_input_usd != null && m.cost_per_1m_output_usd != null;
        const priceHtml = priceKnown
            ? `<span class="font-semibold text-slate-800">Вхід: $${m.cost_per_1m_input_usd} | Вихід: $${m.cost_per_1m_output_usd}</span>`
            : `<span class="text-amber-700 font-semibold">ціна не задана</span>`;
        const isChecked = Boolean(m.enabled);
        const newBadge = m.is_new
            ? `<span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 border border-emerald-300 text-[9px] font-bold">нова</span>`
            : '';
        const customBadge = m.is_custom
            ? `<span class="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-800 border border-indigo-200 text-[9px] font-semibold">власна</span>`
            : '';

        const lim = m.limits || {};
        const rpmVal = lim.rpm ?? m.rpm ?? '-';
        const rpdVal = lim.rpd ?? m.rpd ?? '-';
        const safeId = escapeHtml(m.id);

        return `
            <div class="p-3.5 rounded-xl border ${isChecked ? 'border-blue-400 bg-white' : 'border-slate-200 bg-slate-50/60'} hover:border-blue-400 transition">
                <div class="flex items-start justify-between gap-3">
                    <label class="flex items-start gap-2.5 cursor-pointer flex-1">
                        <input type="checkbox" ${isChecked ? 'checked' : ''} onchange="toggleSingleModelEnabled('${safeId}', this.checked)" class="w-4 h-4 mt-0.5 rounded text-blue-600 focus:ring-blue-500 cursor-pointer">
                        <div>
                            <div class="font-bold text-slate-900 text-xs flex flex-wrap items-center gap-1.5">
                                <span>${escapeHtml(m.name)}</span>
                                <span class="text-[10px] font-mono font-normal text-slate-400">(${escapeHtml(m.api_model_name || m.id)})</span>
                                ${newBadge}
                                ${customBadge}
                            </div>
                            <div class="text-[10px] text-slate-500 mt-0.5">
                                ${escapeHtml(checkedAgo)}${m.probe_reason ? ' | ' + escapeHtml(m.probe_reason) : ''}
                            </div>
                        </div>
                    </label>
                    <div class="flex items-center gap-1.5 shrink-0">
                        ${statusBadge}
                        <button onclick="probeSingleModelStatus('${safeId}')" title="Перевірити модель" class="px-2 py-0.5 rounded border border-slate-300 bg-white hover:bg-slate-100 text-slate-700 text-[10px] font-semibold transition">
                            Оновити
                        </button>
                        ${m.is_custom ? `<button onclick="deleteCustomModelEntry('${safeId}')" title="Видалити модель" class="px-2 py-0.5 rounded border border-rose-200 bg-rose-50 hover:bg-rose-100 text-rose-700 text-[10px] font-semibold">&times;</button>` : ''}
                    </div>
                </div>

                <div class="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[10px] mt-2.5 pt-2 border-t border-slate-100">
                    <div class="p-1.5 rounded bg-slate-50 border border-slate-200/80">
                        <div class="text-slate-500 font-medium text-[9px]">Контекстне вікно:</div>
                        <div class="font-semibold text-slate-800 mt-0.5">${inCtx} токенів</div>
                    </div>

                    <div class="p-1.5 rounded bg-slate-50 border border-slate-200/80 flex items-center justify-between gap-1">
                        <div>
                            <div class="text-slate-500 font-medium text-[9px]">Тариф ($ / 1M токенів):</div>
                            <div class="mt-0.5">${priceHtml}</div>
                        </div>
                        <button onclick="promptEditModelPricingAndLimits('${safeId}')" class="px-1.5 py-0.5 rounded bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 text-[9px] font-semibold">
                            Змінити
                        </button>
                    </div>

                    <div class="p-1.5 rounded bg-slate-50 border border-slate-200/80 flex items-center justify-between gap-1">
                        <div>
                            <div class="text-slate-500 font-medium text-[9px]">Ліміти (RPM / RPD):</div>
                            <div class="font-semibold text-slate-800 mt-0.5">${rpmVal} RPM | ${rpdVal} RPD</div>
                        </div>
                        <button onclick="promptEditModelPricingAndLimits('${safeId}')" class="px-1.5 py-0.5 rounded bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 text-[9px] font-semibold">
                            Ліміти
                        </button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function closeProviderModelsModal() {
    const modal = document.getElementById('providerModelsModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
    renderProvidersTilesGrid();
    renderActiveModelsSummary();
}

async function toggleSingleModelEnabled(modelId, isChecked) {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;

    const m = meta.models.find(x => x.id === modelId);
    if (m) m.enabled = isChecked;

    const enabled_models = meta.models.filter(x => x.enabled).map(x => x.id);
    updateActiveModelsCounter();
    renderActiveModelsSummary();
    renderEnvKeyStatus();
    populateControls();

    if (APP_STATE.activeModalProviderId) {
        renderProviderModalListContent(APP_STATE.activeModalProviderId);
    }

    try {
        await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled_models })
        });
    } catch (e) {
        console.error('Помилка збереження списку моделей:', e);
    }
}

async function toggleAllModelsForCurrentProvider(enable) {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models || !APP_STATE.activeModalProviderId) return;

    const searchQuery = (document.getElementById('provModalSearchInput')?.value || '').trim().toLowerCase();
    meta.models.forEach(m => {
        if (m.provider === APP_STATE.activeModalProviderId) {
            if (!searchQuery ||
                (m.name || '').toLowerCase().includes(searchQuery) ||
                (m.id || '').toLowerCase().includes(searchQuery) ||
                (m.api_model_name || '').toLowerCase().includes(searchQuery)) {
                m.enabled = enable;
            }
        }
    });

    const enabled_models = meta.models.filter(x => x.enabled).map(x => x.id);
    updateActiveModelsCounter();
    renderActiveModelsSummary();
    renderEnvKeyStatus();
    populateControls();
    renderProviderModalListContent(APP_STATE.activeModalProviderId);

    try {
        await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled_models })
        });
    } catch (e) {
        console.error('Помилка збереження списку моделей:', e);
    }
}

async function toggleProviderRoute(providerId, targetRoute) {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;

    const affected = meta.models.filter(m => m.provider === providerId && m.can_openrouter);
    const routes = { ...(meta.settings?.model_routes || {}) };
    affected.forEach(m => {
        routes[m.id] = targetRoute;
    });

    try {
        const resp = await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_routes: routes })
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (e) {
        setProbeBanner(`Не вдалося змінити маршрут: ${e.message || e}`, true);
        return;
    }
    // Перемикач реагує одразу; повна перевірка йде у фоні, бо може тривати хвилину
    affected.forEach(m => {
        m.route = targetRoute === 'openrouter' ? 'openrouter' : 'direct';
    });
    if (meta.settings) meta.settings.model_routes = routes;
    renderProviderModalListContent(providerId);
    probeApiLimits({ silent: false });
}

async function quickSelectSettingsModels(mode) {
    const meta = APP_STATE.metadata;
    if (!meta || !meta.models) return;

    const works = m => m.status_code === 'ok';
    const profiles = {
        all: m => !m.is_dynamic,
        working: m => works(m) && !m.is_dynamic,
        free: m => works(m) && m.is_free_tier,
        paid: m => works(m) && !m.is_free_tier,
        local: m => m.provider === 'ollama',
        custom: m => m.is_custom,
    };
    const pick = profiles[mode];
    if (!pick) return;
    if (!meta.models.some(pick)) {
        setProbeBanner('У цьому наборі зараз немає жодної моделі. Спочатку оновіть статуси або додайте модель.', true);
        return;
    }
    meta.models.forEach(m => {
        m.enabled = pick(m);
    });

    const enabled_models = meta.models.filter(x => x.enabled).map(x => x.id);
    updateActiveModelsCounter();
    renderProvidersTilesGrid();
    renderActiveModelsSummary();
    renderEnvKeyStatus();
    populateControls();

    if (APP_STATE.activeModalProviderId) {
        renderProviderModalListContent(APP_STATE.activeModalProviderId);
    }

    try {
        await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled_models })
        });
    } catch (e) {
        console.error('Помилка збереження:', e);
    }
}
