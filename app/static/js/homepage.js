/**
 * bsq: the Home Page, laid out as QuickBooks Desktop's: Vendors, Customers
 * and Employees down the left with the workflow arrows between them,
 * Company and Banking on the right. Each icon opens its screen (a form
 * opens ready to fill in). "Insights" is the overview of cards that was the
 * main page before (#/insights).
 */
const HomePage = {
    // [key, label, icon, x (% of the width), y (px), what it is]
    // what it is: a "#/…" address (a link: it only opens a page); 'form'
    // (opens a blank form to save: not for a read-only sign-in); 'admin
    // form' (Pay Employees: a new pay run, the administrator's)
    items() {
        return [
            ['bills', 'Enter Bills', '🧾', 20, 120, 'form'],
            ['paybills', 'Pay Bills', '💸', 55, 120, 'form'],
            ['hst', 'Manage HST', '📊', 69, 120, '#/tax'],
            ['estimates', 'Estimates', '📝', 7, 400, 'form'],
            ['invoice', T('Create Invoices'), '📄', 20, 400, 'form'],
            ['payment', 'Receive Payments', '💰', 50, 400, 'form'],
            ['statements', 'Statements', '📑', 41, 500, '#/reports/customer-statement'],
            ['credits', 'Refunds & Credits', '↩️', 66, 500, 'form'],
            ['payroll', 'Payroll Centre', '👥', 7, 680, '#/payroll'],
            ['time', 'Enter Time', '⏱️', 20, 680, '#/timesheet'],
            ['payemp', 'Pay Employees', '💵', 35, 680, 'admin form'],
            ['liab', 'Pay Liabilities', '🏛️', 50, 680, '#/payroll reports'],
            ['forms', 'Process Payroll Forms', '📋', 64, 680, '#/payroll reports'],
            ['accounts', 'Chart of Accounts', '📒', 83, 100, '#/accounts'],
            ['items', 'Items & Services', '📦', 94, 100, '#/items'],
            ['reports', 'Report Center', '📈', 83, 210, '#/reports'],
            ['deposits', 'Record Deposits', '📥', 83, 400, 'form'],
            ['reconcile', 'Reconcile', '✅', 94, 400, 'form'],
            ['cheques', 'Write Cheques', '✍️', 83, 520, 'form'],
            ['register', 'Cheque Register', '📘', 94, 520, 'register'],
            ['banks', 'Bank Accounts', '🏦', 83, 640, '#/banking'],
            ['cc', 'Enter Credit Card Charges', '💳', 94, 640, 'form'],
        ];
    },
    // panels: [title, left %, right %, top px, bottom px]
    PANELS: [
        ['Vendors', 0, 75.5, 10, 240], [T('Customers'), 0, 75.5, 255, 570], ['Employees', 0, 75.5, 585, 770],
        ['Company', 77, 100, 10, 300], ['Banking', 77, 100, 315, 770],
    ],
    // arrows: ['h', y, x1 %, x2 %] or ['v', x %, y1, y2]; a head at the second end unless noHead
    ARROWS: [
        ['h', 120, 20, 55], ['v', 20, 120, 400],
        ['h', 400, 7, 20], ['h', 400, 20, 50], ['h', 400, 50, 83],
        ['v', 41, 500, 400, true],
        ['v', 20, 680, 400],
        ['h', 680, 20, 35], ['h', 680, 35, 50], ['h', 680, 50, 64],
    ],
    GAP: 34,   // px kept clear around an icon at each end of an arrow

    tabs(active) {
        const tab = (key, label, href) => `<a href="${href}" class="home-tab${active === key ? ' home-tab--on' : ''}"${active === key ? ' aria-current="page"' : ''}>${label}</a>`;
        return `<nav class="home-tabs" aria-label="Home">${tab('home', 'Home Page', '#/')}${tab('insights', 'Insights', '#/insights')}</nav>`;
    },

    render() {
        const g = HomePage.GAP;
        const panels = HomePage.PANELS.map(([t, l, r, top, bot]) =>
            `<section class="home-panel" style="left:${l}%;width:calc(${r - l}% - 6px);top:${top}px;height:${bot - top}px" aria-label="${t}">
                <span class="home-panel__title">${t.toUpperCase()}</span></section>`).join('');
        const arrows = HomePage.ARROWS.map(a => {
            if (a[0] === 'h') {
                const [, y, x1, x2] = a;
                const back = x2 < x1;
                return `<span class="home-line home-line--h${back ? ' home-line--back' : ''}" aria-hidden="true"
                    style="top:${y - 2}px;left:calc(${Math.min(x1, x2)}% + ${g}px);width:calc(${Math.abs(x2 - x1)}% - ${2 * g}px)"></span>`;
            }
            // from the icon at y1 to the icon at y2; with noHead it runs on
            // into the line at y2 (Statements joins the invoice-to-payment line)
            const [, x, y1, y2, noHead] = a;
            const up = y2 < y1;
            const lo = Math.min(y1, y2), hi = Math.max(y1, y2);
            const top = noHead ? lo : lo + g, bottom = noHead ? hi - g : hi - g;
            return `<span class="home-line home-line--v${up ? ' home-line--up' : ''}${noHead ? ' home-line--nohead' : ''}" aria-hidden="true"
                style="left:calc(${x}% - 2px);top:${top}px;height:${bottom - top}px"></span>`;
        }).join('');
        const items = HomePage.items().map(([key, label, icon, x, y, what]) => {
            const inner = `<span class="home-item__icon" aria-hidden="true">${icon}</span><span class="home-item__label">${escapeHtml(label)}</span>`;
            const pos = `style="left:${x}%;top:${y - 26}px"`;
            if (what.startsWith('#/')) {
                // the payroll links open its Pay Runs, or its PD7A, WSIB & T4 tab
                const [href, tab] = what.split(' ');
                const pick = href === '#/payroll' ? ` onclick="PayrollCAPage._tab='${tab || 'runs'}'"` : '';
                return `<a class="home-item" href="${href}" ${pos}${pick}>${inner}</a>`;
            }
            if (what === 'register') return `<button type="button" class="home-item" ${pos} onclick="HomePage.register()">${inner}</button>`;
            const marks = (what.includes('admin') ? ' data-admin' : '') + (what.includes('form') ? ' data-write' : '');
            const call = what === 'admin form' ? 'HomePage.payEmployees()' : `HomePage.open('${key}')`;
            return `<button type="button" class="home-item"${marks} ${pos} onclick="${call}">${inner}</button>`;
        }).join('');
        return `${HomePage.tabs('home')}
            <div class="home-wrap"><div class="home-flow">${panels}${arrows}${items}</div></div>`;
    },

    async _bank() {
        // the one bank account (not a card), if there is just one
        try {
            const rows = (await API.get('/banking/overview')) || [];
            const banks = (Array.isArray(rows) ? rows : rows.accounts || []).filter(a => a.bank_kind !== 'credit_card');
            return banks.length === 1 ? banks[0].account_id : null;
        } catch (e) { return null; }
    },

    // the Cheque Register: the bank account's register (a read)
    async register() {
        const id = await HomePage._bank();
        return App.navigate(id ? `#/banking/${id}` : '#/banking');
    },

    // Pay Employees: a new pay run
    async payEmployees() {
        PayrollCAPage._tab = 'runs';
        await App.navigate('#/payroll');
        await PayrollCAPage.newRun();
    },

    // a form, opened blank, ready to fill in
    async open(key) {
        const go = async (hash, then) => { await App.navigate(hash); if (then) await then(); };
        switch (key) {
            case 'bills': return go('#/bills', () => BillsPage.showForm());
            case 'paybills': return go('#/bills', () => BillsPage.showPayForm());
            case 'estimates': return go('#/estimates', () => EstimatesPage.showForm());
            case 'invoice': return go('#/invoices', () => InvoicesPage.showForm());
            case 'payment': return go('#/payments', () => PaymentsPage.showForm());
            case 'credits': return go('#/credit-memos', () => CreditMemosPage.showForm());
            case 'deposits': return go('#/deposits', () => DepositsPage.makeDeposit());
            case 'cheques': return go('#/expenses', () => ExpensesPage.showForm());
            case 'cc': return go('#/cc-charges', () => CCChargesPage.showForm());
            case 'reconcile': {
                const id = await HomePage._bank();
                return id ? go(`#/banking/${id}`, () => BankingPage.startReconcile(id)) : go('#/banking');
            }
        }
    },
};
