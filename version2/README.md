# Fleet data check (dqp)

Connect Excel/CSV, Monday, Samsara and any other platform. Normalize everything to one common model, then find
duplicates, missing values, mismatches and spelling mistakes, and see which data lives in which platform.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'
dqp demo                       # optional: sample data in its OWN file, data/demo.db (view it with DQP_DB=data/demo.db)
streamlit run dqp/ui/app.py   # your real data lives in data/dqp.db and starts empty
pytest -q                      # 53 tests
```

Real platforms: open **Platforms & schedule > Add a platform**.
Tokens come from environment variables (`SAMSARA_API_TOKEN`, `MONDAY_API_TOKEN`) or are pasted per session.
They are never written to the database.

## Navigation

**Platform data** (one page per platform): rows, columns, search, "is in / is not in / contains / empty / duplicated"
filters on any column, an Issues column per row, column profile, CSV download, sync button.

**Checks & tools**: Overview, Issues (triage), Compare records (side by side), Find & look up (search everywhere,
paste a list and see which platforms contain each value), Coverage, Column mapping, Platforms & schedule.

## Cleaning after each fetch

Page **Cleaning rules**. APIs return far more than you need, so cleaning runs between fetch and store:

1. fetch everything,
2. keep only mapped columns plus any you tick (everything else is never saved),
3. keep only rows that match your filters (for example `Status is not in terminated`),
4. tidy values: trim spaces, turn `N/A`, `null`, `-` into empty, drop empty rows (exact-duplicate rows are kept unless you turn that on),
5. save the smaller snapshot, with counts ("fetched 4,210 x 38, saved 3,980 x 9").

Only the names and fill counts of dropped columns are remembered, never their values. An optional untouched copy can be kept for 7 days
while you set things up (off by default). Rows removed by a filter are not checked, so a record removed from one platform is reported
as missing from it wherever it exists elsewhere. The page warns about this.

## AI second opinion with a human in the loop (optional)

Page **Review queue**. The model never changes data. It advises, you decide, and your decisions are remembered.

```bash
# any OpenAI-compatible server works; Ollama on your own machine keeps data local
ollama pull llama3.1:8b        # or any model you prefer
```

Then open Review queue > AI model settings (default `http://localhost:11434/v1`) and select **Test the model**.

| What | How it works |
|---|---|
| Possible matches | Pairs in the 85-92% name-similarity band are queued automatically. The model says same / different / unsure with a reason; you confirm. Your answer is applied on the next check and overrides the automatic matching. |
| Issue second opinions | The model reviews flagged mismatches, typos and format problems as real problem / false alarm / unsure and may propose a corrected value. |
| Approved fixes | Approved corrections become a per-platform to-do list (CSV). Nothing is written back to your platforms; the next sync closes the issue once the source is fixed. |
| Audit | Every decision, whether the model agreed, which model, and Undo. |
| Plain-language report | A short briefing written from the check's numbers. |

Safeguards: temperature 0, strict JSON that is validated (unusable answers become "unsure"), answers cached by input,
suggested license numbers, VINs, phones, emails and IDs are dropped unless they already appear in the evidence,
optional masking of phones and emails, a warning when the endpoint is not local, and auto-apply is off by default
(if enabled, only "same / different" answers above your confidence are applied, marked `auto`, and can be undone).

Decisions are keyed to platform row IDs. For Excel that is the row number, so inserting rows above a decided row can detach the decision.

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
| AI client, tasks, review queue | `llm/client.py`, `llm/tasks.py`, `llm/review.py` |
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
