from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.colombia_holidays import build_holidays_dataframe


def parse_years(args: list[str]) -> list[int]:
    if args:
        return [int(arg) for arg in args]

    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")
    raw_years = os.getenv("FIN_SEM_SEMA_YEARS", "").strip()
    if raw_years:
        return [int(item.strip()) for item in raw_years.split(",") if item.strip()]

    current_year = datetime.now().year
    return [current_year, current_year + 1]


def main(args: list[str]) -> None:
    years = parse_years(args)
    holidays_df = build_holidays_dataframe(years)
    output_path = ROOT_DIR / "config" / "colombia_holidays_visual.csv"
    holidays_df.to_csv(output_path, index=False, encoding="utf-8")
    print(output_path)
    print(holidays_df.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
