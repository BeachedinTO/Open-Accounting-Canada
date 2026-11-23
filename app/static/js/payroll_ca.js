/**
 * bsq: Canadian payroll (CRA). Pay runs, employees, and the PD7A, WSIB and
 * T4 reports, over /api/payroll/ca. Deductions are worked out on the
 * server by CRA's T4127 formulas (app/services/payroll_ca.py).
 */
const PayrollCAPage = {
    _tab: 'runs',
    _tabs() {
        const t = (k, label) => `<button type="button" class="btn ${PayrollCAPage._tab === k ? 'btn-primary' : 'btn-secondary'}" onclick="PayrollCAPage.show('${k}')">${label}</button>`;
        return `<div style="display:flex; gap:8px; margin-bottom:12px;">${t('runs', 'Pay Runs')}${t('employees', 'Employees')}${t('reports', 'PD7A, WSIB &amp; T4')}</div>`;
    },
    show(tab) {
        PayrollCAPage._tab = tab;
        if (tab === 'employees') return App.navigate('#/employees');
        if (location.hash === '#/payroll') return App.refresh();
        App.navigate('#/payroll');
    },
    money: v => formatCurrency(v || 0),

    async render() {
        if (PayrollCAPage._tab === 'employees') PayrollCAPage._tab = 'runs';
        if (PayrollCAPage._tab === 'reports') return PayrollCAPage.renderReports();
        const runs = await API.get('/payroll/ca/runs');
        const rows = runs.map(r => `<tr>
            <td>${formatDate(r.pay_date)}</td><td>${formatDate(r.period_start)} – ${formatDate(r.period_end)}</td>
            <td>${r.employees}</td><td>${statusBadge(r.status)}</td>
            <td class="amount">${PayrollCAPage.money(r.total_gross)}</td><td class="amount">${PayrollCAPage.money(r.total_net)}</td>
            <td class="actions"><button class="btn btn-sm btn-secondary" onclick="App.navigate('#/payroll/${r.id}')">Open</button></td></tr>`).join('');
        return `<div class="page-header"><h2>Payroll (Canada)</h2>
                <div class="btn-group"><button class="btn btn-primary" onclick="PayrollCAPage.newRun()">+ New Pay Run</button></div></div>
            ${PayrollCAPage._tabs()}
            <div class="table-container"><table>
                <thead><tr><th scope="col">Pay date</th><th scope="col">Pay period</th><th scope="col">Employees</th><th scope="col">Status</th>
                <th scope="col" class="amount">Gross</th><th scope="col" class="amount">Net</th><th scope="col"></th></tr></thead>
                <tbody>${rows || '<tr><td colspan="7" style="text-align:center;color:var(--gray-500)">No pay runs yet. Add employees, then start a pay run.</td></tr>'}</tbody>
            </table></div>
            <p style="font-size:12px;color:var(--gray-600);margin-top:10px;">Deductions follow CRA's Payroll Deductions Formulas (T4127) for Ontario, 26 pays a year. Check a pay against CRA's online calculator (PDOC) whenever CRA's tables change.</p>`;
    },

    // a new run: two weeks ending the Friday before, paid a week later
    async newRun() {
        const emps = (await API.get('/payroll/ca/employees')).filter(e => e.is_active);
        if (!emps.length) { toast('Add your employees first.', 'error'); return App.navigate('#/employees'); }
        const runs = await API.get('/payroll/ca/runs');
        const last = runs.find(r => r.status !== 'void');
        const iso = d => d.toISOString().slice(0, 10);
        const add = (s, n) => { const d = new Date(s + 'T12:00:00'); d.setDate(d.getDate() + n); return iso(d); };
        const start = last ? add(last.period_end, 1) : todayISO();
        const end = add(start, 13);
        const pay = last ? add(last.pay_date, 14) : add(end, 7);
        const rows = emps.map(e => `<tr data-emp="${e.id}">
            <td><input type="checkbox" class="pr-on" checked aria-label="Pay ${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}"></td>
            <td>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</td>
            <td><input class="pr-hours" type="number" step="0.25" min="0" value="${e.default_hours ?? ''}" data-default="${e.default_hours ?? ''}" style="width:90px" aria-label="Hours">
                <span class="pr-hours-from" style="font-size:11px;color:var(--gray-600);"></span></td>
            <td><input class="pr-rate" type="number" step="0.01" min="0" value="${e.pay_rate}" style="width:100px" aria-label="Rate"></td>
            <td>${e.vacation_pct ? e.vacation_pct + '%' : '—'}</td></tr>`).join('');
        openModal('New Pay Run', `<form id="pr-form" onsubmit="PayrollCAPage.saveRun(event)">
            <div class="form-grid">
                <div class="form-group"><label>Period start *</label><input name="period_start" type="date" required value="${start}" onchange="PayrollCAPage.timesheetHours()"></div>
                <div class="form-group"><label>Period end *</label><input name="period_end" type="date" required value="${end}" onchange="PayrollCAPage.timesheetHours()"></div>
                <div class="form-group"><label>Pay date *</label><input name="pay_date" type="date" required value="${pay}"></div>
            </div>
            <div class="table-container"><table class="line-items-table">
                <thead><tr><th scope="col">Pay</th><th scope="col">Employee</th><th scope="col">Hours</th><th scope="col">Rate</th><th scope="col">Vacation pay</th></tr></thead>
                <tbody>${rows}</tbody></table></div>
            <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button type="submit" class="btn btn-primary">Calculate</button></div></form>`, { wide: true });
        await PayrollCAPage.timesheetHours();
    },
    // bsq: hours come from the timesheets in the pay period, as QuickBooks'
    // "Use time data to create pay cheques"; with none, the usual hours
    async timesheetHours() {
        const f = document.getElementById('pr-form');
        if (!f || !f.period_start.value || !f.period_end.value) return;
        let hours = {};
        try {
            hours = await API.get(`/payroll/ca/timesheet-hours?period_start=${f.period_start.value}&period_end=${f.period_end.value}`);
        } catch (e) { return; }
        $$('#pr-form tbody tr').forEach(r => {
            const inp = r.querySelector('.pr-hours'), note = r.querySelector('.pr-hours-from');
            const h = hours[r.dataset.emp];
            if (h) { inp.value = h; note.textContent = 'from timesheets'; }
            else { inp.value = inp.dataset.default; note.textContent = inp.dataset.default ? '' : 'no timesheet: enter hours'; }
        });
    },
    async saveRun(e) {
        e.preventDefault();
        const f = e.target;
        const lines = $$('#pr-form tbody tr').filter(r => r.querySelector('.pr-on').checked).map(r => ({
            employee_id: parseInt(r.dataset.emp), hours: parseFloat(r.querySelector('.pr-hours').value) || 0,
            rate: parseFloat(r.querySelector('.pr-rate').value) || 0,
        }));
        try {
            const run = await API.post('/payroll/ca/runs', { period_start: f.period_start.value, period_end: f.period_end.value, pay_date: f.pay_date.value, lines });
            closeModal();
            App.navigate(`#/payroll/${run.id}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        const [run, accounts] = await Promise.all([API.get(`/payroll/ca/runs/${id}`), API.get('/accounts')]);
        const banks = accounts.filter(a => a.bank_kind && a.is_active !== false && a.account_type !== 'liability');
        const m = PayrollCAPage.money;
        const rows = run.stubs.map(s => `<tr>
            <td>${escapeHtml(s.employee)}</td><td class="amount">${s.hours.toFixed(2)}</td>
            <td class="amount">${m(s.gross)}${s.vacation_pay ? `<br><span style="font-size:11px;color:var(--gray-600)">incl. vacation ${m(s.vacation_pay)}</span>` : ''}</td>
            <td class="amount">${m(s.cpp + s.cpp2)}</td><td class="amount">${m(s.ei)}</td><td class="amount">${m(s.income_tax)}</td>
            <td class="amount"><strong>${m(s.net)}</strong></td><td>${escapeHtml(s.pay_method_label)}</td>
            <td>${run.status === 'draft'
                ? `<input class="pr-ref" data-emp="${s.employee_id}" maxlength="40" style="width:110px" placeholder="${s.pay_method === 'cheque' ? 'Cheque #' : escapeHtml(s.pay_method_label)}">`
                : escapeHtml(s.payment_ref)}</td>
            <td><a class="btn btn-sm btn-secondary" href="/api/payroll/ca/runs/${run.id}/stubs/${s.id}/pdf" target="_blank" rel="noopener">Stub</a></td></tr>`).join('');
        const t = k => run.stubs.reduce((a, s) => a + s[k], 0);
        const remit = t('cpp') + t('cpp2') + t('ei') + t('income_tax') + t('employer_cpp') + t('employer_cpp2') + t('employer_ei');
        const actions = run.status === 'draft' ? `
                <select id="pr-bank" aria-label="Pay from">${banks.map(b => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('')}</select>
                <button class="btn btn-primary" onclick="PayrollCAPage.process(${run.id})">Process Pay Run</button>
                <button class="btn btn-danger" data-destructive onclick="PayrollCAPage.remove(${run.id})">Delete Draft</button>`
            : run.status === 'processed' ? `<button class="btn btn-danger" data-destructive onclick="PayrollCAPage.voidRun(${run.id})">Void</button>` : '';
        openModal(`Pay Run ${formatDate(run.pay_date)}`, `
            <p>Pay period ${formatDate(run.period_start)} – ${formatDate(run.period_end)} · ${statusBadge(run.status)}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Employee</th><th scope="col" class="amount">Hours</th><th scope="col" class="amount">Gross</th>
                <th scope="col" class="amount">CPP</th><th scope="col" class="amount">EI</th><th scope="col" class="amount">Tax</th>
                <th scope="col" class="amount">Net</th><th scope="col">Paid by</th><th scope="col">Cheque # / Ref</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody>
                <tfoot><tr><td><strong>Total</strong></td><td></td><td class="amount">${m(t('gross'))}</td><td class="amount">${m(t('cpp') + t('cpp2'))}</td>
                <td class="amount">${m(t('ei'))}</td><td class="amount">${m(t('income_tax'))}</td><td class="amount"><strong>${m(t('net'))}</strong></td><td></td><td></td><td></td></tr></tfoot>
            </table></div>
            <p style="font-size:13px;">Employer CPP ${m(t('employer_cpp') + t('employer_cpp2'))} · Employer EI ${m(t('employer_ei'))} · WSIB ${m(t('wsib'))}<br>
            <strong>Owed to the Receiver General for this pay: ${m(remit)}</strong> (goes on the month's PD7A)</p>
            <div class="form-actions" style="gap:8px;">${actions}<button class="btn btn-secondary" onclick="closeModal(); App.navigate('#/payroll')">Close</button></div>`, { wide: true });
    },
    async process(id) {
        const bank = $('#pr-bank')?.value;
        // each paycheque's cheque number or e-transfer reference (blank: an
        // e-transfer or direct deposit is named as such; a cheque has none yet)
        const refs = {};
        $$('.pr-ref').forEach(i => { if (i.value.trim()) refs[i.dataset.emp] = i.value.trim(); });
        if (!(confirm('Process this pay run? Each employee\'s pay is posted as its own cheque or e-transfer from the bank, and the stubs become final.'))) return;
        try { await API.post(`/payroll/ca/runs/${id}/process`, { bank_account_id: parseInt(bank), refs }); toast('Pay run processed: paycheques posted'); PayrollCAPage.view(id); }
        catch (err) { toast(err.message, 'error'); }
    },
    async voidRun(id) {
        if (!(confirm('Void this pay run? A reversing entry is posted; the stubs no longer count.'))) return;
        try { await API.post(`/payroll/ca/runs/${id}/void`); toast('Pay run voided'); PayrollCAPage.view(id); }
        catch (err) { toast(err.message, 'error'); }
    },
    async remove(id) {
        if (!(confirm('Delete this draft pay run?'))) return;
        try { await API.del(`/payroll/ca/runs/${id}`); closeModal(); App.navigate('#/payroll'); App.refresh(); }
        catch (err) { toast(err.message, 'error'); }
    },

    // ── employees ─────────────────────────────────────────────────────────
    async renderEmployees() {
        PayrollCAPage._tab = 'employees';
        const emps = (await API.get('/payroll/ca/employees')).filter(e => ActiveLists.shows('employees', e));
        ActiveLists.remember('employees', emps);
        const rows = emps.map(e => `<tr>
            <td>${escapeHtml(e.last_name)}, ${escapeHtml(e.first_name)}</td><td>${escapeHtml(e.sin_display)}</td>
            <td class="amount">${PayrollCAPage.money(e.pay_rate)}/h × ${e.hours_from_timesheets ? 'timesheet hours' : (e.default_hours ?? '—')}</td>
            <td>${e.vacation_pct ? e.vacation_pct + '% on each pay' : 'Paid time off'}</td>
            <td>${{ direct_deposit: 'Direct deposit', etransfer: 'E-transfer' }[e.pay_method] || 'Cheque'}</td>
            <td>${e.is_active ? 'Active' : 'Inactive'}</td>
            <td class="actions"><button class="btn btn-sm btn-secondary" onclick="PayrollCAPage.employeeForm(${e.id})">Edit</button>
                ${ActiveLists.buttonHtml('employees', e)}</td></tr>`).join('');
        return `<div class="page-header"><h2>Employees</h2>
                <div class="btn-group"><button class="btn btn-primary" onclick="PayrollCAPage.employeeForm()">+ New Employee</button></div></div>
            ${PayrollCAPage._tabs()}
            <div class="toolbar">${ActiveLists.pickerHtml('employees')}</div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Name</th><th scope="col">SIN</th><th scope="col" class="amount">Pay</th><th scope="col">Vacation</th>
                <th scope="col">Paid by</th><th scope="col">Status</th><th scope="col"></th></tr></thead>
                <tbody>${rows || `<tr><td colspan="7" style="text-align:center;color:var(--gray-500)">${ActiveLists.emptyText('employees') || 'No employees yet.'}</td></tr>`}</tbody>
            </table></div>`;
    },
    async employeeForm(id = null) {
        const e = id ? (await API.get('/payroll/ca/employees')).find(x => x.id === id) : {
            province: 'ON', default_hours: 80, vacation_pct: 0, pay_method: 'cheque', is_active: true };
        const v = k => escapeHtml(e[k] == null ? '' : String(e[k]));
        openModal(id ? `Edit ${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}` : 'New Employee', `
            <form id="emp-ca-form" onsubmit="PayrollCAPage.saveEmployee(event, ${id || 'null'})">
            <div class="form-grid">
                <div class="form-group"><label>First name *</label><input name="first_name" required value="${v('first_name')}"></div>
                <div class="form-group"><label>Last name *</label><input name="last_name" required value="${v('last_name')}"></div>
                <div class="form-group"><label>SIN *</label><input name="sin" inputmode="numeric" ${id ? '' : 'required'} placeholder="${id ? 'unchanged: ' + v('sin_display') : '000 000 000'}"></div>
                <div class="form-group"><label>Date of birth *</label><input name="birth_date" type="date" required value="${v('birth_date')}"></div>
                <div class="form-group full-width"><label>Address *</label><input name="address1" required value="${v('address1')}"></div>
                <div class="form-group"><label>Unit / line 2</label><input name="address2" value="${v('address2')}"></div>
                <div class="form-group"><label>City *</label><input name="city" required value="${v('city')}"></div>
                <div class="form-group"><label>Province</label><input name="province" maxlength="2" value="${v('province') || 'ON'}"></div>
                <div class="form-group"><label>Postal code *</label><input name="postal_code" required value="${v('postal_code')}"></div>
                <div class="form-group"><label>Email</label><input name="email" type="email" value="${v('email')}"></div>
                <div class="form-group"><label>Hire date</label><input name="hire_date" type="date" value="${v('hire_date')}"></div>
                <div class="form-group"><label>Hourly rate *</label><input name="pay_rate" type="number" step="0.01" min="0" required value="${v('pay_rate')}"></div>
                <div class="form-group"><label>Hours each pay</label><input name="default_hours" type="number" step="0.25" min="0" value="${v('default_hours')}"></div>
                <div class="form-group"><label style="font-weight:normal;"><input name="hours_from_timesheets" type="checkbox" ${(id ? e.hours_from_timesheets : false) ? 'checked' : ''}> Use timesheet hours to create pay cheques</label>
                    <small style="display:block;color:var(--gray-600);">Off: paid the hours each pay (timesheets for job tracking only)</small></div>
                <div class="form-group"><label>Vacation pay on each pay (%)</label><input name="vacation_pct" type="number" step="0.01" min="0" value="${v('vacation_pct')}" title="4 for 4%; 0 for paid time off"></div>
                <div class="form-group"><label>TD1 federal claim</label><input name="td1_federal" type="number" step="0.01" min="0" value="${v('td1_federal')}" placeholder="basic amount"></div>
                <div class="form-group"><label>TD1ON Ontario claim</label><input name="td1_ontario" type="number" step="0.01" min="0" value="${v('td1_ontario')}" placeholder="basic amount"></div>
                <div class="form-group"><label>Extra tax each pay</label><input name="extra_tax" type="number" step="0.01" min="0" value="${v('extra_tax') || 0}"></div>
                <div class="form-group"><label>Paid by</label><select name="pay_method" onchange="document.getElementById('emp-dd').hidden = this.value !== 'direct_deposit'">
                    <option value="cheque" ${!['direct_deposit', 'etransfer'].includes(e.pay_method) ? 'selected' : ''}>Cheque</option>
                    <option value="etransfer" ${e.pay_method === 'etransfer' ? 'selected' : ''}>E-transfer</option>
                    <option value="direct_deposit" ${e.pay_method === 'direct_deposit' ? 'selected' : ''}>Direct deposit</option></select></div>
                <div class="form-group"><label><input type="checkbox" name="cpp_exempt" ${e.cpp_exempt ? 'checked' : ''}> CPP exempt</label>
                    <label><input type="checkbox" name="ei_exempt" ${e.ei_exempt ? 'checked' : ''}> EI exempt</label>
                    <label><input type="checkbox" name="is_active" ${e.is_active ? 'checked' : ''}> Active</label></div>
            </div>
            <div id="emp-dd" class="form-grid" ${e.pay_method === 'direct_deposit' ? '' : 'hidden'}>
                <div class="form-group"><label>Institution # (3)</label><input name="bank_institution" inputmode="numeric" value="${v('bank_institution')}"></div>
                <div class="form-group"><label>Transit # (5)</label><input name="bank_transit" inputmode="numeric" value="${v('bank_transit')}"></div>
                <div class="form-group"><label>Account #</label><input name="bank_account" inputmode="numeric" placeholder="${e.bank_account_last4 ? 'unchanged: …' + v('bank_account_last4') : ''}"></div>
            </div>
            <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button type="submit" class="btn btn-primary">Save Employee</button></div></form>`, { wide: true });
    },
    async saveEmployee(ev, id) {
        ev.preventDefault();
        const f = ev.target;
        const num = n => f[n].value === '' ? null : parseFloat(f[n].value);
        const body = {
            first_name: f.first_name.value, last_name: f.last_name.value, birth_date: f.birth_date.value || null,
            address1: f.address1.value, address2: f.address2.value || null, city: f.city.value, province: f.province.value || 'ON',
            postal_code: f.postal_code.value, email: f.email.value || null, hire_date: f.hire_date.value || null,
            pay_rate: num('pay_rate') || 0, default_hours: num('default_hours'), vacation_pct: num('vacation_pct') || 0,
            hours_from_timesheets: f.hours_from_timesheets.checked,
            td1_federal: num('td1_federal'), td1_ontario: num('td1_ontario'), extra_tax: num('extra_tax') || 0,
            pay_method: f.pay_method.value, cpp_exempt: f.cpp_exempt.checked, ei_exempt: f.ei_exempt.checked, is_active: f.is_active.checked,
            bank_institution: f.bank_institution.value || null, bank_transit: f.bank_transit.value || null,
        };
        if (f.sin.value.trim()) body.sin = f.sin.value;
        if (f.bank_account.value.trim()) body.bank_account = f.bank_account.value;
        try {
            await API[id ? 'put' : 'post'](id ? `/payroll/ca/employees/${id}` : '/payroll/ca/employees', body);
            closeModal(); toast('Employee saved'); App.navigate('#/employees'); App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    // ── reports ───────────────────────────────────────────────────────────
    async renderReports() {
        setTimeout(() => { PayrollCAPage.pd7a(); PayrollCAPage.wsib(); PayrollCAPage.t4(); }, 0);
        const now = new Date();
        const y = now.getFullYear(), mo = now.getMonth() + 1;
        const prev = mo === 1 ? [y - 1, 12] : [y, mo - 1];
        return `<div class="page-header"><h2>Payroll Reports</h2></div>${PayrollCAPage._tabs()}
            <div class="card" style="padding:14px;margin-bottom:12px;"><h3 style="margin-top:0">PD7A remittance (monthly)</h3>
                <label>Month <input id="pd7a-month" type="month" value="${prev[0]}-${String(prev[1]).padStart(2, '0')}" onchange="PayrollCAPage.pd7a()"></label>
                <div id="pd7a-out" style="margin-top:8px;"></div></div>
            <div class="card" style="padding:14px;margin-bottom:12px;"><h3 style="margin-top:0">WSIB (quarterly)</h3>
                <label>Year <input id="wsib-year" type="number" value="${y}" style="width:90px" onchange="PayrollCAPage.wsib()"></label>
                <label>Quarter <select id="wsib-q" onchange="PayrollCAPage.wsib()">${[1, 2, 3, 4].map(q => `<option ${q === Math.ceil(mo / 3) ? 'selected' : ''}>${q}</option>`).join('')}</select></label>
                <div id="wsib-out" style="margin-top:8px;"></div></div>
            <div class="card" style="padding:14px;"><h3 style="margin-top:0">T4 slips (year-end)</h3>
                <label>Year <input id="t4-year" type="number" value="${y}" style="width:90px" onchange="PayrollCAPage.t4()"></label>
                <a class="btn btn-sm btn-secondary" id="t4-csv" href="/api/payroll/ca/reports/t4.csv?year=${y}">Download CSV</a>
                <div id="t4-out" style="margin-top:8px;"></div></div>
`;
    },
    async pd7a() {
        const [yy, mm] = $('#pd7a-month').value.split('-').map(Number);
        const p = await API.get(`/payroll/ca/reports/pd7a?year=${yy}&month=${mm}`);
        const m = PayrollCAPage.money;
        $('#pd7a-out').innerHTML = `<table><tbody>
            <tr><td>Gross payroll in the month</td><td class="amount">${m(p.gross_payroll)}</td></tr>
            <tr><td>Employees in the last pay period</td><td class="amount">${p.employees_last_pay}</td></tr>
            <tr><td>CPP (employee + employer, incl. CPP2)</td><td class="amount">${m(p.cpp)}</td></tr>
            <tr><td>EI (employee + employer)</td><td class="amount">${m(p.ei)}</td></tr>
            <tr><td>Tax deducted</td><td class="amount">${m(p.income_tax)}</td></tr>
            <tr><td><strong>Total remittance</strong></td><td class="amount"><strong>${m(p.total)}</strong></td></tr>
            <tr><td>Due</td><td class="amount">${formatDate(p.due_date)}</td></tr></tbody></table>`;
    },
    async wsib() {
        const w = await API.get(`/payroll/ca/reports/wsib?year=${$('#wsib-year').value}&quarter=${$('#wsib-q').value}`);
        $('#wsib-out').innerHTML = `Insurable earnings ${formatDate(w.from)} – ${formatDate(w.to)}: <strong>${PayrollCAPage.money(w.insurable_earnings)}</strong>
            × ${w.rate}% = premium <strong>${PayrollCAPage.money(w.premium)}</strong>${w.rate ? '' : ' <em>(set the WSIB rate in Settings)</em>'}`;
    },
    async t4() {
        const yy = $('#t4-year').value;
        $('#t4-csv').href = `/api/payroll/ca/reports/t4.csv?year=${yy}`;
        const t = await API.get(`/payroll/ca/reports/t4?year=${yy}`);
        const m = PayrollCAPage.money;
        $('#t4-out').innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Employee</th><th scope="col" class="amount">14 Employment</th><th scope="col" class="amount">16 CPP</th><th scope="col" class="amount">16A CPP2</th>
            <th scope="col" class="amount">18 EI</th><th scope="col" class="amount">22 Tax</th><th scope="col" class="amount">24 EI earnings</th><th scope="col" class="amount">26 CPP earnings</th></tr></thead>
            <tbody>${t.slips.map(s => `<tr><td>${escapeHtml(s.name)}</td><td class="amount">${m(s.box_14)}</td><td class="amount">${m(s.box_16)}</td><td class="amount">${m(s.box_16A)}</td>
            <td class="amount">${m(s.box_18)}</td><td class="amount">${m(s.box_22)}</td><td class="amount">${m(s.box_24)}</td><td class="amount">${m(s.box_26)}</td></tr>`).join('') || '<tr><td colspan="8">No pays this year.</td></tr>'}</tbody></table></div>
            <p>T4 Summary: ${t.summary.slips} slips · employer CPP ${m(t.summary.employer_cpp + t.summary.employer_cpp2)} · employer EI ${m(t.summary.employer_ei)} · total deductions ${m(t.summary.total_deductions)}</p>`;
    },
};
