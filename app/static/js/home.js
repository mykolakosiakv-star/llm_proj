const HOME_TASKS = [
    { role: 'Співробітник підтримки', title: 'Відповісти на запит клієнта', text: 'Знайти відповідь у базі знань і дізнатися, чи потрібен оператор.', tab: 'sandbox' },
    { role: 'Керівник підтримки', title: 'Обрати модель для впровадження', text: 'Рекомендація з урахуванням якості, ціни та безпеки.', tab: 'analytics' },
    { role: 'Дослідник', title: 'Перевірити гіпотези на даних', text: 'Запустити серію запитів по сферах і побачити, що вона підтверджує.', tab: 'experiments' },
    { role: 'Інженер впровадження', title: 'Додати власну модель', text: 'Підключити будь-який сервіс із OpenAI-сумісним API та перевірити його.', tab: 'settings' },
    { role: 'Інженер впровадження', title: 'Додати сферу або запити', text: 'Завантажити типові запити та перевірити, як модель на них відповідає.', tab: 'dataset' },
    { role: 'Студент або викладач', title: 'Відтворити результати й експортувати їх', text: 'Методика, таблиці та вибірка для ручної перевірки.', tab: 'analytics' },
];

function renderHomeTasks() {
    const box = document.getElementById('homeTasks');
    if (!box) return;
    box.innerHTML = HOME_TASKS.map(task => `
        <button onclick="switchTab('${task.tab}')" class="card-box p-4 text-left hover:border-slate-400 transition">
            <div class="text-xs text-slate-500">${escapeHtml(task.role)}</div>
            <div class="text-sm font-semibold text-slate-900 mt-1">${escapeHtml(task.title)}</div>
            <p class="text-sm text-slate-600 mt-1">${escapeHtml(task.text)}</p>
        </button>`).join('');
}

function renderHomeStatus() {
    const box = document.getElementById('homeStatus');
    if (!box) return;
    const models = (APP_STATE.metadata?.models || []).filter(m => m.enabled);
    const working = models.filter(m => m.status_code === 'ok').length;
    const domains = APP_STATE.metadata?.domains || {};
    const perDomain = Object.values(domains).map(d => {
        const count = (APP_STATE.queries || []).filter(q => (q.domain || 'fintech') === d.id).length;
        return `${escapeHtml(d.name_uk)}: ${count}`;
    });
    box.innerHTML = `
        <li>Моделей увімкнено ${models.length}, відповідають ${working}.</li>
        <li>Серій експериментів: ${(APP_STATE.runs || []).length}.</li>
        <li>Типових запитів за сферами: ${perDomain.join('; ') || 'немає'}.</li>`;
}

async function renderHomeHypotheses() {
    const box = document.getElementById('homeHypotheses');
    if (!box) return;
    try {
        const res = await fetch('/api/hypotheses').then(r => r.json());
        box.innerHTML = res.available
            ? ['h1', 'h2', 'h3'].map(k => hypothesisCardHtml(res.hypotheses[k])).join('')
            : '<p class="text-sm text-slate-600 lg:col-span-3">Завершених серій експериментів ще немає. Запустіть серію у вкладці «Експерименти», і тут з\'являться висновки за гіпотезами.</p>';
    } catch (err) {
        box.innerHTML = '<p class="text-sm text-slate-600 lg:col-span-3">Не вдалося завантажити висновки за гіпотезами.</p>';
    }
}

function renderHome() {
    renderHomeTasks();
    renderHomeStatus();
    renderHomeHypotheses();
}
