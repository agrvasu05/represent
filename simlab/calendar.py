"""Regulatory calendar: NPCI execution windows and Indian bank working days.

Rule sources (encoded, not invented):
- NPCI circular effective Aug 2025: mandate executions/retries must run in
  non-peak hours. Peak = 10:00-13:00 and 17:00-21:30 IST.
- RBI settlement/working-day convention: 2nd & 4th Saturdays and all Sundays
  are non-working for banks (bank-holiday list simplified to weekends here;
  the simplification is flagged in DECISIONS.md).
- RBI DPSS circular 629 (Sep 2019): failed-debit auto-reversal TAT of T+5
  calendar days for merchant transactions, then Rs.100/day compensation.
"""
from __future__ import annotations

from dataclasses import dataclass

# Simulation clock: integer day (0..HORIZON) + slot label.
# Slots are the only times an action can execute. Peak slots exist so a
# non-compliant policy CAN violate the window rule and get caught.
SLOTS = ("0800", "1130", "1430", "1900", "2300")
PEAK_SLOTS = frozenset({"1130", "1900"})  # inside 10-13 / 17-21:30 IST
NON_PEAK_SLOTS = tuple(s for s in SLOTS if s not in PEAK_SLOTS)

# NPCI retry budget: 1 initial attempt + at most 3 re-presentations.
MAX_RETRIES = 3
# Pre-debit notification must precede a scheduled debit by >= 24h.
PDN_LEAD_DAYS = 1
# RBI e-mandate AFA-free ceiling (general category), in paise.
AFA_LIMIT_PAISE = 15_000_00
# RBI TAT: reversal due within T+5 for merchant debits; then Rs.100/day.
TAT_REVERSAL_DAYS = 5
TAT_COMPENSATION_PAISE_PER_DAY = 100_00


def is_sunday(day: int) -> bool:
    # Day 0 of the simulation is a Monday by convention.
    return day % 7 == 6


def is_second_or_fourth_saturday(day: int) -> bool:
    if day % 7 != 5:
        return False
    saturday_index = day // 7  # 0-based week number
    return saturday_index % 4 in (1, 3)


def is_working_day(day: int) -> bool:
    return not (is_sunday(day) or is_second_or_fourth_saturday(day))


def add_working_days(day: int, n: int) -> int:
    d = day
    while n > 0:
        d += 1
        if is_working_day(d):
            n -= 1
    return d


def day_of_month(day: int) -> int:
    """Simulation months are 30 days; returns 1..30."""
    return (day % 30) + 1


@dataclass(frozen=True)
class When:
    day: int
    slot: str

    def __post_init__(self) -> None:
        if self.slot not in SLOTS:
            raise ValueError(f"unknown slot {self.slot}")

    def is_peak(self) -> bool:
        return self.slot in PEAK_SLOTS

    def sort_key(self) -> tuple[int, int]:
        return (self.day, SLOTS.index(self.slot))
