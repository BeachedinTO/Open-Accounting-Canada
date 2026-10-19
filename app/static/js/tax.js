/**
 * bsq (Canada): HST — the GST/HST return worksheet (regular method) and
 * recording the return's payment or refund. Replaces the US Schedule C and
 * Pay Sales Tax page. Figures come from the ledger: 2200 HST Payable
 * (collected) and 1350 HST Recoverable (input tax credits).
 */
const TaxPage = {
    _quarters(year) {
        return [1, 2, 3, 4].map(q => {
            const m0 = (q - 1) * 3;
            const start = `${year}-${String(m0 + 1).padStart(2, '0')}-01`;
            const endDate = new Date(year, m0 + 3, 0);
            const end = `${year}-${String(m0 + 3).padStart(2, '0')}-${String(endDate.getDate()).padStart(2, '0')}`;
            return { label: `Q${q} ${year} (${['Jan–Mar', 'Apr–Jun', 'Jul–Sep', 'Oct–Dec'][q - 1]})`, start, end };
        });
    },

    async render() {
        const now = new Date();
        const year = now.getFullYear();
        const periods = [...TaxPage._quarters(year - 1), ...TaxPage._quarters(year)];
        // default: the last quarter that has ended
        const cur = Math.floor(now.getMonth() / 3);
        const def = periods[4 + cur - 1] || periods[3];
        const opts = periods.map(p => `<option value="${p.start}|${p.end}" ${p === def ? 'selected' : ''}>${p.label}</option>`).join('');
        setTimeout(() => TaxPage.generate(), 0);
        return `
            <div class="page-header">
                <h2>HST Return</h2>
                <div style="font-size:10px; color:var(--text-muted);">GST/HST return worksheet, regular method, from your books</div>
            </div>
            <div class="toolbar">
                <label for="hst-period" style="font-size:10px;font-weight:700;">Reporting period:</label>
                <select id="hst-period" onchange="TaxPage.pickPeriod(this.value)">${opts}<option value="custom">Custom dates…</option></select>
                <label style="font-size:10px;font-weight:700;">From:</label>
                <input type="date" id="tax-start" value="${def.start}" onchange="$('#hst-period').value='custom'">
                <label style="font-size:10px;font-weight:700;">To:</label>
                <input type="date" id="tax-end" value="${def.end}" onchange="$('#hst-period').value='custom'">
                <button class="btn btn-primary" onclick="TaxPage.generate()">Show</button>
            </div>
            <div style="background:#fef3c7;border:1px solid #fbbf24;padding:6px 10px;margin-bottom:12px;font-size:10px;color:#92400e;">
                These figures are worked out from your books to help you file. Check them before you file with CRA; adjustments (lines 104 and 107) are entered on the return by hand.
            </div>
            <div id="tax-results"></div>`;
    },

    pickPeriod(value) {
        if (value === 'custom') return;
        const [start, end] = value.split('|');
        $('#tax-start').value = start;
        $('#tax-end').value = end;
        TaxPage.generate();
    },

    async generate() {
        const start = $('#tax-start').value;
        const end = $('#tax-end').value;
        const box = $('#tax-results');
        if (!box) return;
        let d;
        try {
            d = await API.get(`/reports/hst-return?start_date=${start}&end_date=${end}`);
        } catch (err) { box.innerHTML = `<p style="color:var(--text-danger)">${escapeHtml(err.message)}</p>`; return; }
        TaxPage._last = d;
        const row = (line, label, value, strong = false) => `<tr${strong ? ' style="font-weight:700;"' : ''}>
            <td style="width:60px;">${line}</td><td>${label}</td><td class="amount">${value}</td></tr>`;
        const net = d.line_109;
        const result = net > 0 ? `Amount owing to CRA (line 115): <strong>${formatCurrency(net)}</strong>`
            : net < 0 ? `Refund claimed (line 114): <strong>${formatCurrency(-net)}</strong>`
            : 'Nothing owing and no refund.';
        const filed = d.filed_transaction_id
            ? `<div style="margin-top:10px; padding:8px; background:var(--primary-light); border-radius:4px; font-size:12px;">
                 Recorded as filed on ${formatDate(d.filed_date)}.
                 <button data-destructive class="btn btn-sm btn-danger" onclick="TaxPage.voidReturn(${d.filed_transaction_id})">Void</button></div>`
            : `<div class="form-actions" style="justify-content:flex-start;">
                 <button class="btn btn-primary" onclick="TaxPage.showRecord()">${net >= 0 ? 'Record payment to CRA' : 'Record refund from CRA'}</button></div>`;
        box.innerHTML = `
            <div class="table-container" style="max-width:720px;"><table>
                <thead><tr><th scope="col">Line</th><th scope="col">Description</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>
                    ${row('101', 'Sales and other revenue (before HST)', `${formatCurrency(d.line_101)} <span style="color:var(--text-muted);font-size:10px;">(enter $${d.line_101_rounded.toLocaleString()})</span>`)}
                    ${row('103', 'GST/HST collected or collectible', formatCurrency(d.line_103))}
                    ${row('104', 'Adjustments', formatCurrency(d.line_104))}
                    ${row('105', 'Total GST/HST and adjustments', formatCurrency(d.line_105), true)}
                    ${row('106', 'Input tax credits (ITCs)', formatCurrency(d.line_106))}
                    ${row('107', 'Adjustments', formatCurrency(d.line_107))}
                    ${row('108', 'Total ITCs and adjustments', formatCurrency(d.line_108), true)}
                    ${row('109', 'Net tax', formatCurrency(d.line_109), true)}
                </tbody></table></div>
            <p style="font-size:13px; margin-top:8px;">${result}</p>
            ${filed}`;
    },

    async showRecord() {
        const d = TaxPage._last;
        if (!d) return;
        const banks = (await API.get('/accounts?bank=1&active_only=true')).filter(a => a.bank_kind === 'bank');
        const opts = banks.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');
        const owing = d.line_109 >= 0;
        openModal(owing ? 'Record HST Payment to CRA' : 'Record HST Refund from CRA', `
            <form onsubmit="TaxPage.submitRecord(event)">
                <p style="font-size:12px;">Period ${formatDate(d.start_date)} to ${formatDate(d.end_date)}: clears ${formatCurrency(d.line_103)} from HST Payable and ${formatCurrency(d.line_106)} from HST Recoverable, and ${owing ? 'pays' : 'receives'} <strong>${formatCurrency(Math.abs(d.line_109))}</strong>.</p>
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>${owing ? 'Paid from' : 'Deposited to'} *</label>
                        <select name="bank_account_id" required><option value="">Select...</option>${opts}</select></div>
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="memo" placeholder="e.g. CRA confirmation number"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Record</button>
                </div>
            </form>`);
    },

    async submitRecord(e) {
        e.preventDefault();
        const form = e.target;
        const d = TaxPage._last;
        try {
            await API.post('/reports/hst-return/record', {
                start_date: d.start_date,
                end_date: d.end_date,
                date: form.date.value,
                bank_account_id: parseInt(form.bank_account_id.value),
                memo: form.memo.value || null,
            });
            toast('HST return recorded');
            closeModal();
            TaxPage.generate();
        } catch (err) { toast(err.message, 'error'); }
    },

    async voidReturn(id) {
        if (!confirm('Void this HST return entry? A reversing entry is posted and the period can be recorded again.')) return;
        try {
            await API.post(`/reports/hst-return/${id}/void`);
            toast('HST return entry voided');
            TaxPage.generate();
        } catch (err) { toast(err.message, 'error'); }
    },
};
