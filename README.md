# Library Manager

Python CLI (standard library only) for running a small library: books, members,
loans, reservations and reports, all stored in a single JSON file.

```
python library.py              # interactive menu (data in library.json)
python library.py --db my.json # use another data file
python library.py --stats      # print the stats dashboard and exit
python library.py --selftest   # run the built-in checks
```

## Features

- **Books** — add/list, copies tracking, ranked fuzzy search over title/author/ISBN
  (substring hits first, then `difflib` similarity, so typos like "dnue" find "Dune").
- **Members** — unique IDs, validated and de-duplicated emails, editable details.
- **Loans** — 14-day due dates, max 3 loans per member, availability per copy.
- **Reservations** — FIFO hold queue per book; a returned copy is auto-checked-out to
  the first eligible member in line (members at their loan limit keep their place).
- **Overdue & fines** — overdue report sorted by days late, 0.25/day fine capped at
  10.00, per-member loan history with fine totals.
- **Import / export** — CSV import of books (`title,author[,isbn][,copies]`, duplicate
  ISBNs skipped with line-numbered errors); CSV export of all loans or just overdue ones.
- **Stats dashboard** — totals, outstanding fines, holds waiting, most-borrowed books
  and most active members.

## Data

Everything lives in `library.json` (or the `--db` path). Saves are atomic (write to a
temp file, then rename), so a crash never leaves a half-written database.
