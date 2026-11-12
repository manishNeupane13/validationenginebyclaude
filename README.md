# Fleet data check (dqp)

Connect Excel/CSV, Monday, Samsara and any other platform. Normalize everything to one common model, then find
duplicates, missing values, mismatches and spelling mistakes, and see which data lives in which platform.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'
dqp demo                       # optional: sample data with planted defects
streamlit run dqp/ui/app.py
pytest -q                      # 27 tests
```

Real platforms: open **Platforms & schedule > Add a platform**.
Tokens come from environment variables (`SAMSARA_API_TOKEN`, `MONDAY_API_TOKEN`) or are pasted per session.
They are never written to the database.

## Navigation

**Platform data** (one page per platform): rows, columns, search, "is in / is not in / contains / empty / duplicated"
filters on any column, an Issues column per row, column profile, CSV download, sync button.

**Checks & tools**: Overview, Issues (triage), Compare records (side by side), Find & look up (search everywhere,
paste a list and see which platforms contain each value), Coverage, Column mapping, Platforms & schedule.

## Pipeline

| Step | Module |
|---|---|
| Connector interface, Samsara, Monday, Excel | `dqp/connectors/` |
| Common schema, mapping suggestions, normalizer | `schema.py`, `mapping.py`, `normalize.py` |
| Missing, format, enum, exact duplicates | `rules.py` |
| Identity resolution (key, name, fuzzy name) | `resolve.py` |
| Missing in platform, mismatch, value missing | `crossrules.py` |
| Spelling and near-duplicate names | `spelling.py` |
| Coverage matrices | `coverage.py` |
| Lookups and search | `lookup.py` |
| Issues, triage, snapshots (SQLite) | `store.py` |
| Orchestration, schedule | `engine.py`, `scheduler.py`, `cli.py` |

Add a platform by subclassing `Connector` (`test_connection`, `list_entities`, `fetch`) and decorating with `@register("kind")`.
Add a field by editing `schema.py`. Add a rule by returning `Issue` objects and calling it in `engine.validate`.

## Please verify against your accounts

- Samsara: `/fleet/vehicles` and `/fleet/drivers` with `pagination.hasNextPage` / `endCursor` and the `after` parameter (`connectors/samsara.py`).
  Nested fields are flattened with dots, e.g. `staticAssignedVehicle.name`. Check the column names your account returns on the Column mapping page.
- Monday: GraphQL `items_page` / `next_items_page` with API-Version 2024-10 (`connectors/monday.py`). Column titles become field names.
- Thresholds: name link 92%, review 85%, spelling 78%, key contradiction below 80% similarity (`resolve.py`, `spelling.py`).

## Limits

- Fuzzy name matching compares all names pairwise: fine for thousands of rows, slow beyond roughly 10,000 per entity.
- The in-app scheduler runs only while the app is open; use `dqp run --every 60` or cron for always-on.
- SQLite suits one user or a small team. Move `store.py` to PostgreSQL for shared production use.
- The Cogoeld/ELD connector is not written because there is no API detail yet; export to CSV and use the Excel connector,
  or copy `connectors/samsara.py` as a template.
