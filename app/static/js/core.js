let APP_STATE = {
    metadata: null,
    methodology: null,
    queries: [],
    prompts: [],
    runs: [],
    currentRunId: null,
    pendingDeleteRunId: null,
    selectedAnalyticsRunIds: [],
    analyticsAvailableFilters: { models: [], prompts: [], categories: [] },
    lastLogId: 0,
    exportCache: { markdown: '', csv_models: '', latex: '' },
    charts: {
        modelsBar: null,
        nistRadar: null,
        promptsLine: null,
        latencyScatter: null,
    }
};

const MODEL_COLORS = [
    { bg: 'rgba(37, 99, 235, 0.78)', border: '#1d4ed8' },
    { bg: 'rgba(16, 185, 129, 0.78)', border: '#047857' },
    { bg: 'rgba(139, 92, 246, 0.78)', border: '#6d28d9' },
    { bg: 'rgba(245, 158, 11, 0.78)', border: '#b45309' },
    { bg: 'rgba(225, 29, 72, 0.78)', border: '#be123c' },
    { bg: 'rgba(14, 165, 233, 0.78)', border: '#0369a1' },
];

document.addEventListener('DOMContentLoaded', async () => {
    await initApplication();
    setInterval(pollLogsAndStatus, 600);
});

async function initApplication() {
    await refreshMetadataAndData();
    await pollLogsAndStatus();
    renderHome();
}

async function refreshMetadataAndData() {
    const [metaRes, methRes, queriesRes, promptsRes, runsRes] = await Promise.all([
        fetch('/api/metadata').then(r => r.json()),
        fetch('/api/methodology').then(r => r.json()).catch(() => null),
        fetch('/api/queries').then(r => r.json()),
        fetch('/api/prompts').then(r => r.json()),
        fetch('/api/experiments').then(r => r.json()),
    ]);

    APP_STATE.metadata = metaRes;
    APP_STATE.methodology = methRes;
    APP_STATE.queries = queriesRes.items || [];
    APP_STATE.prompts = promptsRes.items || [];
    APP_STATE.runs = runsRes.runs || [];

    if (APP_STATE.runs.length > 0) {
        const validIds = new Set(APP_STATE.runs.map(r => r.id));
        if (!APP_STATE.currentRunId || !validIds.has(APP_STATE.currentRunId)) {
            APP_STATE.currentRunId = APP_STATE.runs[0].id;
        }
        APP_STATE.selectedAnalyticsRunIds = APP_STATE.selectedAnalyticsRunIds.filter(id => validIds.has(id));
        if (APP_STATE.selectedAnalyticsRunIds.length === 0) {
            APP_STATE.selectedAnalyticsRunIds = [APP_STATE.runs[0].id];
        }
    } else {
        APP_STATE.currentRunId = null;
        APP_STATE.selectedAnalyticsRunIds = [];
    }

    populateControls();
    renderEnvKeyStatus();
    renderProvidersTilesGrid();
    renderActiveModelsSummary();
    renderPromptCards();
    renderDatasetTable();
    renderExperimentRunsList();
    renderMethodologySection();
    updateAnalyticsVisibility();
    updateBatchExperimentEstimate();
}

function switchTab(tabId) {
    const tabs = ['home', 'sandbox', 'experiments', 'analytics', 'dataset', 'settings', 'logs'];
    tabs.forEach(t => {
        const sec = document.getElementById(`tab-${t}`);
        const btn = document.getElementById(`tabBtn-${t}`);
        if (sec) sec.classList.toggle('hidden', t !== tabId);
        if (btn) btn.classList.toggle('active', t === tabId);
    });

    const liveBanner = document.getElementById('liveStatusBanner');
    if (liveBanner) {
        const showBanner = (tabId === 'sandbox' || tabId === 'experiments');
        liveBanner.classList.toggle('hidden', !showBanner);
    }
    if (tabId === 'experiments') {
        updateBatchExperimentEstimate();
    }
    if (tabId === 'home') {
        renderHome();
    }
}

let _wasBusyLastPoll = false;
let _lastRefreshedProgress = -1;
let _isPollingLogs = false;
const _seenLogIds = new Set();

async function pollLogsAndStatus() {
    if (_isPollingLogs) return;
    _isPollingLogs = true;
    try {
        const res = await fetch(`/api/logs?since_id=${APP_STATE.lastLogId}`).then(r => r.json());
        if (res.status) {
            renderLiveStatusHeader(res.status);
            await pollActivePrompt(res.status.is_busy);

            const isBusy = Boolean(res.status.is_busy);
            const curProg = Number(res.status.progress_current || 0);
            if ((_wasBusyLastPoll && !isBusy) || (isBusy && curProg !== _lastRefreshedProgress)) {
                _lastRefreshedProgress = curProg;
                try {
                    const runsRes = await fetch('/api/experiments').then(r => r.json());
                    APP_STATE.runs = runsRes.runs || [];
                    renderExperimentRunsList();
                    if (!isBusy) {
                        updateAnalyticsVisibility();
                    }
                } catch (_) {}
            }
            _wasBusyLastPoll = isBusy;
        }
        if (res.logs && res.logs.length > 0) {
            appendLogsToTerminal(res.logs);
            if (res.last_log_id != null && res.last_log_id > APP_STATE.lastLogId) {
                APP_STATE.lastLogId = res.last_log_id;
            }
        }
    } catch (e) {
        // Ігноруємо тимчасові збої мережі при опитуванні
    } finally {
        _isPollingLogs = false;
    }
}

let _activePromptId = null;

async function pollActivePrompt(isBusy) {
    const modal = document.getElementById('interactiveDecisionModal');
    if (!isBusy) {
        if (modal && !modal.classList.contains('hidden')) {
            modal.classList.add('hidden');
            modal.classList.remove('flex');
        }
        _activePromptId = null;
        return;
    }

    try {
        const p = await fetch('/api/experiments/active-prompt').then(r => r.json());
        if (!modal) return;

        if (p && p.prompt_id) {
            if (_activePromptId === p.prompt_id) {
                return;
            }
            _activePromptId = p.prompt_id;

            document.getElementById('interactiveModalTitle').textContent = p.title || 'Увага: потрібне рішення';
            document.getElementById('interactiveModalMessage').textContent = p.message || '';
            const detailsEl = document.getElementById('interactiveModalDetails');
            if (p.context && Object.keys(p.context).length > 0) {
                detailsEl.textContent = JSON.stringify(p.context, null, 2);
                detailsEl.classList.remove('hidden');
            } else {
                detailsEl.classList.add('hidden');
            }

            const btnsContainer = document.getElementById('interactiveModalButtons');
            btnsContainer.innerHTML = (p.options || []).map(opt => {
                const isPrimary = Boolean(opt.primary);
                const btnCls = isPrimary
                    ? 'bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 px-4 rounded-xl shadow transition'
                    : 'bg-slate-100 hover:bg-slate-200 text-slate-800 font-semibold py-2 px-4 rounded-xl border border-slate-300 transition';
                const targetModel = opt.target_model ? `'${opt.target_model}'` : 'null';
                const targetRoute = opt.target_route ? `'${opt.target_route}'` : 'null';
                return `
                    <button onclick="handleInteractiveDecision('${p.prompt_id}', '${opt.action}', ${targetModel}, ${targetRoute})" class="${btnCls} text-left flex items-center justify-between">
                        <span>${escapeHtml(opt.label)}</span>
                    </button>
                `;
            }).join('');

            modal.classList.remove('hidden');
            modal.classList.add('flex');
        } else {
            if (_activePromptId) {
                _activePromptId = null;
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }
        }
    } catch (e) {
        // Ігноруємо помилки мережі при запиті активного промпта
    }
}

async function handleInteractiveDecision(promptId, action, targetModel, targetRoute) {
    const data = {};
    if (targetModel) data.target_model = targetModel;
    if (targetRoute) data.target_route = targetRoute;

    try {
        await fetch('/api/experiments/resolve-prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt_id: promptId, action, data })
        });
    } catch (e) {
        console.error(e);
    }
    const modal = document.getElementById('interactiveDecisionModal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
    _activePromptId = null;
    await pollLogsAndStatus();
}

function renderLiveStatusHeader(st) {
    const dot = document.getElementById('liveStatusDot');
    if (st.is_busy) {
        dot.className = 'w-2 h-2 rounded-full bg-amber-400 animate-ping inline-block';
    } else {
        dot.className = 'w-2 h-2 rounded-full bg-emerald-400 inline-block';
    }

    document.getElementById('liveStageText').textContent = st.current_stage || 'Очікування';
    document.getElementById('liveActiveModel').textContent = st.active_model || '-';
    document.getElementById('liveActivePrompt').textContent = st.active_prompt || '-';
    document.getElementById('liveActiveQuery').textContent = st.active_query_code || '-';

    const progWrap = document.getElementById('liveProgressBarWrap');
    if ((st.operation_type === 'batch_experiment' || st.operation_type === 'judge_evaluation') && st.progress_total > 0) {
        progWrap.classList.remove('hidden');
        const opPrefix = st.operation_type === 'judge_evaluation' ? 'Оцінювання суддею' : 'Виконання серії';
        document.getElementById('liveProgressLabel').textContent =
            `${opPrefix}: ${st.progress_current} / ${st.progress_total} (${st.progress_pct}%)`;
        document.getElementById('liveProgressBarFill').style.width = `${st.progress_pct}%`;
    } else {
        progWrap.classList.add('hidden');
    }
}

function appendLogsToTerminal(logs) {
    const term = document.getElementById('liveLogTerminal');
    if (term && APP_STATE.lastLogId === 0 && _seenLogIds.size === 0) {
        term.innerHTML = '';
    }

    const levelColors = {
        INFO: 'text-slate-300',
        STEP: 'text-sky-300 font-semibold',
        LLM_REQ: 'text-amber-300',
        LLM_RES: 'text-emerald-300 font-semibold',
        EVAL: 'text-indigo-300',
        WARN: 'text-orange-400 font-semibold',
        ERROR: 'text-rose-400 font-bold',
    };

    if (term) {
        logs.forEach(item => {
            if (item.id != null) {
                if (_seenLogIds.has(item.id)) return;
                _seenLogIds.add(item.id);
            }
            const colorCls = levelColors[item.level] || 'text-slate-300';
            const line = document.createElement('div');
            line.className = `${colorCls} break-words`;
            line.innerHTML = `<span class="text-slate-500">[${item.timestamp}]</span> <span class="text-slate-400">[${item.stage}]</span> ${escapeHtml(item.message)}`;
            term.appendChild(line);
        });

        const badge = document.getElementById('navLogsCountBadge');
        if (badge) {
            badge.textContent = String(term.children.length);
        }

        term.scrollTop = term.scrollHeight;
    }
}

async function clearServerLogs() {
    await fetch('/api/logs/clear', { method: 'POST' });
    APP_STATE.lastLogId = 0;
    _seenLogIds.clear();
    document.getElementById('liveLogTerminal').innerHTML = '';
    const badge = document.getElementById('navLogsCountBadge');
    if (badge) badge.textContent = '0';
    await pollLogsAndStatus();
}

function copyAllLogs() {
    const term = document.getElementById('liveLogTerminal');
    if (term) {
        navigator.clipboard.writeText(term.innerText || '');
    }
}

async function cancelBatchExperiment() {
    await fetch('/api/experiments/cancel', { method: 'POST' });
}

function escapeHtml(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function formatKeywordEntry(entry) {
    if (!entry) return '';
    if (typeof entry === 'string') return entry;
    if (typeof entry === 'object') {
        const id = entry.id || '';
        const variants = Array.isArray(entry.any_of) ? entry.any_of.join(' / ') : '';
        return id && variants ? `${id} (${variants})` : (id || variants);
    }
    return String(entry);
}

function getRunStatusBadgeHtml(run) {
    const status = run.status || 'completed';
    const badges = [];

    if (status === 'completed') {
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">Завершено (${run.completed_items}/${run.total_items})</span>`);
    } else if (status === 'paused_quota') {
        const resumeNote = run.summary?.resume_after ? ` (відновлення: ${run.summary.resume_after})` : '';
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-900 border border-amber-400">Пауза: денна квота (${run.completed_items}/${run.total_items})${escapeHtml(resumeNote)}</span>`);
    } else if (status === 'incomplete') {
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300">Неповна (${run.completed_items}/${run.total_items})</span>`);
    } else if (status === 'cancelled') {
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-orange-100 text-orange-800 border border-orange-300">Скасовано (${run.completed_items}/${run.total_items})</span>`);
    } else if (status === 'failed') {
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-100 text-rose-800 border border-rose-300">Помилка (${run.completed_items}/${run.total_items})</span>`);
    } else {
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-100 text-blue-800">${escapeHtml(status)}</span>`);
    }

    if (run.errors_count > 0) {
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-50 text-rose-700 border border-rose-200">Помилок API: ${run.errors_count}</span>`);
    }
    const judgedCount = Number(run.judged_items_count || 0);
    const compCount = Number(run.completed_items || 0);
    if (compCount > 0) {
        const judgeCls = judgedCount >= compCount
            ? 'bg-indigo-100 text-indigo-800 border-indigo-300'
            : (judgedCount > 0 ? 'bg-amber-50 text-amber-800 border-amber-300' : 'bg-slate-100 text-slate-600 border-slate-300');
        badges.push(`<span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${judgeCls} border">Суддя: ${judgedCount}/${compCount}</span>`);
    }
    return badges.join(' ');
}

function getStatusBadgeHtml(statusCode, statusLabel) {
    const code = statusCode || 'unchecked';
    const label = statusLabel || {
        ok: 'Працює',
        offline: "Немає зв'язку",
        rate_limited: 'Ліміт запитів',
        daily_quota: 'Денний ліміт вичерпано',
        no_credit: 'Нульовий баланс',
        invalid_key: 'Невірний ключ',
        no_key: 'Ключ не задано',
        model_missing: 'Модель недоступна',
        unchecked: 'Не перевірено',
    }[code] || 'Не перевірено';

    const styles = {
        ok: 'bg-emerald-100 text-emerald-800 border-emerald-300',
        no_credit: 'bg-rose-100 text-rose-800 border-rose-300',
        rate_limited: 'bg-amber-100 text-amber-900 border-amber-300',
        daily_quota: 'bg-orange-100 text-orange-900 border-orange-300',
        offline: 'bg-slate-100 text-slate-600 border-slate-300',
        no_key: 'bg-slate-100 text-slate-500 border-slate-300',
        invalid_key: 'bg-rose-100 text-rose-800 border-rose-300',
        model_missing: 'bg-amber-50 text-amber-800 border-amber-200',
        unchecked: 'bg-slate-100 text-slate-600 border-slate-300',
    };
    const dotColors = {
        ok: 'bg-emerald-500',
        no_credit: 'bg-rose-500',
        rate_limited: 'bg-amber-500',
        daily_quota: 'bg-orange-500',
        offline: 'bg-slate-400',
        no_key: 'bg-slate-400',
        invalid_key: 'bg-rose-500',
        model_missing: 'bg-amber-400',
        unchecked: 'bg-slate-400',
    };
    const cls = styles[code] || 'bg-slate-100 text-slate-600 border-slate-300';
    const dot = dotColors[code] || 'bg-slate-400';
    return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-semibold border ${cls}">
        <span class="w-1.5 h-1.5 rounded-full ${dot}"></span>
        <span>${escapeHtml(label)}</span>
    </span>`;
}
