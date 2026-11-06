const HOME_TASKS = [
    { role: 'Співробітник підтримки', title: 'Відповісти на запит клієнта', text: 'Знайти відповідь у базі знань і дізнатися, чи потрібен оператор.', tab: 'sandbox' },
    { role: 'Керівник підтримки', title: 'Обрати модель для впровадження', text: 'Рекомендація з урахуванням якості, ціни та безпеки.', tab: 'analytics#results-models' },
    { role: 'Дослідник', title: 'Перевірити гіпотези на даних', text: 'Запустити серію запитів по сферах і побачити, що вона підтверджує.', tab: 'experiments' },
    { role: 'Інженер впровадження', title: 'Додати власну модель', text: 'Підключити будь-який сервіс із OpenAI-сумісним API та перевірити його.', tab: 'settings' },
    { role: 'Інженер впровадження', title: 'Додати сферу або запити', text: 'Завантажити FAQ або типові запити та перевірити, як модель на них відповідає.', tab: 'dataset' },
    { role: 'Студент або викладач', title: 'Відтворити результати й експортувати їх', text: 'Методика, таблиці та вибірка для ручної перевірки.', tab: 'analytics#results-engineer' },
];

const HOME_TABS = [
    { tab: 'sandbox', name: 'Помічник', text: 'Відповідає на запит клієнта: знаходить фрагменти бази знань, формує відповідь і підказує, чи потрібен оператор.' },
    { tab: 'experiments', name: 'Експерименти', text: 'Запускає серії запитів по сферах, моделях, промптах і режимах контексту. Показує прогноз вартості й часу, працює у фоні, дозволяє продовжити перервану серію.' },
    { tab: 'analytics', name: 'Результати', text: 'Вердикти за гіпотезами, рекомендація моделі, каскад, статистика, експорт таблиць і вибірка для ручної оцінки.' },
    { tab: 'dataset', name: 'Дані', text: 'Тестові запити, база знань, шаблони промптів, чернетки на перевірку, завантаження FAQ і створення нових сфер.' },
    { tab: 'settings', name: 'Моделі', text: 'Провайдери та ключі, пошук доступних моделей, перевірка кожної моделі окремо, власні моделі, ціни й ліміти.' },
    { tab: 'logs', name: 'Журнал', text: 'Події та помилки під час роботи: запити до моделей, статуси, перевірки.' },
];

const HOME_HYPOTHESIS_INFO = {
    H1: {
        question: 'Чи потрібно обирати модель окремо для кожної сфери, чи одна модель підходить усім?',
        compare: 'Моделі між собою в межах кожної сфери: фінтех, ІТ-підтримка, електронна комерція, телеком.',
        proof: c => `Найкраща модель різна в різних сферах, а розрив між переможцем сфери та загальним лідером не менший за ${c.practical_gap_pts} п.п. Для порівняння в сфері потрібно не менше ${c.min_cell_queries} запитів на модель.`,
        benefit: 'Якщо підтверджується, рекомендацію «яку модель брати» треба давати окремо для вашої сфери. Якщо ні, достатньо однієї моделі для всіх.',
        preset: 'h1',
        see: 'Результати, розділ «Чи залежить найкраща модель від сфери»',
        seeTarget: 'analytics#results-domains',
    },
    H2: {
        question: 'Що дає більше: кращий промпт і база знань чи дорожча модель?',
        compare: 'Розбіг якості між стратегіями промпту та між режимами контексту проти розбігу між моделями.',
        proof: () => 'Розбіг якості від промпту або від контексту не менший за розбіг від вибору моделі.',
        benefit: 'Якщо підтверджується, дешевше покращити інструкцію або базу знань, ніж переходити на дорожчу модель.',
        preset: 'h2',
        see: 'Результати, розділ «Чи важливі промпт і контекст»',
        seeTarget: 'analytics#results-context',
    },
    H3: {
        question: 'Чи можна більшість типових запитів віддати дешевій моделі без втрати безпеки?',
        compare: 'Лідера за якістю з дешевшими моделями: різниця в якості, пропущені передачі оператору, ціна.',
        proof: c => `Дешевша модель поступається лідеру не більш ніж на ${c.quality_tolerance_pts} п.п. якості, не пропускає більше обов'язкових передач оператору і коштує менше.`,
        benefit: 'Якщо підтверджується, прості запити можна віддати дешевій моделі, а складні й ризикові передавати сильній моделі або оператору.',
        preset: 'h3',
        see: 'Результати, розділ «Чи достатньо дешевої моделі»',
        seeTarget: 'analytics#results-cascade',
    },
};

// Ціль виду «вкладка» або «вкладка#розділ»: для «Результатів» відкривається потрібний розділ сторінки
function homeGo(target) {
    const [tab, section] = target.split('#');
    if (tab === 'analytics' && section) openResultsSection(section);
    else switchTab(tab);
}

function homeScrollTo(id) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function homeOpenPreset(tab, preset) {
    switchTab(tab);
    if (preset && typeof applyExperimentPreset === 'function') applyExperimentPreset(preset);
}

function homeHypothesisHtml(id, verdict, criteria) {
    const info = HOME_HYPOTHESIS_INFO[id];
    const style = VERDICT_CLASSES[verdict?.verdict] || VERDICT_CLASSES.insufficient;
    const status = verdict
        ? `<div class="mt-3 border-l-4 pl-3 ${style.card}">
               <div class="text-sm font-semibold ${style.label}">${escapeHtml(verdict.verdict_label_uk)}</div>
               <p class="text-sm text-slate-700">${escapeHtml(verdict.summary_uk)}</p>
               ${verdict.preliminary_uk ? `<p class="text-xs text-slate-500 mt-1">Попередньо, це ще не висновок: ${escapeHtml(verdict.preliminary_uk)}</p>` : ''}
               ${verdict.missing_uk ? `<p class="text-xs text-slate-500 mt-1">${escapeHtml(verdict.missing_uk)}</p>` : ''}
           </div>`
        : `<div class="mt-3 border-l-4 pl-3 ${VERDICT_CLASSES.insufficient.card}">
               <div class="text-sm font-semibold ${VERDICT_CLASSES.insufficient.label}">Замало даних</div>
               <p class="text-sm text-slate-700">Завершених серій ще немає. Запустіть серію для цієї гіпотези.</p>
           </div>`;
    const row = (label, text) => `<div><dt class="text-xs text-slate-500">${label}</dt><dd class="text-sm text-slate-800">${escapeHtml(text)}</dd></div>`;
    return `<div class="card-box p-5">
        <div class="flex flex-wrap items-baseline gap-x-3">
            <span class="text-xs font-semibold text-slate-500">${id}</span>
            <span class="text-base font-semibold text-slate-900">${escapeHtml(HOME_HYPOTHESES_TITLES[id])}</span>
        </div>
        <dl class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 mt-3">
            ${row('Питання', info.question)}
            ${row('Що порівнюється', info.compare)}
            ${row('Що вважається підтвердженням', info.proof(criteria))}
            ${row('Що це дає на практиці', info.benefit)}
        </dl>
        ${status}
        <div class="flex flex-wrap items-center gap-3 mt-4 text-sm">
            <button onclick="homeOpenPreset('experiments', '${info.preset}')" class="px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50">Налаштувати серію для ${id}</button>
            <button onclick="homeGo('${info.seeTarget}')" class="px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50">Де дивитися: ${escapeHtml(info.see)}</button>
        </div>
    </div>`;
}

const HOME_HYPOTHESES_TITLES = {
    H1: 'Найкраща модель залежить від предметної сфери',
    H2: 'Промпт і контекст впливають не менше, ніж вибір моделі',
    H3: 'Для більшості запитів достатньо дешевої моделі',
};

function renderHomeTasks() {
    const box = document.getElementById('homeTasks');
    if (!box) return;
    box.innerHTML = HOME_TASKS.map(task => `
        <button onclick="homeGo('${task.tab}')" class="card-box p-4 text-left hover:border-slate-400 transition">
            <div class="text-xs text-slate-500">${escapeHtml(task.role)}</div>
            <div class="text-sm font-semibold text-slate-900 mt-1">${escapeHtml(task.title)}</div>
            <p class="text-sm text-slate-600 mt-1">${escapeHtml(task.text)}</p>
        </button>`).join('');
}

function renderHomeTabs() {
    const box = document.getElementById('homeTabs');
    if (!box) return;
    box.innerHTML = HOME_TABS.map(item => `
        <button onclick="switchTab('${item.tab}')" class="card-box p-4 text-left hover:border-slate-400 transition">
            <div class="text-sm font-semibold text-slate-900">${escapeHtml(item.name)}</div>
            <p class="text-sm text-slate-600 mt-1">${escapeHtml(item.text)}</p>
        </button>`).join('');
}

function renderHomePrompts() {
    const box = document.getElementById('homePrompts');
    if (!box) return;
    const prompts = APP_STATE.prompts || [];
    box.innerHTML = prompts.map(p => `
        <div class="card-box p-4">
            <div class="flex items-baseline gap-2">
                <span class="text-xs font-semibold text-slate-500">${escapeHtml(p.code)}</span>
                <span class="text-sm font-semibold text-slate-900">${escapeHtml(String(p.name_uk || '').replace(/^P\d:\s*/, ''))}</span>
            </div>
            <p class="text-xs text-slate-500 mt-0.5">${escapeHtml(p.strategy_type || '')}</p>
            <p class="text-sm text-slate-600 mt-2">${escapeHtml(p.description_uk || '')}</p>
        </div>`).join('') || '<p class="text-sm text-slate-600">Шаблони промптів завантажуються.</p>';
}

function homeStepHtml(number, done, title, text, tab) {
    const mark = done === null ? '' : `<span class="text-xs ${done ? 'text-emerald-800' : 'text-amber-800'}">${done ? 'зроблено' : 'ще потрібно'}</span>`;
    return `<li class="card-box p-4 flex flex-wrap items-start justify-between gap-3">
        <div>
            <div class="text-sm font-semibold text-slate-900">${number}. ${escapeHtml(title)} ${mark}</div>
            <p class="text-sm text-slate-600 mt-1">${escapeHtml(text)}</p>
        </div>
        <button onclick="homeGo('${tab}')" class="px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50 text-sm">Відкрити</button>
    </li>`;
}

function renderHomeSteps(kbItems, hypothesesAvailable) {
    const box = document.getElementById('homeSteps');
    if (!box) return;
    const working = (APP_STATE.metadata?.models || []).filter(m => m.enabled && m.status_code === 'ok').length;
    const kbReady = kbItems.length > 0 && kbItems.every(s => s.queries >= 30);
    const runs = (APP_STATE.runs || []).length;
    box.innerHTML = [
        homeStepHtml(1, working > 0, 'Підключіть моделі', 'Вкажіть ключі провайдерів або додайте власну модель, натисніть «Оновити статуси» й увімкніть моделі, що відповідають.', 'settings'),
        homeStepHtml(2, kbReady, 'Підготуйте дані', 'Перегляньте тестові запити, затвердіть чернетки. Для висновків потрібно не менше 30 перевірених запитів у кожній сфері. За потреби розширте базу дистракторами.', 'dataset'),
        homeStepHtml(3, runs > 0, 'Запустіть серію', 'Оберіть гіпотезу, перевірте прогноз вартості та часу й запустіть. Серія працює у фоні.', 'experiments'),
        homeStepHtml(4, hypothesesAvailable, 'Прочитайте результати', 'Вердикти за гіпотезами, рекомендація моделі й розрахунок каскаду.', 'analytics#results-hypotheses'),
        homeStepHtml(5, null, 'Користуйтеся помічником', 'Відповідайте на запити клієнтів із моделлю, яку рекомендують результати.', 'sandbox'),
    ].join('');
}

function renderHomeStatus(kbItems) {
    const box = document.getElementById('homeStatus');
    if (!box) return;
    const models = (APP_STATE.metadata?.models || []).filter(m => m.enabled);
    const working = models.filter(m => m.status_code === 'ok').length;
    const perDomain = (kbItems || []).map(s => `${escapeHtml(s.name_uk)}: ${s.queries}${s.queries_draft ? ` (чернеток ${s.queries_draft})` : ''}`);
    box.innerHTML = `
        <li>Моделей увімкнено ${models.length}, відповідають ${working}.</li>
        <li>Серій експериментів: ${(APP_STATE.runs || []).length}.</li>
        <li>Перевірених тестових запитів за сферами: ${perDomain.join('; ') || 'немає'}.</li>`;
}

async function renderHomeDynamic() {
    const [hyp, kb] = await Promise.all([
        fetch('/api/hypotheses').then(r => r.json()).catch(() => null),
        fetch('/api/kb/summary').then(r => r.json()).catch(() => ({ items: [] })),
    ]);
    const criteria = hyp?.criteria || { min_queries: 30, min_cell_queries: 10, practical_gap_pts: 3, quality_tolerance_pts: 5 };
    const note = document.getElementById('homeCriteriaNote');
    if (note) note.textContent = `Кожна гіпотеза перевіряється на зібраних даних. Висновок видається, лише коли спільних запитів не менше ${criteria.min_queries}. Менша вибірка дає позначку «Замало даних».`;
    const box = document.getElementById('homeHypotheses');
    if (box) {
        const verdicts = hyp?.available ? hyp.hypotheses : {};
        box.innerHTML = ['H1', 'H2', 'H3'].map(id => homeHypothesisHtml(id, verdicts[id.toLowerCase()], criteria)).join('');
    }
    renderHomeSteps(kb.items || [], Boolean(hyp?.available));
    renderHomeStatus(kb.items || []);
}

function renderHome() {
    renderHomeTasks();
    renderHomeTabs();
    renderHomePrompts();
    renderHomeDynamic();
}
