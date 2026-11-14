/**
 * Apollo Voice AI admin panel: settings, doctors, departments, FAQs and
 * escalation keywords. Reads are public; writes send the admin bearer token
 * when the server requires one (ADMIN_TOKEN).
 */

const state = {
    config: {},
    doctors: [],
    departments: [],
    faqs: [],
    redisConnected: true,
    authEnabled: false,
};

const ADMIN_TOKEN_KEY = 'apollo_admin_token';
const TABS = ['settings', 'doctors', 'departments', 'faqs', 'escalation'];
const $ = (id) => document.getElementById(id);
const toast = (message, type = 'success') => window.ui.toast(message, type);

const ICONS = {
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
    empty: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
};

// ============================================================================
// UTILITIES
// ============================================================================

/** Escape a value for safe interpolation into HTML. */
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getAdminToken() {
    try {
        return sessionStorage.getItem(ADMIN_TOKEN_KEY) || '';
    } catch {
        return '';
    }
}

function setAdminToken(token) {
    try {
        if (token) sessionStorage.setItem(ADMIN_TOKEN_KEY, token);
        else sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    } catch {
        /* storage unavailable: token lives only for this request */
    }
    updateTokenButton();
}

function updateTokenButton() {
    const btn = $('tokenButton');
    btn.hidden = !state.authEnabled;
    $('tokenButtonText').textContent = getAdminToken() ? 'Forget admin token' : 'Enter admin token';
}

async function errorMessage(response, fallback) {
    if (response.status === 401) return 'The admin token was missing or not accepted, so nothing was saved.';
    try {
        const data = await response.json();
        if (typeof data.detail === 'string') return data.detail === 'Redis not connected' ? 'Redis is not connected, so changes cannot be saved.' : data.detail;
        if (Array.isArray(data.detail) && data.detail[0]) {
            const d = data.detail[0];
            const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : 'value';
            return `${String(field).replace(/_/g, ' ')}: ${d.msg}`;
        }
    } catch {
        /* not JSON */
    }
    return fallback;
}

/**
 * fetch() wrapper for the admin API. Sends the bearer token (if any) and,
 * when the server answers 401, asks for the token once and retries.
 */
async function adminFetch(url, options = {}, retried = false) {
    const headers = { ...(options.headers || {}) };
    const token = getAdminToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) {
        setAdminToken('');
        if (!retried) {
            const entered = await requestToken(Boolean(token));
            if (entered) {
                setAdminToken(entered);
                return adminFetch(url, options, true);
            }
        }
    }
    return response;
}

function jsonRequest(method, body) {
    return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

function setBusy(button, busy, busyLabel = 'Saving…') {
    if (!button) return;
    if (busy) {
        button.dataset.label = button.innerHTML;
        button.classList.add('is-busy');
        button.textContent = busyLabel;
        button.disabled = true;
    } else {
        button.classList.remove('is-busy');
        if (button.dataset.label) button.innerHTML = button.dataset.label;
        button.disabled = false;
    }
}

// ============================================================================
// DIALOGS
// ============================================================================

function openDialog(id) {
    const dialog = $(id);
    clearErrors(dialog.querySelector('form'));
    dialog.showModal();
    const first = dialog.querySelector('input:not([type=hidden]), textarea, select');
    if (first) first.focus();
}

function closeDialog(id) {
    $(id).close();
}

function setupDialogs() {
    document.querySelectorAll('dialog').forEach((dialog) => {
        dialog.querySelectorAll('[data-close]').forEach((btn) => btn.addEventListener('click', () => dialog.close('cancel')));
        // Click on the backdrop closes the dialog.
        dialog.addEventListener('click', (e) => {
            if (e.target === dialog) dialog.close('cancel');
        });
    });
}

function requestToken(wasRejected) {
    const dialog = $('tokenModal');
    const form = $('tokenForm');
    const input = $('tokenInput');
    form.reset();
    clearErrors(form);
    if (wasRejected) showFieldError(input, 'That token was not accepted. Check ADMIN_TOKEN on the server.');

    return new Promise((resolve) => {
        const onSubmit = (e) => {
            e.preventDefault();
            const value = input.value.trim();
            if (!value) {
                showFieldError(input, 'Enter the admin token.');
                input.focus();
                return;
            }
            cleanup();
            dialog.close('ok');
            resolve(value);
        };
        const onClose = () => {
            cleanup();
            resolve(null);
        };
        const cleanup = () => {
            form.removeEventListener('submit', onSubmit);
            dialog.removeEventListener('close', onClose);
        };
        form.addEventListener('submit', onSubmit);
        dialog.addEventListener('close', onClose);
        dialog.showModal();
        input.focus();
    });
}

function confirmDialog(title, text, confirmLabel = 'Delete') {
    const dialog = $('confirmModal');
    $('confirmTitle').textContent = title;
    $('confirmText').textContent = text;
    dialog.querySelector('button[value="confirm"]').textContent = confirmLabel;
    dialog.returnValue = '';
    return new Promise((resolve) => {
        dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
        dialog.showModal();
    });
}

// ============================================================================
// VALIDATION
// ============================================================================

function fieldLabel(input) {
    const label = document.querySelector(`label[for="${input.id}"]`);
    return label ? label.textContent.replace('*', '').trim() : 'This field';
}

function showFieldError(input, message) {
    input.setAttribute('aria-invalid', 'true');
    const id = `${input.id}-error`;
    let node = document.getElementById(id);
    if (!node) {
        node = document.createElement('span');
        node.id = id;
        node.className = 'field-error';
        input.insertAdjacentElement('afterend', node);
        const described = (input.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
        input.setAttribute('aria-describedby', [id, ...described].join(' '));
    }
    node.textContent = message;
}

function clearFieldError(input) {
    input.removeAttribute('aria-invalid');
    const node = document.getElementById(`${input.id}-error`);
    if (node) {
        node.remove();
        const described = (input.getAttribute('aria-describedby') || '').split(' ').filter((x) => x && x !== node.id);
        if (described.length) input.setAttribute('aria-describedby', described.join(' '));
        else input.removeAttribute('aria-describedby');
    }
}

function clearErrors(form) {
    if (!form) return;
    form.querySelectorAll('[aria-invalid="true"]').forEach(clearFieldError);
}

/** Validate a form; shows inline messages and focuses the first problem. */
function validateForm(form) {
    let firstInvalid = null;
    form.querySelectorAll('input, textarea, select').forEach((input) => {
        if (input.type === 'hidden' || input.type === 'checkbox') return;
        clearFieldError(input);
        const value = input.value.trim();
        let message = '';
        if (input.required && !value) {
            message = `${fieldLabel(input)} is required.`;
        } else if (input.type === 'number' && value) {
            const n = Number(value);
            const min = input.min !== '' ? Number(input.min) : -Infinity;
            const max = input.max !== '' ? Number(input.max) : Infinity;
            if (!Number.isInteger(n)) message = 'Enter a whole number.';
            else if (n < min || n > max) message = `Enter a number between ${input.min} and ${input.max}.`;
        }
        if (message) {
            showFieldError(input, message);
            firstInvalid = firstInvalid || input;
        }
    });
    if (firstInvalid) firstInvalid.focus();
    return !firstInvalid;
}

function setupLiveValidation() {
    document.addEventListener('input', (e) => {
        if (e.target.matches('[aria-invalid="true"]') && e.target.value.trim()) clearFieldError(e.target);
    });
}

// ============================================================================
// NAVIGATION (hash-based, so sections can be linked and survive reloads)
// ============================================================================

function showTab(tabId, focusHeading = false) {
    if (!TABS.includes(tabId)) tabId = 'settings';
    TABS.forEach((id) => {
        $(`section-${id}`).hidden = id !== tabId;
    });
    document.querySelectorAll('.nav-item').forEach((item) => {
        if (item.dataset.tab === tabId) item.setAttribute('aria-current', 'page');
        else item.removeAttribute('aria-current');
    });
    const title = $(`section-${tabId}`).querySelector('h1');
    document.title = `${title ? title.textContent : 'Admin'} · Apollo Voice AI admin`;
    if (focusHeading) $('main').focus({ preventScroll: true });
}

function initTabs() {
    window.addEventListener('hashchange', () => showTab(location.hash.slice(1), true));
    showTab(location.hash.slice(1));
}

// ============================================================================
// SERVER STATUS
// ============================================================================

async function loadHealth() {
    try {
        const data = await (await fetch('/health', { cache: 'no-store' })).json();
        state.redisConnected = Boolean(data.redis_connected);
        state.authEnabled = Boolean(data.admin_auth_enabled);
    } catch {
        state.redisConnected = true; // unknown: let the API report errors
    }
    $('redisNotice').hidden = state.redisConnected;
    document.querySelectorAll('[data-requires-write]').forEach((btn) => {
        btn.disabled = !state.redisConnected;
        if (!state.redisConnected) btn.title = 'Redis is not connected';
    });
    updateTokenButton();
}

// ============================================================================
// TABLE STATES
// ============================================================================

function renderSkeleton(table, columns) {
    const row = `<tr class="skeleton-row">${'<td><span class="skeleton"></span></td>'.repeat(columns)}</tr>`;
    table.querySelector('tbody').innerHTML = row.repeat(3);
}

function renderTableState(stateEl, kind, { title, text, action, onAction } = {}) {
    if (!kind) {
        stateEl.replaceChildren();
        return;
    }
    stateEl.innerHTML = `
        <div class="state-block">
            ${kind === 'error' ? ICONS.error : ICONS.empty}
            <h3>${escapeHtml(title)}</h3>
            <p>${escapeHtml(text)}</p>
            ${action ? `<button type="button" class="btn btn-${kind === 'error' ? 'secondary' : 'primary'} btn-sm">${escapeHtml(action)}</button>` : ''}
        </div>`;
    const btn = stateEl.querySelector('button');
    if (btn && onAction) {
        btn.addEventListener('click', onAction);
        if (kind === 'empty' && !state.redisConnected) btn.disabled = true;
    }
}

function setCount(id, n) {
    $(id).textContent = n ? String(n) : '';
}

/**
 * Generic loader for a list endpoint: skeleton while loading, error with
 * retry on failure, empty state with a call to action, else rows.
 */
async function loadCollection(view) {
    const { key, url, table, stateEl, columns, countEl, render, emptyTitle, emptyText, emptyAction, onEmptyAction } = view;
    $(table).hidden = false;
    renderSkeleton($(table), columns);
    renderTableState($(stateEl));
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(String(response.status));
        state[key] = (await response.json())[key] || [];
    } catch {
        $(table).querySelector('tbody').replaceChildren();
        $(table).hidden = true;
        renderTableState($(stateEl), 'error', {
            title: `Couldn’t load ${key}`,
            text: 'The server did not respond. Check that it is running, then try again.',
            action: 'Try again',
            onAction: () => loadCollection(view),
        });
        return;
    }

    setCount(countEl, state[key].length);
    if (state[key].length === 0) {
        $(table).hidden = true;
        $(table).querySelector('tbody').replaceChildren();
        renderTableState($(stateEl), 'empty', { title: emptyTitle, text: emptyText, action: emptyAction, onAction: onEmptyAction });
        return;
    }
    $(table).hidden = false;
    $(table).querySelector('tbody').innerHTML = state[key].map(render).join('');
}

function actionButtons(kind, item, label, { edit = true } = {}) {
    const name = escapeHtml(label);
    const id = escapeHtml(item.id);
    return `
        <td class="actions">
            ${edit ? `<button type="button" class="btn-icon" data-action="edit-${kind}" data-id="${id}" aria-label="Edit ${name}" title="Edit">${ICONS.edit}</button>` : ''}
            <button type="button" class="btn-icon btn-icon-danger" data-action="delete-${kind}" data-id="${id}" aria-label="Delete ${name}" title="Delete">${ICONS.trash}</button>
        </td>`;
}

// ============================================================================
// SETTINGS
// ============================================================================

async function loadSettings() {
    try {
        const response = await fetch('/api/admin/config');
        if (!response.ok) throw new Error(String(response.status));
        state.config = await response.json();
        populateSettingsForm(state.config);
    } catch {
        toast('Couldn’t load settings. Showing defaults.', 'error');
        populateSettingsForm({});
    }
}

function populateSettingsForm(config) {
    $('hospitalName').value = config.hospital_name || 'Apollo Hospital';
    $('city').value = config.city || '';
    $('emergencyNumber').value = config.emergency_number || '108';
    $('helpline').value = config.helpline || '';
    $('maxWords').value = config.max_words || 50;
    $('sessionTimeout').value = config.session_timeout_minutes || 30;
    $('disclaimerText').value = config.disclaimer_text || '';
    $('tone').value = config.tone || 'formal';
    $('primaryLanguage').value = config.primary_language || 'en';
    $('disclaimerRequired').checked = config.disclaimer_required !== false;
    $('alwaysRecommendDoctor').checked = config.always_recommend_doctor !== false;
    $('ttsEnabled').checked = config.tts_enabled !== false;
    $('escalationKeywords').value = (config.escalation_keywords || []).join('\n');
    clearErrors($('settingsForm'));
}

async function saveConfig(config, button, successMessage) {
    setBusy(button, true);
    try {
        const response = await adminFetch('/api/admin/config', jsonRequest('POST', config));
        if (!response.ok) {
            toast(await errorMessage(response, 'Couldn’t save. Please try again.'), 'error');
            return false;
        }
        state.config = config;
        toast(successMessage);
        return true;
    } catch {
        toast('Couldn’t reach the server. Please try again.', 'error');
        return false;
    } finally {
        setBusy(button, false);
        if (!state.redisConnected) button.disabled = true;
    }
}

async function saveSettings(e) {
    e.preventDefault();
    const form = e.target;
    if (!validateForm(form)) return;
    const data = new FormData(form);
    const config = {
        ...state.config,
        hospital_name: data.get('hospital_name').trim(),
        city: data.get('city').trim(),
        emergency_number: data.get('emergency_number').trim(),
        helpline: data.get('helpline').trim(),
        tone: data.get('tone'),
        max_words: parseInt(data.get('max_words'), 10),
        primary_language: data.get('primary_language'),
        session_timeout_minutes: parseInt(data.get('session_timeout_minutes'), 10),
        disclaimer_required: data.get('disclaimer_required') === 'on',
        always_recommend_doctor: data.get('always_recommend_doctor') === 'on',
        tts_enabled: data.get('tts_enabled') === 'on',
        disclaimer_text: data.get('disclaimer_text'),
        supported_languages: state.config.supported_languages || ['kn', 'ta', 'te', 'hi', 'en'],
    };
    await saveConfig(config, e.submitter || form.querySelector('[type=submit]'), 'Settings saved');
}

async function saveEscalation(e) {
    e.preventDefault();
    const keywords = $('escalationKeywords')
        .value.split('\n')
        .map((k) => k.trim())
        .filter(Boolean);
    const unique = [...new Set(keywords)];
    await saveConfig({ ...state.config, escalation_keywords: unique }, e.submitter || e.target.querySelector('[type=submit]'), `Saved ${unique.length} escalation keyword${unique.length === 1 ? '' : 's'}`);
}

// ============================================================================
// DOCTORS
// ============================================================================

const doctorsView = {
    key: 'doctors',
    url: '/api/admin/doctors',
    table: 'doctorsTable',
    stateEl: 'doctorsState',
    columns: 6,
    countEl: 'doctorsCount',
    emptyTitle: 'No doctors yet',
    emptyText: 'Add the doctors patients ask about, with their department and hours.',
    emptyAction: 'Add doctor',
    onEmptyAction: () => showDoctorModal(),
    render: (doc) => `
        <tr>
            <td class="cell-title"><span class="cell-primary">${escapeHtml(doc.name)}</span>${doc.name_kn ? `<span class="cell-sub" lang="kn">${escapeHtml(doc.name_kn)}</span>` : ''}</td>
            <td data-label="Specialization">${escapeHtml(doc.specialization || '–')}</td>
            <td data-label="Department">${escapeHtml(doc.department || '–')}</td>
            <td data-label="Days">${escapeHtml(formatDays(doc.available_days))}</td>
            <td data-label="Timings">${escapeHtml(doc.timings || '–')}</td>
            ${actionButtons('doctor', doc, doc.name)}
        </tr>`,
};

const DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function formatDays(days) {
    if (!Array.isArray(days) || days.length === 0) return typeof days === 'string' && days ? days : '–';
    if (days.length === 7) return 'Every day';
    const sorted = [...days].sort((a, b) => DAY_ORDER.indexOf(a) - DAY_ORDER.indexOf(b));
    return sorted.map((d) => d.slice(0, 3)).join(', ');
}

function showDoctorModal(doctor = null) {
    const form = $('doctorForm');
    form.reset();
    $('doctorModalTitle').textContent = doctor ? 'Edit doctor' : 'Add doctor';
    $('doctorId').value = doctor ? doctor.id : '';
    $('departmentOptions').innerHTML = state.departments.map((d) => `<option value="${escapeHtml(d.name)}"></option>`).join('');
    if (doctor) {
        $('doctorName').value = doctor.name || '';
        $('doctorNameKn').value = doctor.name_kn || '';
        $('doctorSpec').value = doctor.specialization || '';
        $('doctorDept').value = doctor.department || '';
        $('doctorTimings').value = doctor.timings || '';
        const days = Array.isArray(doctor.available_days) ? doctor.available_days : [];
        form.querySelectorAll('input[name="available_days"]').forEach((cb) => (cb.checked = days.includes(cb.value)));
    }
    openDialog('doctorModal');
}

async function saveDoctor(e) {
    e.preventDefault();
    const form = e.target;
    if (!validateForm(form)) return;
    const data = new FormData(form);
    const doctor = {
        name: data.get('name').trim(),
        name_kn: data.get('name_kn').trim(),
        specialization: data.get('specialization').trim(),
        department: data.get('department').trim(),
        timings: data.get('timings').trim(),
        available_days: data.getAll('available_days'),
    };
    const id = data.get('id');
    await saveItem({
        button: e.submitter,
        url: id ? `/api/admin/doctors/${encodeURIComponent(id)}` : '/api/admin/doctors',
        method: id ? 'PUT' : 'POST',
        body: doctor,
        dialog: 'doctorModal',
        success: id ? `Updated ${doctor.name}` : `Added ${doctor.name}`,
        reload: () => loadCollection(doctorsView),
    });
}

// ============================================================================
// DEPARTMENTS
// ============================================================================

const departmentsView = {
    key: 'departments',
    url: '/api/admin/departments',
    table: 'departmentsTable',
    stateEl: 'departmentsState',
    columns: 5,
    countEl: 'departmentsCount',
    emptyTitle: 'No departments yet',
    emptyText: 'Add departments so the assistant can tell patients where to go.',
    emptyAction: 'Add department',
    onEmptyAction: () => showDepartmentModal(),
    render: (dept) => `
        <tr>
            <td class="cell-title"><span class="cell-primary">${escapeHtml(dept.name)}</span></td>
            <td data-label="Location">${escapeHtml(dept.floor || '–')}</td>
            <td data-label="Timings">${escapeHtml(dept.timings || '–')}</td>
            <td data-label="Contact">${escapeHtml(dept.contact || '–')}</td>
            ${actionButtons('department', dept, dept.name, { edit: false })}
        </tr>`,
};

function showDepartmentModal() {
    $('departmentForm').reset();
    openDialog('departmentModal');
}

async function saveDepartment(e) {
    e.preventDefault();
    const form = e.target;
    if (!validateForm(form)) return;
    const data = new FormData(form);
    const department = {
        name: data.get('name').trim(),
        floor: data.get('floor').trim(),
        timings: data.get('timings').trim(),
        contact: data.get('contact').trim(),
    };
    await saveItem({
        button: e.submitter,
        url: '/api/admin/departments',
        method: 'POST',
        body: department,
        dialog: 'departmentModal',
        success: `Added ${department.name}`,
        reload: () => loadCollection(departmentsView),
    });
}

// ============================================================================
// FAQS
// ============================================================================

const faqsView = {
    key: 'faqs',
    url: '/api/admin/faqs',
    table: 'faqsTable',
    stateEl: 'faqsState',
    columns: 4,
    countEl: 'faqsCount',
    emptyTitle: 'No FAQs yet',
    emptyText: 'FAQs are searched before every reply, so answers stay accurate to your hospital.',
    emptyAction: 'Add FAQ',
    onEmptyAction: () => showFaqModal(),
    render: (faq) => `
        <tr>
            <td class="cell-title"><span class="cell-primary">${escapeHtml(faq.question)}</span>${faq.question_kn ? `<span class="cell-sub" lang="kn">${escapeHtml(faq.question_kn)}</span>` : ''}</td>
            <td data-label="Answer"><span class="cell-clamp">${escapeHtml(faq.answer || '–')}</span></td>
            <td data-label="Category"><span class="badge">${escapeHtml(faq.category || 'general')}</span></td>
            ${actionButtons('faq', faq, faq.question)}
        </tr>`,
};

function showFaqModal(faq = null) {
    $('faqForm').reset();
    $('faqModalTitle').textContent = faq ? 'Edit FAQ' : 'Add FAQ';
    $('faqId').value = faq ? faq.id : '';
    if (faq) {
        $('faqQuestion').value = faq.question || '';
        $('faqQuestionKn').value = faq.question_kn || '';
        $('faqAnswer').value = faq.answer || '';
        $('faqAnswerKn').value = faq.answer_kn || '';
        $('faqCategory').value = faq.category || 'general';
    }
    openDialog('faqModal');
}

async function saveFaq(e) {
    e.preventDefault();
    const form = e.target;
    if (!validateForm(form)) return;
    const data = new FormData(form);
    const faq = {
        question: data.get('question').trim(),
        question_kn: data.get('question_kn').trim(),
        answer: data.get('answer').trim(),
        answer_kn: data.get('answer_kn').trim(),
        category: data.get('category'),
    };
    const id = data.get('id');
    await saveItem({
        button: e.submitter,
        url: id ? `/api/admin/faqs/${encodeURIComponent(id)}` : '/api/admin/faqs',
        method: id ? 'PUT' : 'POST',
        body: faq,
        dialog: 'faqModal',
        success: id ? 'FAQ updated' : 'FAQ added',
        reload: () => loadCollection(faqsView),
    });
}

// ============================================================================
// SHARED CRUD
// ============================================================================

async function saveItem({ button, url, method, body, dialog, success, reload }) {
    setBusy(button, true);
    try {
        const response = await adminFetch(url, jsonRequest(method, body));
        if (!response.ok) {
            toast(await errorMessage(response, 'Couldn’t save. Please try again.'), 'error');
            return;
        }
        closeDialog(dialog);
        toast(success);
        await reload();
    } catch {
        toast('Couldn’t reach the server. Please try again.', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function deleteItem({ url, label, success, reload }) {
    const ok = await confirmDialog(`Delete ${label}?`, 'The assistant will stop using it straight away. This cannot be undone.');
    if (!ok) return;
    try {
        const response = await adminFetch(url, { method: 'DELETE' });
        if (!response.ok) {
            toast(await errorMessage(response, 'Couldn’t delete. Please try again.'), 'error');
            return;
        }
        toast(success);
        await reload();
    } catch {
        toast('Couldn’t reach the server. Please try again.', 'error');
    }
}

function setupTableActions() {
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const id = btn.dataset.id;
        switch (btn.dataset.action) {
            case 'edit-doctor':
                showDoctorModal(state.doctors.find((d) => d.id === id));
                break;
            case 'delete-doctor': {
                const doc = state.doctors.find((d) => d.id === id);
                deleteItem({ url: `/api/admin/doctors/${encodeURIComponent(id)}`, label: doc ? doc.name : 'doctor', success: 'Doctor deleted', reload: () => loadCollection(doctorsView) });
                break;
            }
            case 'delete-department': {
                const dept = state.departments.find((d) => d.id === id);
                deleteItem({ url: `/api/admin/departments/${encodeURIComponent(id)}`, label: dept ? dept.name : 'department', success: 'Department deleted', reload: () => loadCollection(departmentsView) });
                break;
            }
            case 'edit-faq':
                showFaqModal(state.faqs.find((f) => f.id === id));
                break;
            case 'delete-faq':
                deleteItem({ url: `/api/admin/faqs/${encodeURIComponent(id)}`, label: 'this FAQ', success: 'FAQ deleted', reload: () => loadCollection(faqsView) });
                break;
        }
    });
}

// ============================================================================
// START
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    initTabs();
    setupDialogs();
    setupLiveValidation();
    setupTableActions();

    $('settingsForm').addEventListener('submit', saveSettings);
    $('resetSettings').addEventListener('click', () => {
        populateSettingsForm(state.config);
        toast('Changes discarded', 'info');
    });
    $('escalationForm').addEventListener('submit', saveEscalation);
    $('doctorForm').addEventListener('submit', saveDoctor);
    $('departmentForm').addEventListener('submit', saveDepartment);
    $('faqForm').addEventListener('submit', saveFaq);
    $('addDoctor').addEventListener('click', () => showDoctorModal());
    $('addDepartment').addEventListener('click', () => showDepartmentModal());
    $('addFaq').addEventListener('click', () => showFaqModal());
    $('tokenButton').addEventListener('click', async () => {
        if (getAdminToken()) {
            setAdminToken('');
            toast('Admin token forgotten for this tab', 'info');
        } else {
            const token = await requestToken(false);
            if (token) {
                setAdminToken(token);
                toast('Token saved for this tab. It is checked on your next change.', 'info');
            }
        }
    });

    await loadHealth();
    await Promise.all([loadSettings(), loadCollection(doctorsView), loadCollection(departmentsView), loadCollection(faqsView)]);
});
