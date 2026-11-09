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
CRA_2025 = {1501: ("200.80", "107.75"), 1997: ("269.60", "176.50"), 2489: ("352.00", "258.95"),
            2993: ("452.75", "359.70"), 4013: ("661.85", "568.80")}


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
