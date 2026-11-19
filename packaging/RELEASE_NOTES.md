**Open Accounting Canada** — bsq Landscape Architects' Canadian edition, based on SlowBooks Pro 2026

> **SlowBooks Pro 2026 — originally created by Trent Von Holten**
> Open Accounting Canada is a modified fork of [SlowBooks Pro 2026](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026) (base version 2.23.5), used under the SlowBooks Pro 2026 Source Available License, version 2.0 (attached as `LICENSE.txt`, and shown and installed by the installer). It is not SlowBooks Pro and is not endorsed by its author. Thanks to [VonHoltenCodes](https://github.com/VonHoltenCodes), creator and maintainer of SlowBooks Pro, and to its contributors.
>
> Canadian edition: **Open Accounting Canada**, forked and maintained for bsq design inc. (Toronto) in [BeachedinTO/bsq-slowbooks](https://github.com/BeachedinTO/bsq-slowbooks), branch `bsq-canada`. For internal use; not sold or offered as a service.

### New in this release
- **Weekly Timesheet**, laid out like QuickBooks Desktop's (Payroll & HR → Timesheet): Customer:Job, Service Item, Payroll Item, Notes, Monday–Sunday, Total, Billable?; Previous/Next week, Copy Last Sheet, Save & Next Week.
- **Billable time on invoices**: billable timesheet hours for a customer's jobs are offered under "Time" when you start that customer's invoice, next to billable expenses. They come on as the service item, with the hours as the quantity and the notes as the description. The rate is entered on the invoice. Each entry is billed once; voiding the invoice frees it.
- **Pay runs take hours from timesheets** (as QuickBooks' "Use time data to create pay cheques"). Paid hours are locked on the timesheet until the run is voided.

### Canadian edition so far
- HST on sales and purchases (13% Ontario, input tax credits), quarterly HST return worksheet.
- Cheques, expenses and bills shaped like the QuickBooks forms; split lines by Customer:Job; billable costs onto invoices.
- Canadian payroll (Ontario, CRA T4127 July 2026 tables): CPP/CPP2, EI, federal and Ontario tax, vacation pay, WSIB, PD7A and T4 figures, pay stubs. Pays dated 2027 wait for CRA's January 2027 tables.
- QuickBooks Desktop import (lists, opening balances, transaction history, open invoices).

### Install
Close Open Accounting first, then run `OpenAccountingCanada-Setup-x64.exe`. Windows shows "unknown publisher" (the installer isn't code-signed): **More info → Run anyway**. Your company files stay where they are and are updated when first opened.

`SHA256SUMS.windows` lists the files' checksums. Open Accounting Canada calculates taxes and payroll from published CRA rules, but its figures carry no warranty; check them before filing or remitting.
