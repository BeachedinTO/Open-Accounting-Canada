"""bsq: Canadian payroll deductions (CRA T4127), checked against CRA's own
published T4032 tables."""

from datetime import date
from decimal import Decimal as D

import pytest

import app.services.payroll_ca as m


@pytest.fixture
def year_2025(monkeypatch):
    """CRA's January 2025 figures, only to check the method against CRA's
    printed 2025 biweekly federal table."""
    t = dict(m.YEARS[(2026, 7)])
    t.update({
        "source": "T4127 January 2025 (test only)",
        "fed": [(D(0), D("0.15"), D(0)), (D(57375), D("0.205"), D(3156)), (D(114750), D("0.26"), D(9467)),
                (D(177882), D("0.29"), D(14803)), (D(253414), D("0.33"), D(24940))],
        "fed_low": D("0.15"), "bpaf": (D(16129), D(14538), D(177882), D(253414)), "cea": D(1471),
        "ympe": D(71300), "yampe": D(81200), "cpp_max": D("4034.10"), "cpp_base_max": D("3356.10"),
        "cpp2_max": D("396.00"), "ei_rate": D("0.0164"), "ei_max": D("1077.48"), "ei_max_insurable": D(65700),
    })
    monkeypatch.setitem(m.YEARS, (2025, 1), t)


# CRA T4032ON, January 1, 2025, biweekly federal tax: range midpoint ->
# (claim code 0, claim code 1). CRA rounds to the nearest 5 cents.
# (Above the CPP ceiling the printed tables cap the enhanced-CPP deduction
# and T4127 does not, so only pays below it are compared.)
CRA_2025 = {1501: ("200.80", "107.75"), 1997: ("269.60", "176.50"), 2489: ("352.00", "258.95")}


@pytest.mark.parametrize("gross", sorted(CRA_2025))
def test_federal_tax_matches_cras_table(year_2025, gross):
    c0, c1 = CRA_2025[gross]
    code1 = m.calculate(D(gross), date(2025, 3, 7), 26, m.Employee(), m.YTD()).federal_tax
    code0 = m.calculate(D(gross), date(2025, 3, 7), 26, m.Employee(td1_federal=D(0)), m.YTD()).federal_tax
    assert abs(code1 - D(c1)) <= D("0.08") and abs(code0 - D(c0)) <= D("0.08"), (code1, code0)


def test_cpp_and_ei_per_pay_and_their_maximums():
    t = m.YEARS[(2026, 7)]
    p = m.calculate(D("2000"), date(2026, 11, 6), 26, m.Employee(), m.YTD())
    assert p.cpp == ((D("2000") - D(3500) / 26) * D("0.0595")).quantize(D("0.01"))
    assert p.ei == D("32.60") and p.employer_ei == D("45.64")
    # the year's maximums stop them
    near = m.YTD(cpp=t["cpp_max"] - D("10"), ei=t["ei_max"] - D("5"), pensionable=D(70000))
    p = m.calculate(D("2000"), date(2026, 12, 4), 26, m.Employee(), near)
    assert (p.cpp, p.ei) == (D("10.00"), D("5.00"))


def test_cpp2_on_earnings_above_the_first_ceiling():
    t = m.YEARS[(2026, 7)]
    ytd = m.YTD(cpp=t["cpp_max"], pensionable=D(73600))
    p = m.calculate(D("3000"), date(2026, 12, 4), 26, m.Employee(), ytd)
    assert p.cpp == 0 and p.cpp2 == D("80.00")  # 4% of (76,600 - 74,600)


def test_no_cpp_past_70_and_a_missing_year_is_refused():
    p = m.calculate(D("2000"), date(2026, 11, 6), 26, m.Employee(birth_date=date(1950, 1, 1)), m.YTD())
    assert p.cpp == 0 and p.ei > 0
    with pytest.raises(m.TableMissing):
        m.calculate(D("2000"), date(2027, 1, 8), 26, m.Employee(), m.YTD())


def test_ontario_tax_parts():
    p = m.calculate(D("3000"), date(2026, 11, 6), 26, m.Employee(), m.YTD())
    assert p.ontario_tax > 0 and p.detail["V2"] == D("750.00")  # Health Premium over $72k: $600 + 25% of the excess, capped at $750
    assert p.net == p.gross - p.cpp - p.cpp2 - p.ei - p.federal_tax - p.ontario_tax


@pytest.fixture
def july_2025(monkeypatch):
    """CRA's July 2025 figures (T4127 121st edition), for bsq's own stubs."""
    t = dict(m.YEARS[(2026, 7)])
    t.update({
        "source": "T4127 121st edition, July 2025 (test only)",
        "fed": [(D(0), D("0.14"), D(0)), (D(57375), D("0.205"), D(3729)), (D(114750), D("0.26"), D(10041)),
                (D(177882), D("0.29"), D(15377)), (D(253414), D("0.33"), D(25514))],
        "fed_low": D("0.14"), "bpaf": (D(16129), D(14538), D(177882), D(253414)), "cea": D(1471),
        "on": [(D(0), D("0.0505"), D(0)), (D(52886), D("0.0915"), D(2168)), (D(105775), D("0.1116"), D(4294)),
               (D(150000), D("0.1216"), D(5794)), (D(220000), D("0.1316"), D(7994))],
        "on_basic": D(12747), "on_surtax": (D(5710), D("0.20"), D(7307), D("0.36")), "on_s2": D(294),
        "ympe": D(71300), "yampe": D(81200), "cpp_max": D("4034.10"), "cpp_base_max": D("3356.10"),
        "cpp2_max": D("396.00"), "ei_rate": D("0.0164"), "ei_max": D("1077.48"), "ei_max_insurable": D(65700),
    })
    monkeypatch.setitem(m.YEARS, (2025, 7), t)


PAY_DAY = date(2025, 11, 7)


def test_bsq_stub_hourly_with_vacation_pay(july_2025):
    """Sakiko, 76 h x $24 + 4% vacation pay = 1,896.96: QuickBooks withheld
    CPP 104.86, EI 31.11, income tax 239.02."""
    p = m.calculate(D("1896.96"), PAY_DAY, 26, m.Employee(),
                    m.YTD(cpp=D("2164.92"), ei=D("643.64"), pensionable=D("39212.16")))
    assert (p.cpp, p.cpp2, p.ei, p.income_tax) == (D("104.86"), 0, D("31.11"), D("239.02"))


def test_bsq_stub_near_the_cpp_ceiling(july_2025):
    """Orontes, 80 h x $36: CPP 163.35, EI 47.23, income tax 495.81."""
    p = m.calculate(D("2880.00"), PAY_DAY, 26, m.Employee(),
                    m.YTD(cpp=D("3289.06"), ei=D("956.62"), pensionable=D("58240")))
    assert (p.cpp, p.ei, p.income_tax) == (D("163.35"), D("47.23"), D("495.81"))


def test_bsq_stub_crossing_the_cpp_ceiling(july_2025):
    """Jiffy, 80 h x $39 on the pay that crosses the YMPE: CPP 126.24 (the
    rest of the maximum), EI 0 (maximum reached). CPP2 is CRA's 4% of the
    part above the YMPE (18.40); QuickBooks took 4% of the whole pay
    (124.80). With QuickBooks' CPP2 the tax is QuickBooks' 531.81."""
    ytd = m.YTD(cpp=D("3907.86"), ei=D("1077.48"), pensionable=D("68640"))
    p = m.calculate(D("3120.00"), PAY_DAY, 26, m.Employee(), ytd)
    assert (p.cpp, p.cpp2, p.ei) == (D("126.24"), D("18.40"), 0)
    qb = m.calculate(D("3120.00"), PAY_DAY, 26, m.Employee(),
                     m.YTD(cpp=D("3907.86"), ei=D("1077.48"), pensionable=D("71300")))
    assert (qb.cpp2, qb.income_tax) == (D("124.80"), D("531.81"))


def test_matches_quickbooks_stubs_of_nov_20_2026_with_their_year_to_date():
    """bsq's QuickBooks pay of Nov 20, 2026 (Oct 31 - Nov 13), with each
    employee's year-to-date from QuickBooks' Employee Earnings Summary.
    Jiffy had reached the 2026 CPP and EI maximums; Orontes reaches EI's."""
    from datetime import date as _d
    from decimal import Decimal as _D

    from app.services import payroll_ca as e

    pd = _d(2026, 11, 20)
    jiffy = e.calculate(_D("3289.60"), pd, 26, e.Employee(birth_date=_d(1989, 3, 20)),
                        e.YTD(cpp=_D("4230.45"), cpp2=_D("35.65"), ei=_D("1123.07"),
                              pensionable=_D("75491.20"), insurable=_D("75491.20")))
    assert (jiffy.cpp, jiffy.cpp2, jiffy.ei, jiffy.income_tax, jiffy.net) == \
        (_D("0.00"), _D("131.58"), _D("0.00"), _D("577.98"), _D("2580.04"))
    orontes = e.calculate(_D("3120.00"), pd, 26, e.Employee(birth_date=_d(1991, 12, 17)),
                          e.YTD(cpp=_D("3814.17"), ei=_D("1096.46"),
                                pensionable=_D("67200"), insurable=_D("67200")))
    assert (orontes.cpp, orontes.cpp2, orontes.ei) == (_D("177.63"), _D("0.00"), _D("26.61"))
    # QuickBooks took $557.86 of income tax. T4127 works out to $557.8528
    # (federal $365.5041 + Ontario $192.3487), which rounds to $557.85: one
    # cent less, and net one cent more. Rounding the parts or the total
    # differently doesn't reach QuickBooks' figure; left as T4127 gives it.
    assert (orontes.income_tax, orontes.net) == (_D("557.85"), _D("2357.91"))
