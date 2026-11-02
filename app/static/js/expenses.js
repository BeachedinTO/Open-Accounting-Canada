/* bsq: a cheque's amount in words, as QuickBooks prints it:
 * 5051.86 -> "Five thousand fifty-one and 86/100" */
function chequeWords(amount) {
    const ones = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
        'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
    const tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];
    const under1000 = n => {
        const out = [];
        if (n >= 100) { out.push(ones[Math.floor(n / 100)] + ' hundred'); n %= 100; }
        if (n >= 20) out.push(tens[Math.floor(n / 10)] + (n % 10 ? '-' + ones[n % 10] : ''));
        else if (n > 0) out.push(ones[n]);
        return out.join(' ');
    };
    const cents = Math.round(amount * 100);
    let dollars = Math.floor(cents / 100);
    const parts = [];
    for (const [size, name] of [[1e9, 'billion'], [1e6, 'million'], [1e3, 'thousand']]) {
        if (dollars >= size) { parts.push(under1000(Math.floor(dollars / size)) + ' ' + name); dollars %= size; }
    }
    if (dollars) parts.push(under1000(dollars));
    let words = parts.join(' ') || 'zero';
    words = words.charAt(0).toUpperCase() + words.slice(1);
    return `${words} and ${String(cents % 100).padStart(2, '0')}/100`;
}

/**
 * Expenses — record a receipt that's already been paid.
 * DR Expense Account, CR the bank / credit-card account it was paid from.
 *
 * This is where most scanned receipts belong: a bill is money you still
 * owe, a sales receipt is money you took in — a paid receipt is neither.
 * One form, one save, scan attached as evidence.
 */
/**
 * bsq (Canada): the "HST included" box on Expenses and Credit Card Charges.
 * Shown only when Settings > Tax on Purchases is recoverable. The 13%
 * button works out the HST inside a tax-included total: total x 13 / 113.
 */
/** bsq: the HST rate (percent) a new purchase document starts at — the
 * company's default tax rate when HST on purchases is claimed back. */
async function bsqPurchaseHstPercent() {
    try {
        const s = await API.get('/settings');
        return s.purchase_tax_recoverable === 'true' ? (parseFloat(s.default_tax_rate || '0') || 0) : 0;
    } catch (e) { return 0; }
}

const HstField = {
    RATE: 13,
    async enabled() {
        try {
            const s = await API.get('/settings');
            return (s && s.purchase_tax_recoverable) === 'true';
        } catch (e) { return false; }
    },
    html(on) {
        if (!on) return '';
        return `<div class="form-group"><label>HST included</label>
            <div style="display:flex; gap:6px; align-items:center;">
                <input name="tax_amount" type="number" step="0.01" min="0" placeholder="0.00"
                    title="The HST shown on the receipt. It is part of the amount, and is claimed back as an input tax credit.">
                <button type="button" class="btn btn-sm btn-secondary" onclick="HstField.fill(this.form)"
                    title="Work out ${HstField.RATE}% HST included in the amount">${HstField.RATE}%</button>
            </div></div>`;
    },
    fill(form) {
        if (form.querySelector && form.querySelector('.split-lines')) {
            delete form.tax_amount.dataset.manual;
            SplitPurchase.recalc(form);
            return;
        }
        const total = parseFloat(form.amount.value);
        if (!(total > 0) || !form.tax_amount) return;
        form.tax_amount.value = (Math.round(total * HstField.RATE / (100 + HstField.RATE) * 100) / 100).toFixed(2);
        if (typeof SplitPurchase !== 'undefined') SplitPurchase.recalc(form);
    },
    value(form) {
        return form.tax_amount && form.tax_amount.value ? parseFloat(form.tax_amount.value) : 0;
    },
};

/**
 * bsq: the split lines on the purchase forms (Expenses: cheque, debit,
 * cash, e-transfer; Credit Card Charges): each line has a type of expense
 * (account), the customer or job it is for, a description and its amount
 * before HST. One HST amount covers the receipt. The lines plus HST must
 * add up to the amount paid; the summary under the table says by how much
 * they don't yet.
 */
const SplitPurchase = {
    _acctOpts: '',
    tableHtml(expenseAccts, firstAccountId) {
        SplitPurchase._acctOpts = PurchaseAccounts.options(expenseAccts);
        return `<h3 style="margin:12px 0 8px;font-size:14px;">Expenses</h3>
            <div class="table-container table-container--scroll"><table class="line-items-table split-lines-table">
                <thead><tr><th scope="col" title="The type of expense">Account</th><th scope="col" title="HST charged on this line (tax code H, 13%)">Tax</th><th scope="col" class="col-amount" title="Before HST">Amount</th><th scope="col">Memo</th><th scope="col">${T('Customer')}:${T('Job')}</th>
                    <th scope="col" title="Billable to the customer">Billable</th><th scope="col"></th></tr></thead>
                <tbody class="split-lines">${SplitPurchase.rowHtml(firstAccountId)}</tbody>
            </table></div>
            <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="SplitPurchase.addRow(this.form)">+ Add line</button>
            <div class="split-summary" style="margin-top:8px; font-size:13px;"></div>`;
    },
    rowHtml(accountId) {
        const opts = SplitPurchase._acctOpts.replace(`value="${accountId}"`, `value="${accountId}" selected`);
        return `<tr>
            <td><select class="sp-acct" aria-label="Type of expense"><option value="">Choose...</option>${opts}</select></td>
            <td style="text-align:center;"><input type="checkbox" class="sp-tax" title="HST charged on this line" aria-label="HST on this line" checked onchange="SplitPurchase.recalc(this.form)"></td>
            <td><input class="sp-amt" type="number" step="0.01" aria-label="Amount before HST" oninput="SplitPurchase.recalc(this.form)"></td>
            <td><input class="sp-memo" aria-label="Description"></td>
            <td><select class="sp-cj" aria-label="${T('Customer')}:${T('Job')}">${CustomerJob.options(null)}</select></td>
            <td style="text-align:center;"><input type="checkbox" class="sp-bill" title="Billable to the customer"></td>
            <td style="white-space:nowrap;">
                <button type="button" class="btn btn-sm btn-secondary" title="Put what is left to assign on this line" onclick="SplitPurchase.rest(this)">Rest</button>
                <button type="button" class="btn btn-sm btn-secondary" title="Remove this line" aria-label="Remove line" onclick="SplitPurchase.removeRow(this)">&times;</button>
            </td></tr>`;
    },
    addRow(form) {
        form.querySelector('.split-lines').insertAdjacentHTML('beforeend', SplitPurchase.rowHtml(''));
        SplitPurchase.recalc(form);
    },
    removeRow(btn) {
        const form = btn.form, body = btn.closest('tbody');
        if (body.rows.length > 1) btn.closest('tr').remove();
        SplitPurchase.recalc(form);
    },
    cents(n) { return Math.round((Number(n) || 0) * 100) / 100; },
    totals(form) {
        let lines = 0, taxed = 0;
        form.querySelectorAll('.split-lines tr').forEach(row => {
            const v = parseFloat(row.querySelector('.sp-amt').value) || 0;
            lines += v;
            if (row.querySelector('.sp-tax') && row.querySelector('.sp-tax').checked) taxed += v;
        });
        lines = SplitPurchase.cents(lines);
        const hst = SplitPurchase.cents(HstField.value(form));
        const paid = SplitPurchase.cents(parseFloat(form.amount.value) || 0);
        return { lines, taxed: SplitPurchase.cents(taxed), hst, paid, left: SplitPurchase.cents(paid - hst - lines) };
    },
    // As in QuickBooks: the HST is 13% of the ticked lines and the amount
    // paid is the lines plus HST, until either is typed in by hand.
    autofill(form) {
        let t = SplitPurchase.totals(form);
        const hstBox = form.tax_amount;
        if (hstBox && !hstBox.dataset.manual) {
            hstBox.value = t.taxed ? (Math.round(t.taxed * HstField.RATE) / 100).toFixed(2) : '';
        }
        t = SplitPurchase.totals(form);
        if (!form.amount.dataset.manual && t.lines) {
            form.amount.value = SplitPurchase.cents(t.lines + t.hst).toFixed(2);
        }
    },
    // a box typed in by hand keeps its value (cleared: back to automatic)
    wire(form) {
        if (!form) return;
        const mark = el => { if (!el) return; el.addEventListener('input', () => {
            if (el.value === '') delete el.dataset.manual; else el.dataset.manual = '1';
            SplitPurchase.recalc(form);
        }); };
        mark(form.amount);
        mark(form.tax_amount);
        SplitPurchase.recalc(form);
    },
    rest(btn) {
        const form = btn.form, amt = btn.closest('tr').querySelector('.sp-amt');
        const t = SplitPurchase.totals(form);
        amt.value = SplitPurchase.cents((parseFloat(amt.value) || 0) + t.left).toFixed(2);
        SplitPurchase.recalc(form);
    },
    recalc(form) {
        const box = form && form.querySelector('.split-summary');
        if (!box) return;
        SplitPurchase.autofill(form);
        const t = SplitPurchase.totals(form);
        const words = form.querySelector('#cheque-words');
        if (words) words.textContent = t.paid > 0 ? chequeWords(t.paid) : '';
        const ok = t.left === 0 && t.paid > 0;
        box.innerHTML = `Expenses ${formatCurrency(t.lines)} + Total Taxes ${formatCurrency(t.hst)} = <strong>${formatCurrency(SplitPurchase.cents(t.lines + t.hst))}</strong>
            &nbsp;·&nbsp; Amount paid ${formatCurrency(t.paid)} &nbsp;·&nbsp;
            <span style="font-weight:700; color:${ok ? 'var(--text-success, #2e7d32)' : 'var(--text-danger, #c62828)'};">${ok ? 'Balanced' : `Left to assign ${formatCurrency(t.left)}`}</span>`;
    },
    // [{account_id, amount, memo, is_billable, job_id|customer_id}], or an
    // error to show
    collect(form) {
        const lines = [];
        let error = null;
        form.querySelectorAll('.split-lines tr').forEach((row, i) => {
            const amount = parseFloat(row.querySelector('.sp-amt').value) || 0;
            if (!amount) return;
            const acct = row.querySelector('.sp-acct').value;
            if (!acct && !error) error = `Line ${i + 1}: choose the type of expense.`;
            lines.push({
                account_id: acct ? parseInt(acct) : null,
                amount: amount.toFixed(2),
                memo: row.querySelector('.sp-memo').value || null,
                is_billable: !!row.querySelector('.sp-bill').checked,
                ...CustomerJob.parse(row.querySelector('.sp-cj').value),
            });
        });
        if (!error && !lines.length) error = 'Add a line: the type of expense, the customer and the amount.';
        const t = SplitPurchase.totals(form);
        if (!error && t.left !== 0) error = `The lines plus HST come to ${formatCurrency(SplitPurchase.cents(t.lines + t.hst))}, but the amount paid is ${formatCurrency(t.paid)}. Left to assign: ${formatCurrency(t.left)}.`;
        return { lines, error };
    },
    // the detail view's lines
    detailHtml(lines) {
        if (!lines || !lines.length) return '';
        return `<div class="table-container" style="margin-top:8px;"><table><thead><tr><th scope="col">Type of expense</th><th scope="col">${T('Customer')}:${T('Job')}</th><th scope="col">Description</th><th scope="col" class="amount">Amount</th></tr></thead><tbody>
            ${lines.map(l => `<tr><td>${escapeHtml(l.account_name || '')}</td><td>${escapeHtml(l.customer_job || '')}</td><td>${escapeHtml(l.memo || '')}${l.is_billable ? ' <em>(billable)</em>' : ''}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`).join('')}
            </tbody></table></div>`;
    },
};

const ExpensesPage = {
    _vendors: [],
    _accounts: [],

    async render() {
        const expenses = await API.get('/expenses');
        let html = `
            <div class="page-header">
                <h2>Expenses</h2>
                <button class="btn btn-primary" ${App.shortcutAttrs('E', 'Enter Expense')} onclick="ExpensesPage.showForm()">+ Enter Expense</button>
            </div>`;

        if (expenses.length === 0) {
            html += `<div class="empty-state"><p>No expenses recorded yet</p>
                <p style="font-size:12px; color:var(--gray-600);">Enter a receipt you've already paid — by card, cheque, e-transfer or cash. Scan it to fill the form.</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Cheque # / Ref</th><th scope="col">Payee</th><th scope="col">Expense Account</th><th scope="col">Paid From</th>
                <th scope="col">Status</th><th scope="col" class="amount">HST</th><th scope="col" class="amount">Amount</th><th scope="col"></th></tr></thead><tbody>`;
            for (const x of expenses) {
                const isVoid = x.status === 'void';
                html += `<tr data-status="${x.status}"${isVoid ? ' class="row--dim" style="text-decoration:line-through;"' : ''}>
                    <td>${formatDate(x.date)}</td>
                    <td>${escapeHtml(x.reference || '')}</td>
                    <td>${escapeHtml(x.payee || '')}</td>
                    <td>${escapeHtml(x.expense_account_name || '')}</td>
                    <td>${escapeHtml(x.paid_from_account_name || '')}</td>
                    <td style="text-decoration:none;">${statusBadge(x.status)}</td>
                    <td class="amount">${x.tax_amount ? formatCurrency(x.tax_amount) : ''}</td>
                    <td class="amount">${formatCurrency(x.amount)}</td>
                    <td class="actions" style="text-decoration:none;">
                        <button class="btn btn-sm btn-secondary" onclick="ExpensesPage.showDetail(${x.id})">View</button>
                        ${isVoid ? '' : `<button data-destructive class="btn btn-sm btn-danger" onclick="ExpensesPage.void(${x.id})">Void</button>`}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    // Accounts money can leave from: the chart's bank and credit-card
    // accounts (bank_kind), nothing else.
    paidFromAccounts(accounts) {
        return accounts.filter(a => a.bank_kind);
    },

    async showForm() {
        const [vendors, accounts] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/accounts'),
        ]);
        ExpensesPage._vendors = vendors;
        ExpensesPage._accounts = accounts;
        const classGroup = await classFormGroupHtml();
        // bsq: the type of expense and the customer go on each line
        await CustomerJob.load();
        // Expense and cost-of-goods accounts: materials are bought against
        // 5100 Materials Cost as often as against an expense (W-L18).
        const expenseAccts = PurchaseAccounts.filter(accounts);
        const paidFrom = ExpensesPage.paidFromAccounts(accounts);
        const hstOn = await HstField.enabled();
        ExpensesPage._hstOn = hstOn;
        const acctOpt = a => `<option value="${a.id}">${accountLabel(a)}</option>`;
        const checking = paidFrom.find(a => /checking/i.test(a.name || '')) || paidFrom[0];

        openModal('Enter Purchase (cheque, debit, cash, e-transfer)', `
            <form id="expense-form" onsubmit="ExpensesPage.save(event)">
                ${ScanHelper.scanRowHtml()}
                <div class="cheque-box">
                    <div class="form-grid">
                        <div class="form-group"><label>Bank account (paid from) *</label>
                            <select name="paid_from_account_id" required>${paidFrom.map(acctOpt).join('')}</select></div>
                        <div class="form-group"><label>No.</label>
                            <input name="reference" placeholder="Cheque number, or debit, cash, e-transfer"></div>
                        <div class="form-group"><label>Date *</label>
                            <input name="date" type="date" required value="${todayISO()}"></div>
                        <div class="form-group"><label>Pay to the order of *</label>
                            ${VendorQuickAdd.html(vendors, { id: 'expense-vendor', onchange: 'ExpensesPage.vendorSelected(this.value)' })}</div>
                        <div class="form-group"><label>$ Amount (with HST) *</label>
                            <input name="amount" type="number" step="0.01" min="0.01" required title="Filled in from the lines and HST; type over it to enter the total from the cheque or receipt"></div>
                        <div class="form-group full-width cheque-words-row"><span id="cheque-words" aria-live="polite"></span> <span class="cheque-dollars">Dollars</span></div>
                        <div class="form-group"><label>Address</label>
                            <div id="expense-vendor-address" aria-label="Payee address" class="cheque-address"></div></div>
                        <div class="form-group"><label>Memo</label>
                            <textarea name="memo" rows="1"></textarea></div>
                        ${classGroup}${Nonprofit.functionFormGroupHtml()}
                    </div>
                </div>
                ${SplitPurchase.tableHtml(expenseAccts, '')}
                <div class="form-grid" style="margin-top:8px;">${HstField.html(hstOn).replace('<label>HST included</label>', '<label>Total Taxes (HST)</label>')}</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="ScanHelper.discard(); closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Expense</button>
                </div>
            </form>`, { wide: true });
        const pf = document.querySelector('#expense-form [name="paid_from_account_id"]');
        if (pf && checking) pf.value = checking.id;
        SplitPurchase.wire(document.getElementById('expense-form'));
        ScanHelper.wire(ExpensesPage._applyScan, ExpensesPage._applyScanField, ExpensesPage._scanFieldTarget);
    },

    vendorSelected(vendorId) {
        if (!vendorId || vendorId === VendorQuickAdd.NEW) return;
        const vendor = ExpensesPage._vendors.find(v => v.id == vendorId);
        const sel = document.querySelector('#expense-form .split-lines .sp-acct');
        if (vendor && vendor.default_expense_account_id && sel && !sel.value) {
            sel.value = vendor.default_expense_account_id;
        }
        const box = document.getElementById('expense-vendor-address');
        if (box) {
            const v = vendor || {};
            const cityLine = ([v.city, v.state].filter(Boolean).join(', ') + (v.zip ? '  ' + v.zip : '')).trim();
            box.textContent = [v.address1, v.address2, cityLine].filter(Boolean).join('\n');
        }
    },

    // Where each canvas field lands — the canvas outlines these inputs in
    // the field's color so the form doubles as the legend.
    _scanFieldTarget(fieldKey) {
        const form = $('#expense-form');
        if (!form) return null;
        if (fieldKey === 'date') return form.querySelector('[name="date"]');
        if (fieldKey === 'merchant') return VendorQuickAdd.nameInput('expense-vendor') || form.querySelector('#expense-vendor');
        if (fieldKey === 'total') return form.querySelector('[name="amount"]');
        if (fieldKey === 'tax' && form.querySelector('[name="tax_amount"]')) return form.querySelector('[name="tax_amount"]');
        if (fieldKey === 'subtotal' || fieldKey === 'tax') return form.querySelector('[name="memo"]');
        if (fieldKey === 'reference') return form.querySelector('[name="reference"]');
        return null;
    },

    _noteLine(form, label, value) {
        const memo = form.querySelector('[name="memo"]');
        if (!memo) return;
        const re = new RegExp(`^${label}:`);
        const kept = memo.value.split('\n').filter(l => !re.test(l)).join('\n').replace(/\s*$/, '');
        memo.value = (kept ? kept + '\n' : '') + `${label}: $${value}`;
    },

    // Box-to-fix canvas: apply one re-read field. Returns {error} when the
    // value can't land, otherwise nothing.
    _applyScanField(fieldKey, value) {
        const form = $('#expense-form');
        if (!form) return;
        if (fieldKey === 'date') {
            form.querySelector('[name="date"]').value = value;
        } else if (fieldKey === 'merchant') {
            VendorQuickAdd.prefill('expense-vendor', value, ExpensesPage._vendors);
        } else if (fieldKey === 'total') {
            // The receipt total is what left the account — tax included.
            form.querySelector('[name="amount"]').value = parseFloat(value).toFixed(2);
        } else if (fieldKey === 'reference') {
            const ref = form.querySelector('[name="reference"]');
            if (ref) ref.value = value;
        } else if (fieldKey === 'subtotal') {
            ExpensesPage._noteLine(form, 'Subtotal', value);
        } else if (fieldKey === 'tax') {
            if (form.tax_amount) { form.tax_amount.value = parseFloat(value).toFixed(2); form.tax_amount.dataset.manual = '1'; SplitPurchase.recalc(form); }
            else ExpensesPage._noteLine(form, 'Tax', value);
        }
    },

    _applyScan(result) {
        const form = $('#expense-form');
        if (!form) return;
        if (result.date) form.querySelector('[name="date"]').value = result.date;
        const ref = form.querySelector('[name="reference"]');
        if (result.reference && ref && !ref.value) ref.value = result.reference;

        const merchant = result.merchant && result.merchant.value;
        if (merchant) {
            const match = VendorQuickAdd.prefill('expense-vendor', merchant, ExpensesPage._vendors);
            if (match) {
                ExpensesPage.vendorSelected(match.id);
            } else {
                const statusEl = $('#scan-status');
                if (statusEl) {
                    statusEl.textContent = `Detected: ${merchant} — new vendor; it's added when you save (or pick one from the list).`;
                }
            }
        }

        const total = parseFloat(result.total || '0');
        if (total > 0) { form.amount.value = total.toFixed(2); form.amount.dataset.manual = '1'; }
        if (result.tax_detected && result.tax) {
            if (form.tax_amount) { form.tax_amount.value = parseFloat(result.tax).toFixed(2); form.tax_amount.dataset.manual = "1"; }
            else ExpensesPage._noteLine(form, 'Tax', result.tax);
        }
        SplitPurchase.recalc(form);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const paidFrom = parseInt(form.paid_from_account_id.value);
        if (!(await Overdraft.confirm(paidFrom, parseFloat(form.amount.value) || 0))) return;
        if (!(await ClassWarn.ok(form))) return;
        const split = SplitPurchase.collect(form);
        if (split.error) { toast(split.error, 'error'); return; }
        try {
            const vendorId = await VendorQuickAdd.ensure('expense-vendor');
            const result = await API.post('/expenses', {
                date: form.date.value,
                vendor_id: vendorId,
                paid_from_account_id: paidFrom,
                amount: parseFloat(form.amount.value),
                tax_amount: HstField.value(form),
                reference: form.reference.value || null,
                memo: form.memo.value || null,
                class_id: classIdFromForm(form),
                ...Nonprofit.formPayload(form),
                lines: split.lines,
            });
            await ScanHelper.attachAfterSave('expense', result.id);
            toast('Expense recorded');
            closeModal();
            App.navigate('#/expenses');
        } catch (err) { toast(err.message, 'error'); }
    },

    /** Wrong account, wrong amount? Void posts the mirror-image entry
     *  (the original stays in the ledger) — then enter it again. */
    async void(id) {
        if (!confirm('Void this expense? A reversing entry will be posted; enter it again to correct it.')) return;
        try {
            await API.post(`/expenses/${id}/void`);
            toast('Expense voided');
            closeModal();
            App.navigate('#/expenses');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showDetail(id) {
        const x = await API.get(`/expenses/${id}`);
        const isVoid = x.status === 'void';
        openModal(`Expense — ${escapeHtml(x.payee || '')}${isVoid ? ' (VOID)' : ''}`, `
            <div style="font-size:13px; line-height:1.7;">
                <strong>Date:</strong> ${formatDate(x.date)}<br>
                ${x.reference ? `<strong>Cheque # / Ref:</strong> ${escapeHtml(x.reference)}<br>` : ''}
                <strong>Payee:</strong> ${escapeHtml(x.payee || '')}<br>
                <strong>Expense Account:</strong> ${escapeHtml(x.expense_account_name || '')}<br>
                <strong>Paid From:</strong> ${escapeHtml(x.paid_from_account_name || '')}<br>
                ${x.tax_amount ? `<strong>Before HST:</strong> ${formatCurrency(x.amount - x.tax_amount)}<br>
                <strong>HST (input tax credit):</strong> ${formatCurrency(x.tax_amount)}<br>` : ''}
                <strong>Amount paid:</strong> ${formatCurrency(x.amount)}<br>
                ${x.memo ? `<strong>Memo:</strong> ${escapeHtml(x.memo).replace(/\n/g, '<br>')}<br>` : ''}
                ${SplitPurchase.detailHtml(x.lines)}
            </div>
            <div style="margin-top:12px; border-top:1px solid var(--gray-300); padding-top:8px;">
                <div style="font-weight:700; font-size:12px; margin-bottom:4px;">Attachments</div>
                <div id="expense-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <div style="display:flex; gap:6px; align-items:center;">
                    <input type="file" id="expense-attach-file" aria-label="File to attach" style="font-size:11px;">
                    <button class="btn btn-sm btn-secondary" onclick="ExpensesPage.uploadAttachment(${x.id})">Attach</button>
                </div>
            </div>
            <div class="form-actions">
                ${isVoid ? '' : `<button data-destructive type="button" class="btn btn-danger" onclick="ExpensesPage.void(${x.id})">Void</button>`}
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        ExpensesPage.loadAttachments(x.id);
    },

    async loadAttachments(id) {
        const el = $('#expense-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/expense/${id}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                        ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                        ${a.file_size == null ? '' : `<span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>`}
                        <button data-destructive aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="ExpensesPage.deleteAttachment(${a.id},${id})" style="padding:0 4px; font-size:10px;">X</button>
                        ${storedFileNote(a)}
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(id) {
        const fileInput = $('#expense-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/expense/${id}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            fileInput.value = '';
            ExpensesPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachmentId, id) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachmentId}`);
            toast('Attachment deleted');
            ExpensesPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },
};

/**
 * Money leaving a bank account: before an expense or a bill payment is
 * saved, say so when it takes the account below zero, and let the user go
 * ahead or stop (macbase1 S-a: an expense and a pay run overdrew Checking
 * to -$2,986.90 without a word). The balance is the ledger balance the
 * register shows. A card is left alone — its balance is money owed, not
 * money in the bank. A failed lookup never blocks the save.
 */
const Overdraft = {
    // accountId, or null for "the server's default" named by defaultNumber.
    async confirm(accountId, amount, defaultNumber, action = 'Save') {
        if (!(amount > 0)) return true;
        let accounts;
        try { accounts = await API.get('/banking/overview'); } catch (e) { return true; }
        const acct = accounts.find(a => accountId ? a.account_id === accountId : a.account_number === defaultNumber);
        if (!acct || acct.bank_kind !== 'bank') return true;
        const after = PurchaseLines.cents(Number(acct.balance) - amount);
        if (after >= 0) return true;
        return confirm(`${acct.name} will be overdrawn by ${formatCurrency(-after)}. ${action} anyway?`);
    },
};
