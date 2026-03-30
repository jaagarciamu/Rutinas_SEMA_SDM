from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd


def _sorted_holiday_rows(holiday_map: dict[date, str]) -> list[dict[str, str | int]]:
    rows = []
    for holiday_date, holiday_name in sorted(holiday_map.items()):
        rows.append(
            {
                "year": holiday_date.year,
                "date": holiday_date.isoformat(),
                "holiday_name": holiday_name,
                "day_name": holiday_date.strftime("%A"),
            }
        )
    return rows


def get_weekend_dates(year: int, include_friday: bool = True) -> set[date]:
    dates = pd.date_range(start=f"{year}-01-01", end=f"{year}-12-31", freq="D")
    day_names = {"Friday", "Saturday", "Sunday"} if include_friday else {"Saturday", "Sunday"}
    weekend = dates[dates.day_name().isin(day_names)]
    return {timestamp.date() for timestamp in weekend}


def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def move_to_monday(base_date: date) -> date:
    if base_date.weekday() == 0:
        return base_date
    return base_date + timedelta(days=(7 - base_date.weekday()))


def colombian_holiday_map(year: int) -> dict[date, str]:
    easter = easter_sunday(year)
    fixed_holidays = {
        date(year, 1, 1): "Ano Nuevo",
        date(year, 5, 1): "Dia del Trabajo",
        date(year, 7, 20): "Independencia de Colombia",
        date(year, 8, 7): "Batalla de Boyaca",
        date(year, 12, 8): "Inmaculada Concepcion",
        date(year, 12, 25): "Navidad",
        easter - timedelta(days=3): "Jueves Santo",
        easter - timedelta(days=2): "Viernes Santo",
    }
    emiliani_holidays = {
        move_to_monday(date(year, 1, 6)): "Reyes Magos",
        move_to_monday(date(year, 3, 19)): "Dia de San Jose",
        move_to_monday(date(year, 6, 29)): "San Pedro y San Pablo",
        move_to_monday(date(year, 8, 15)): "Asuncion de la Virgen",
        move_to_monday(date(year, 10, 12)): "Dia de la Raza",
        move_to_monday(date(year, 11, 1)): "Todos los Santos",
        move_to_monday(date(year, 11, 11)): "Independencia de Cartagena",
        move_to_monday(easter + timedelta(days=43)): "Ascension del Senor",
        move_to_monday(easter + timedelta(days=64)): "Corpus Christi",
        move_to_monday(easter + timedelta(days=71)): "Sagrado Corazon de Jesus",
    }
    return fixed_holidays | emiliani_holidays


def colombian_holidays(year: int) -> set[date]:
    return set(colombian_holiday_map(year).keys())


def build_special_dates(
    years: list[int],
    include_friday: bool,
    cutoff_date: date | None = None,
) -> list[date]:
    active_cutoff = cutoff_date or datetime.now().date()
    special_dates: set[date] = set()
    for year in years:
        special_dates.update(get_weekend_dates(year, include_friday))
        special_dates.update(colombian_holidays(year))
    return sorted(
        {current_date for current_date in special_dates if current_date < active_cutoff},
        reverse=True,
    )


def build_holidays_dataframe(years: list[int]) -> pd.DataFrame:
    rows = []
    for year in years:
        rows.extend(_sorted_holiday_rows(colombian_holiday_map(year)))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    sample_years = [datetime.now().year, datetime.now().year + 1]
    df = build_holidays_dataframe(sample_years)
    print(df.to_string(index=False))
