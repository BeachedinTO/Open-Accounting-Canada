/**
 * bsq: Weekly Timesheet, laid out as QuickBooks Desktop's.
 * One employee, one week (Monday to Sunday). Each row: Customer:Job,
 * Service Item, Payroll Item, Notes (they go on the invoice with billed
 * time), the hours for each day, the row's total and Billable?. Billable
 * hours on a job are offered on that customer's next invoice.
 */
const TimesheetPage = {
    _emp: '',
    _week: '',        // Monday, YYYY-MM-DD
    _emps: [],
    _items: [],
    _sheet: null,
    BLANK_ROWS: 4,

    _iso(d) { return d.toISOString().slice(0, 10); },
    _date(iso) { return new Date(iso + 'T00:00:00Z'); },
    _monday(iso) {
        const d = TimesheetPage._date(iso);
        d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
        return TimesheetPage._iso(d);
    },
    _addDays(iso, n) {
        const d = TimesheetPage._date(iso);
        d.setUTCDate(d.getUTCDate() + n);
        return TimesheetPage._iso(d);
    },
    _fmt(iso, withYear) {
        const d = TimesheetPage._date(iso);
        const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()];
        return `${m} ${d.getUTCDate()}${withYear ? ', ' + d.getUTCFullYear() : ''}`;
    },
    _hrs(v) { const n = Number(v || 0); return n ? n.toFixed(2) : ''; },

    async render() {
        const [emps, items] = await Promise.all([
            API.get('/employees?active_only=true'),
            API.get('/items?active_only=true'),
            CustomerJob.load(),
        ]);
        TimesheetPage._emps = emps || [];
        TimesheetPage._items = (items || []).filter(i => i.is_active !== false
            && ['service', 'labor'].includes(String(i.item_type || '').toLowerCase()));
        if (!TimesheetPage._week) TimesheetPage._week = TimesheetPage._monday(todayISO());
        if (!TimesheetPage._emps.some(e => String(e.id) === String(TimesheetPage._emp))) {
            TimesheetPage._emp = TimesheetPage._emps.length ? String(TimesheetPage._emps[0].id) : '';
        }
        const empOpts = TimesheetPage._emps.map(e =>
            `<option value="${e.id}" ${String(e.id) === TimesheetPage._emp ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`).join('');
        const html = `
            <div class="page-header">
                <h2>Timesheet</h2>
                <div style="display:flex; gap:8px;">
                    <button type="button" class="btn btn-secondary" onclick="TimesheetPage.go(-7)">&#9664; Previous</button>
                    <button type="button" class="btn btn-secondary" onclick="TimesheetPage.go(7)">Next &#9654;</button>
                </div>
            </div>
            ${TimesheetPage._emps.length ? `
            <div style="display:flex; gap:24px; align-items:flex-end; margin-bottom:12px; flex-wrap:wrap;">
                <div class="form-group" style="margin:0; min-width:240px;"><label for="ts-emp">Name</label>
                    <select id="ts-emp" onchange="TimesheetPage.pick(this.value)">${empOpts}</select></div>
                <div class="form-group" style="margin:0;"><label for="ts-week">Week of</label>
                    <div style="display:flex; gap:8px; align-items:center;">
                        <strong id="ts-week-label"></strong>
                        <input id="ts-week" type="date" value="${TimesheetPage._week}" onchange="TimesheetPage.jump(this.value)" title="Pick any day of the week" aria-label="Week of">
                    </div></div>
            </div>
            <div id="ts-sheet"></div>` : '<div class="empty-state"><p>Add an employee first (Payroll → Employees).</p></div>'}`;
        if (TimesheetPage._emps.length) setTimeout(() => TimesheetPage.load(), 0);
        return html;
    },

    async load(copyFrom = null) {
        const box = document.getElementById('ts-sheet');
        if (!box) return;
        const emp = TimesheetPage._emp, week = TimesheetPage._week;
        let sheet;
        try {
            sheet = await API.get(`/time-entries/week?employee_id=${encodeURIComponent(emp)}&week_of=${week}`);
        } catch (err) { toast(err.message, 'error'); return; }
        if (emp !== TimesheetPage._emp || week !== TimesheetPage._week) return;  // changed meanwhile
        if (copyFrom) {
            // Copy Last Sheet: last week's rows and hours, onto this week's
            sheet.rows = sheet.rows.concat(copyFrom.rows.map(r => ({ ...r, entry_ids: [null, null, null, null, null, null, null], locked: [null, null, null, null, null, null, null] })));
        }
        TimesheetPage._sheet = sheet;
        const lbl = document.getElementById('ts-week-label');
        if (lbl) lbl.textContent = `${TimesheetPage._fmt(sheet.days[0])} to ${TimesheetPage._fmt(sheet.days[6], true)}`;
        const days = ['M', 'Tu', 'W', 'Th', 'F', 'Sa', 'Su'];
        const head = sheet.days.map((d, i) =>
            `<th scope="col" class="amount">${days[i]} <br><span style="font-weight:normal;">${TimesheetPage._date(d).getUTCDate()}</span></th>`).join('');
        const rows = sheet.rows.map(r => TimesheetPage.rowHtml(r)).join('')
            + Array.from({ length: TimesheetPage.BLANK_ROWS }, () => TimesheetPage.rowHtml(null)).join('');
        box.innerHTML = `<form id="ts-form" onsubmit="event.preventDefault(); TimesheetPage.save()">
            <div class="table-container"><table class="timesheet-table">
                <thead><tr>
                    <th scope="col" style="width:15%;">${T('Customer')}:${T('Job')}</th>
                    <th scope="col" style="width:12%;">Service Item</th>
                    <th scope="col" style="width:10%;">Payroll Item</th>
                    <th scope="col" style="width:14%;">Notes</th>
                    ${head}
                    <th scope="col" class="amount" style="width:5%;">Total</th>
                    <th scope="col" style="width:6%; text-align:center;">Billable?</th>
                </tr></thead>
                <tbody id="ts-rows">${rows}</tbody>
                <tfoot><tr>
                    <td colspan="4" style="text-align:right; font-weight:700;">Totals</td>
                    ${sheet.days.map((_, i) => `<td class="amount ts-day-total" data-day="${i}" style="font-weight:700;"></td>`).join('')}
                    <td class="amount" id="ts-grand" style="font-weight:700;"></td><td></td>
                </tr></tfoot>
            </table></div>
            <p style="color:var(--gray-600); font-size:12px; margin:6px 0;">Notes go on the ${T('invoice')} with billed time. Billable hours on a ${T('customer')} or ${T('job')} are offered when you create that ${T('customer')}'s ${T('invoice')}.</p>
            <div class="form-actions" style="justify-content:space-between;">
                <div style="display:flex; gap:8px;">
                    <button type="button" class="btn btn-secondary" onclick="TimesheetPage.copyLast()">Copy Last Sheet</button>
                    <button type="button" class="btn btn-secondary" onclick="TimesheetPage.addRow()">+ Add Row</button>
                </div>
                <div style="display:flex; gap:8px;">
                    <button type="button" class="btn btn-secondary" data-readonly-ok onclick="TimesheetPage.load()">Revert</button>
                    <button type="submit" class="btn btn-primary">Save</button>
                    <button type="button" class="btn btn-primary" onclick="TimesheetPage.save(7)">Save &amp; Next Week</button>
                </div>
            </div></form>`;
        // a read-only sign-in sees the sheet locked (it arrives after the page)
        if (typeof App !== 'undefined' && App.isReadOnly && App.isReadOnly()) App.readOnlyPass(box);
        TimesheetPage.totals();
        document.querySelectorAll('#ts-rows .ts-notes').forEach(t => TimesheetPage.fit(t));
    },

    // notes wrap, as QuickBooks' "Wrap text in Notes field"
    fit(t) {
        t.style.height = 'auto';
        if (t.scrollHeight) t.style.height = t.scrollHeight + 'px';
    },

    rowHtml(r) {
        const row = r || { job_id: null, service_item_id: null, pay_type: 'regular', notes: '', billable: false,
            hours: [0, 0, 0, 0, 0, 0, 0], locked: [null, null, null, null, null, null, null] };
        const locked = (row.locked || []).filter(Boolean);
        const lockAll = locked.length ? 'disabled' : '';
        const lockTitle = locked.length ? ` title="Some of these hours were ${escapeHtml(locked[0])}, so this row can't be changed"` : '';
        const items = '<option value="">—</option>' + TimesheetPage._items.map(i =>
            `<option value="${i.id}" ${Number(i.id) === Number(row.service_item_id) ? 'selected' : ''}>${escapeHtml(i.name)}</option>`).join('');
        // the Canadian pay run pays straight time; an older entry of overtime still shows as it is
        const pay = [['regular', 'Regular pay']].concat(row.pay_type === 'overtime' ? [['overtime', 'Overtime']] : [],
            row.pay_type === 'doubletime' ? [['doubletime', 'Double time']] : []).map(([v, l]) =>
            `<option value="${v}" ${row.pay_type === v ? 'selected' : ''}>${l}</option>`).join('');
        const cells = row.hours.map((h, i) => {
            const why = row.locked && row.locked[i];
            return `<td><input class="ts-h" data-day="${i}" type="text" inputmode="decimal" value="${TimesheetPage._hrs(h)}"
                style="width:100%; min-width:40px; box-sizing:border-box; text-align:right;${why ? ' background:var(--gray-100);' : ''}" ${why ? `disabled title="${escapeHtml(why)}"` : ''}
                oninput="TimesheetPage.totals(this)"></td>`;
        }).join('');
        return `<tr class="ts-row"${lockTitle}>
            <td><select class="ts-cj" style="width:100%;" ${lockAll} onchange="TimesheetPage.touched(this)">${CustomerJob.options(row.job_id)}</select></td>
            <td><select class="ts-item" style="width:100%;" ${lockAll} onchange="TimesheetPage.touched(this)">${items}</select></td>
            <td><select class="ts-pay" style="width:100%;" ${lockAll}>${pay}</select></td>
            <td><textarea class="ts-notes" rows="1" ${lockAll} style="width:100%; box-sizing:border-box; resize:vertical; overflow:hidden;" oninput="TimesheetPage.touched(this); TimesheetPage.fit(this)">${escapeHtml(row.notes || '')}</textarea></td>
            ${cells}
            <td class="amount ts-row-total" style="background:var(--gray-100); font-weight:600;"></td>
            <td style="text-align:center;"><input type="checkbox" class="ts-bill" ${row.billable ? 'checked' : ''} ${lockAll}></td>
        </tr>`;
    },

    addRow() {
        const tbody = document.getElementById('ts-rows');
        if (tbody) tbody.insertAdjacentHTML('beforeend', TimesheetPage.rowHtml(null));
    },

    // typing on the last row opens another, as QuickBooks' sheet always has room
    touched(el) {
        const tr = el.closest('tr');
        if (tr && !tr.nextElementSibling) TimesheetPage.addRow();
    },

    totals(el) {
        if (el) TimesheetPage.touched(el);
        const day = [0, 0, 0, 0, 0, 0, 0];
        document.querySelectorAll('#ts-rows tr').forEach(tr => {
            let sum = 0;
            tr.querySelectorAll('.ts-h').forEach(inp => {
                const v = parseFloat(inp.value) || 0;
                sum += v; day[Number(inp.dataset.day)] += v;
            });
            const cell = tr.querySelector('.ts-row-total');
            if (cell) cell.textContent = sum ? sum.toFixed(2) : '';
        });
        document.querySelectorAll('.ts-day-total').forEach(td => { td.textContent = day[Number(td.dataset.day)].toFixed(2); });
        const g = document.getElementById('ts-grand');
        if (g) g.textContent = day.reduce((a, b) => a + b, 0).toFixed(2);
    },

    _rows() {
        const out = [];
        document.querySelectorAll('#ts-rows tr').forEach(tr => {
            const hours = [...tr.querySelectorAll('.ts-h')].map(i => parseFloat(i.value) || 0);
            if (!hours.some(Boolean)) return;
            out.push({
                ...CustomerJob.parse(tr.querySelector('.ts-cj').value),
                service_item_id: parseInt(tr.querySelector('.ts-item').value) || null,
                pay_type: tr.querySelector('.ts-pay').value,
                notes: tr.querySelector('.ts-notes').value.trim() || null,
                billable: tr.querySelector('.ts-bill').checked,
                hours,
            });
        });
        return out;
    },

    async save(thenGo = 0) {
        const bad = [...document.querySelectorAll('#ts-rows .ts-h')].find(i => i.value.trim() && !/^\d*\.?\d+$|^\d+\.$/.test(i.value.trim()));
        if (bad) { bad.focus(); toast(`"${bad.value}" isn't a number of hours.`, 'error'); return; }
        try {
            await API.put('/time-entries/week', {
                employee_id: parseInt(TimesheetPage._emp), week_start: TimesheetPage._week, rows: TimesheetPage._rows(),
            });
            toast('Timesheet saved');
            // a customer billed for the first time now has its own job
            await CustomerJob.load();
            if (thenGo) TimesheetPage.go(thenGo);
            else await TimesheetPage.load();
        } catch (err) { toast(err.message, 'error'); }
    },

    async copyLast() {
        try {
            const prev = await API.get(`/time-entries/week?employee_id=${encodeURIComponent(TimesheetPage._emp)}&week_of=${TimesheetPage._addDays(TimesheetPage._week, -7)}`);
            if (!prev.rows.length) { toast('Last week has no time to copy.', 'error'); return; }
            await TimesheetPage.load(prev);
            toast('Last week copied: check the hours, then Save');
        } catch (err) { toast(err.message, 'error'); }
    },

    pick(empId) { TimesheetPage._emp = String(empId); TimesheetPage.load(); },
    jump(iso) {
        if (!iso) return;
        TimesheetPage._week = TimesheetPage._monday(iso);
        const inp = document.getElementById('ts-week');
        if (inp) inp.value = TimesheetPage._week;
        TimesheetPage.load();
    },
    go(days) { TimesheetPage.jump(TimesheetPage._addDays(TimesheetPage._week, days)); },
};
