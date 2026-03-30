from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.colombia_holidays import build_holidays_dataframe


def main(args: list[str]) -> None:
    years = [int(arg) for arg in args] if args else [datetime.now().year, datetime.now().year + 1]
    holidays_df = build_holidays_dataframe(years)
    print(holidays_df.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
