# Library Manager — Build Progress

Python CLI (standard library only). Books, members and loans stored in a JSON file.

- [x] 1. Skeleton: JSON store (load/save, atomic write), Library class, add/list books, menu loop, `--selftest`
- [x] 2. Members: add/list/edit members, unique IDs, validation of input
- [x] 3. Loans: check out / return, copies-available tracking, per-member loan limit, due dates (14 days)
- [x] 4. Search: case-insensitive title/author/ISBN search with ranked fuzzy matching (difflib)
- [x] 5. Overdue reports: overdue list sorted by days late, fines calculation, per-member history
- [ ] 6. Reservations: FIFO hold queue per book (collections.deque), auto-assign on return
- [ ] 7. Import/export: CSV import of books, CSV export of loans/overdue report
- [ ] 8. Polish: stats dashboard (most borrowed, active members), tidy menus, README, full self-check coverage
