# ============================================================================
# bsq: Canadian payroll deductions for an employee in Ontario, outside
# Quebec: CPP (base + first additional), CPP2, EI, federal and Ontario
# income tax, following CRA's T4127 Payroll Deductions Formulas (the
# "option 1" annualised method, one pay at a time with year-to-date
# maximums).
#
# Every rate and amount lives in YEARS, one table per year (or part of a
# year when CRA publishes a July edition). A year whose figures are not yet
# published by CRA is refused rather than guessed.
#
# Sources: T4127 122nd edition (January 1, 2026) and 123rd edition (July 1,
# 2026); CRA's EI premium rates and maximums page (2027 EI).
# Withholding is an employer obligation: check a pay or two against CRA's
# Payroll Deductions Online Calculator (PDOC) whenever a table changes.
# ============================================================================

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

D = Decimal
CENT = D("0.01")


def _r(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


YEARS = {
    # T4127 123rd edition, effective July 1, 2026 (Ontario and federal tables
    # 8.1 / 8.2); CPP and EI from the 122nd edition (unchanged in July).
    (2026, 7): {
        "source": "T4127 123rd edition, effective July 1, 2026",
        "fed": [(D(0), D("0.14"), D(0)), (D(58523), D("0.205"), D(3804)), (D(117045), D("0.26"), D(10241)),
                (D(181440), D("0.29"), D(15685)), (D(258482), D("0.33"), D(26024))],
        "fed_low": D("0.14"),
        "bpaf": (D(16452), D(14829), D(181440), D(258482)),  # max, min, phase-out from, to
        "cea": D(1501),
        "on": [(D(0), D("0.0505"), D(0)), (D(53891), D("0.0915"), D(2210)), (D(107785), D("0.1116"), D(4376)),
               (D(150000), D("0.1216"), D(5876)), (D(220000), D("0.1316"), D(8076))],
        "on_low": D("0.0505"),
        "on_basic": D(12989),
        "on_surtax": (D(5818), D("0.20"), D(7446), D("0.36")),
        "on_s2": D(300), "on_y_per_dependant": D(554),
        "cpp_base": D("0.0495"), "cpp_first_add": D("0.0100"),
        "ympe": D(74600), "yampe": D(85000), "cpp_exemption": D(3500),
        "cpp_max": D("4230.45"), "cpp_base_max": D("3519.45"),
        "cpp2_rate": D("0.04"), "cpp2_max": D("416.00"),
        "ei_rate": D("0.0163"), "ei_max_insurable": D(68900), "ei_max": D("1123.07"),
        "ei_employer_factor": D("1.4"),
    },
}
# 2027: CRA has published EI (1.64%, $70,800 insurable, $1,161.12 maximum);
# CPP and tax tables arrive with the January 2027 T4127, which is expected to
# lower the base CPP rate to 4.75%. Until it is added here, 2027 is refused.
EI_2027 = {"ei_rate": D("0.0164"), "ei_max_insurable": D(70800), "ei_max": D("1161.12")}


class TableMissing(Exception):
    pass


def table_for(day: date) -> dict:
    keys = sorted(k for k in YEARS if k[0] == day.year and k[1] <= day.month)
    if not keys:
        raise TableMissing(
            f"CRA's payroll tables for {day:%B %Y} are not loaded yet. "
            "Add them from that year's T4127 before running a pay dated then."
        )
    return YEARS[keys[-1]]


@dataclass
class Employee:
    td1_federal: Decimal | None = None   # TD1 total claim; None = basic amount
    td1_ontario: Decimal | None = None   # TD1ON total claim; None = basic amount
    birth_date: date | None = None
    cpp_exempt: bool = False
    ei_exempt: bool = False
    extra_tax: Decimal = D(0)            # L: additional tax per pay asked on TD1
    on_dependants: int = 0               # Y: dependants under 19 / disabled (TD1ON)


@dataclass
class YTD:
    cpp: Decimal = D(0)      # base + first additional withheld this year
    cpp2: Decimal = D(0)
    ei: Decimal = D(0)
    pensionable: Decimal = D(0)   # pensionable earnings this year before this pay
    insurable: Decimal = D(0)


@dataclass
class Pay:
    gross: Decimal
    cpp: Decimal
    cpp2: Decimal
    ei: Decimal
    federal_tax: Decimal
    ontario_tax: Decimal
    employer_cpp: Decimal
    employer_cpp2: Decimal
    employer_ei: Decimal
    detail: dict = field(default_factory=dict)

    @property
    def income_tax(self) -> Decimal:
        return self.federal_tax + self.ontario_tax

    @property
    def net(self) -> Decimal:
        return self.gross - self.cpp - self.cpp2 - self.ei - self.income_tax

    @property
    def remittance(self) -> Decimal:
        """What this pay adds to the PD7A remittance."""
        return (self.cpp + self.employer_cpp + self.cpp2 + self.employer_cpp2
                + self.ei + self.employer_ei + self.income_tax)


def _age_ok(emp: Employee, day: date) -> bool:
    """CPP from the month after the 18th birthday to the month of the 70th."""
    if emp.birth_date is None:
        return True
    b = emp.birth_date
    months = (day.year - b.year) * 12 + (day.month - b.month)
    return 18 * 12 < months + (1 if day.day >= b.day else 0) and months <= 70 * 12


def _bracket(table, income):
    rate, const = table[0][1], table[0][2]
    for threshold, r, k in table:
        if income >= threshold:
            rate, const = r, k
    return rate, const


def calculate(gross, pay_date: date, periods: int, emp: Employee, ytd: YTD,
              pensionable=None, insurable=None) -> Pay:
    """One pay's deductions. `gross` is taxable pay for the period (vacation
    pay included); `pensionable` / `insurable` default to it."""
    t = table_for(pay_date)
    P = D(periods)
    I = D(gross)
    PI = D(pensionable if pensionable is not None else gross)
    IE = D(insurable if insurable is not None else gross)

    # --- CPP (base + first additional) -----------------------------------
    rate = t["cpp_base"] + t["cpp_first_add"]
    cpp = D(0)
    if not emp.cpp_exempt and _age_ok(emp, pay_date):
        cpp = max(D(0), min(t["cpp_max"] - ytd.cpp, rate * (PI - t["cpp_exemption"] / P)))
    cpp = _r(cpp)
    # --- CPP2 on pensionable earnings between YMPE and YAMPE ---------------
    cpp2 = D(0)
    if not emp.cpp_exempt and _age_ok(emp, pay_date):
        w = max(ytd.pensionable, t["ympe"])
        cpp2 = max(D(0), min(t["cpp2_max"] - ytd.cpp2,
                             (min(ytd.pensionable + PI, t["yampe"]) - w) * t["cpp2_rate"]))
    cpp2 = _r(cpp2)
    # --- EI ---------------------------------------------------------------
    ei = D(0)
    if not emp.ei_exempt:
        ei = max(D(0), min(t["ei_max"] - ytd.ei, IE * t["ei_rate"]))
    ei = _r(ei)

    # --- annual taxable income: A = P × (I − F5A) (no RRSP, union dues...) --
    # F5: the enhanced CPP (first additional and CPP2) is deductible, as
    # T4127 states it, this pay's amount annualised. (CRA's printed T4032
    # tables cap it at the year's maximum and so differ by a few cents a pay
    # at higher earnings; QuickBooks follows T4127, and so does this.)
    F5 = cpp * (t["cpp_first_add"] / rate) + cpp2
    A = P * (I - F5)
    if A < 0:
        A = D(0)

    # credits for CPP (base part only) and EI, annualised and capped; once a
    # maximum is reached, the credit is the year's maximum for the rest of
    # the year (T4127: "use the annual maximum contributions")
    if ytd.cpp + cpp >= t["cpp_max"]:
        cpp_credit_base = t["cpp_base_max"]
    else:
        cpp_credit_base = min(P * cpp * (t["cpp_base"] / rate), t["cpp_base_max"])
    if not emp.ei_exempt and ytd.ei + ei >= t["ei_max"]:
        ei_credit_base = t["ei_max"]
    else:
        ei_credit_base = min(P * ei, t["ei_max"])

    # --- federal ----------------------------------------------------------
    R, K = _bracket(t["fed"], A)
    bmax, bmin, lo, hi = t["bpaf"]
    if A <= lo:
        bpaf = bmax
    elif A >= hi:
        bpaf = bmin
    else:
        bpaf = bmax - (A - lo) * ((bmax - bmin) / (hi - lo))
    TC = emp.td1_federal if emp.td1_federal is not None else bpaf
    K1 = t["fed_low"] * TC
    K2 = t["fed_low"] * cpp_credit_base + t["fed_low"] * ei_credit_base
    K4 = min(t["fed_low"] * A, t["fed_low"] * t["cea"])
    T3 = max(D(0), R * A - K - K1 - K2 - K4)
    T1 = T3

    # --- Ontario ----------------------------------------------------------
    V, KP = _bracket(t["on"], A)
    TCP = emp.td1_ontario if emp.td1_ontario is not None else t["on_basic"]
    K1P = t["on_low"] * TCP
    K2P = t["on_low"] * cpp_credit_base + t["on_low"] * ei_credit_base
    T4 = max(D(0), V * A - KP - K1P - K2P)
    s1, r1, s2, r2 = t["on_surtax"]
    V1 = D(0)
    if T4 > s1:
        V1 = r1 * (T4 - s1)
    if T4 > s2:
        V1 += r2 * (T4 - s2)
    if A <= 20000:
        V2 = D(0)
    elif A <= 36000:
        V2 = min(D(300), D("0.06") * (A - 20000))
    elif A <= 48000:
        V2 = min(D(450), 300 + D("0.06") * (A - 36000))
    elif A <= 72000:
        V2 = min(D(600), 450 + D("0.25") * (A - 48000))
    elif A <= 200000:
        V2 = min(D(750), 600 + D("0.25") * (A - 72000))
    else:
        V2 = min(D(900), 750 + D("0.25") * (A - 200000))
    Y = t["on_y_per_dependant"] * emp.on_dependants
    S = max(D(0), min(T4 + V1, 2 * (t["on_s2"] + Y) - (T4 + V1)))
    T2 = max(D(0), T4 + V1 + V2 - S)

    fed = _r(T1 / P + D(emp.extra_tax or 0))
    on = _r(T2 / P)
    return Pay(
        gross=_r(I), cpp=cpp, cpp2=cpp2, ei=ei, federal_tax=fed, ontario_tax=on,
        employer_cpp=cpp, employer_cpp2=cpp2, employer_ei=_r(ei * t["ei_employer_factor"]),
        detail={"A": _r(A), "T1": _r(T1), "T2": _r(T2), "V1": _r(V1), "V2": _r(V2), "S": _r(S),
                "table": t["source"]},
    )
