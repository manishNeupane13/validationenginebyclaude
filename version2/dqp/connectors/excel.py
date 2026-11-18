from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from .base import Connector, register


@register("excel")
class ExcelConnector(Connector):
    """XLSX / XLS / CSV file. Each sheet is an entity (a CSV has one entity: its file name)."""

    def __init__(self, name: str, path: str = "", **extra: Any):
        super().__init__(name, path=path, **extra)
        self.path = Path(path)

    def test_connection(self) -> tuple[bool, str]:
        if not self.path.exists():
            return False, f"File not found: {self.path}"
        try:
            return True, f"Found {len(self.list_entities())} sheet(s) in {self.path.name}."
        except Exception as e:  # noqa: BLE001
            return False, f"Cannot read {self.path.name}: {e}"

    def list_entities(self) -> list[str]:
        if self.path.suffix.lower() in (".csv", ".tsv"):
            return [self.path.stem]
        return list(pd.ExcelFile(self.path).sheet_names)

    def _frame(self, entity: str) -> pd.DataFrame:
        if self.path.suffix.lower() in (".csv", ".tsv"):
            sep = "\t" if self.path.suffix.lower() == ".tsv" else ","
            df = pd.read_csv(self.path, dtype=str, keep_default_na=False, sep=sep)
        else:  # dtype=str keeps leading zeros in IDs and license numbers
            df = pd.read_excel(self.path, sheet_name=entity, dtype=str, keep_default_na=False)
        df.columns = [str(c).strip() for c in df.columns]
        return df

    def fetch(self, entity: str, since: str | None = None) -> Iterator[dict[str, Any]]:
        df = self._frame(entity)
        for i, rec in enumerate(df.to_dict("records")):
            row = {k: (v if str(v).strip() != "" else None) for k, v in rec.items()}
            row["_key"] = f"r{i + 2}"  # spreadsheet row number (header is row 1)
            yield row
