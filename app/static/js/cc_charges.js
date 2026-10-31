/**
 * Credit Card Charges — Enter CC expenses
 * DR Expense Account, CR Credit Card Payable (2100)
 */
const CCChargesPage = {
    async render() {
        const charges = await API.get('/cc-charges');
        let html = `
            <div class="page-header">
                <h2>Credit Card Charges</h2>
                <button class="btn btn-primary" onclick="CCChargesPage.showForm()">+ Enter Charge</button>
            </div>`;

        if (charges.length === 0) {
            html += '<div class="empty-state"><p>No credit card charges recorded yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Reference</th><th scope="col">Payee</th><th scope="col">Account</th><th scope="col">Card</th>
                <th scope="col" class="amount">HST</th><th scope="col" class="amount">Amount</th><th scope="col"></th></tr></thead><tbody>`;
            for (const c of charges) {
                const voided = c.status === 'void';
                html += `<tr style="${voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                    <td>${formatDate(c.date)}</td>
                    <td>${escapeHtml(c.reference || '')}</td>
                    <td>${escapeHtml(c.description || '')}</td>
                    <td>${escapeHtml(c.account_name || '')}</td>
                    <td>${escapeHtml(c.card_account_name || '')}</td>
                    <td class="amount">${c.tax_amount ? formatCurrency(c.tax_amount) : ''}</td>
                    <td class="amount">${formatCurrency(c.amount)}</td>
                    <td>${voided ? '' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="CCChargesPage.voidCharge(${c.id})">Void</button>`}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async showForm() {
        const [allAccounts, cards] = await Promise.all([
            API.get('/accounts'),
            API.get('/accounts?bank=1&active_only=true'),
        ]);
        // Expense and cost-of-goods accounts (W-L18): a card buys materials too.
        const accounts = PurchaseAccounts.filter(allAccounts);
        const cardOpts = cards.filter(a => a.bank_kind === 'credit_card').map(a =>
            `<option value="${a.id}" ${a.account_number === '2100' ? 'selected' : ''}>${accountLabel(a)}</option>`
        ).join('');
        const classGroup = await classFormGroupHtml();
        // bsq: the type of expense and the customer go on each line, the
        // same lines as the Expenses form
        await CustomerJob.load();
        const hstOn = await HstField.enabled();
        const vendors = await API.get('/vendors?active_only=true').catch(() => []);
        const payeeList = `<datalist id="cc-payees">${vendors.map(v => `<option value="${escapeHtml(v.name)}">`).join('')}</datalist>`;

        openModal('Enter Credit Card Charge', `
            <form id="cc-form" onsubmit="CCChargesPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Payee / Vendor</label>
                        <input name="payee" list="cc-payees" autocomplete="off">${payeeList}</div>
                    <div class="form-group"><label>Card *</label>
                        <select name="card_account_id" required>${cardOpts}</select></div>
                    <div class="form-group"><label>Amount charged (with HST) *</label>
                        <input name="amount" type="number" step="0.01" required title="Filled in from the lines and HST; type over it to enter the total from the statement"></div>
                    ${HstField.html(hstOn)}
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                    ${classGroup}${Nonprofit.functionFormGroupHtml()}
                    <div class="form-group full-width"><label>Memo</label>
                        <textarea name="memo" rows="1"></textarea></div>
                </div>
                ${SplitPurchase.tableHtml(accounts, '')}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Charge</button>
                </div>
            </form>`, { wide: true });
        SplitPurchase.wire(document.getElementById('cc-form'));
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        if (!(await ClassWarn.ok(form))) return;
        const split = SplitPurchase.collect(form);
        if (split.error) { toast(split.error, 'error'); return; }
        try {
            await API.post('/cc-charges', {
                date: form.date.value,
                payee: form.payee.value || null,
                card_account_id: parseInt(form.card_account_id.value),
                lines: split.lines,
                amount: parseFloat(form.amount.value),
                tax_amount: HstField.value(form),
                reference: form.reference.value || null,
                memo: form.memo.value || null,
                class_id: classIdFromForm(form),
                ...Nonprofit.formPayload(form),
            });
            toast('Credit card charge recorded');
            closeModal();
            App.navigate('#/cc-charges');
        } catch (err) { toast(err.message, 'error'); }
    },

    async voidCharge(id) {
        if (!confirm('Void this charge? A reversing entry is posted.')) return;
        try {
            await API.post(`/cc-charges/${id}/void`);
            toast('Charge voided');
            App.navigate('#/cc-charges');
        } catch (err) { toast(err.message, 'error'); }
    },
};
