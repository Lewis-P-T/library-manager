"""Library Manager — books, members and loans in a JSON file (stdlib only).

Run:        python library.py [--db library.json]
Self-check: python library.py --selftest
"""
import argparse
import csv
import difflib
import json
import os
import re
import sys
import tempfile
from collections import deque
from datetime import date, timedelta

DEFAULT_DB = "library.json"
LOAN_DAYS = 14
LOAN_LIMIT = 3
FINE_PER_DAY = 0.25
FINE_CAP = 10.00
LOAN_FIELDS = ["book", "title", "member", "name", "out", "due", "returned", "days_late", "fine"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")  # ponytail: shape check only, not RFC 5322


def empty_data():
    return {"books": {}, "members": {}, "loans": [], "holds": {},
            "next_id": {"book": 1, "member": 1}}


class Library:
    def __init__(self, path):
        self.path = path
        self.data = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return empty_data()
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        base = empty_data()
        base.update(data)  # tolerate files saved by older versions
        return base

    def save(self):
        # write to temp file then rename, so a crash never leaves a half-written DB
        folder = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def _new_id(self, kind, prefix):
        n = self.data["next_id"][kind]
        self.data["next_id"][kind] = n + 1
        return f"{prefix}{n:04d}"

    def add_book(self, title, author, isbn="", copies=1):
        title, author = title.strip(), author.strip()
        if not title or not author:
            raise ValueError("title and author are required")
        if copies < 1:
            raise ValueError("copies must be at least 1")
        bid = self._new_id("book", "B")
        self.data["books"][bid] = {"title": title, "author": author,
                                   "isbn": isbn.strip(), "copies": copies}
        self.save()
        return bid

    def list_books(self):
        return sorted(self.data["books"].items(), key=lambda kv: kv[1]["title"].lower())

    # ---- members ----
    @staticmethod
    def _clean_member(name, email):
        name, email = name.strip(), email.strip().lower()
        if not name:
            raise ValueError("name is required")
        if email and not EMAIL_RE.match(email):
            raise ValueError(f"invalid email: {email}")
        return name, email

    def _email_taken(self, email, except_id=None):
        return bool(email) and any(m["email"] == email for mid, m in self.data["members"].items()
                                   if mid != except_id)

    def add_member(self, name, email=""):
        name, email = self._clean_member(name, email)
        if self._email_taken(email):
            raise ValueError(f"email already registered: {email}")
        mid = self._new_id("member", "M")
        self.data["members"][mid] = {"name": name, "email": email}
        self.save()
        return mid

    def edit_member(self, mid, name=None, email=None):
        mid = mid.strip().upper()
        m = self._member(mid)
        name, email = self._clean_member(m["name"] if name is None else name,
                                         m["email"] if email is None else email)
        if self._email_taken(email, except_id=mid):
            raise ValueError(f"email already registered: {email}")
        m.update(name=name, email=email)
        self.save()

    def list_members(self):
        return sorted(self.data["members"].items(), key=lambda kv: kv[1]["name"].lower())

    def _member(self, mid):
        if mid not in self.data["members"]:
            raise ValueError(f"no such member: {mid}")
        return self.data["members"][mid]

    def _book(self, bid):
        if bid not in self.data["books"]:
            raise ValueError(f"no such book: {bid}")
        return self.data["books"][bid]

    # ---- loans ----
    def active_loans(self, member=None, book=None):
        return [l for l in self.data["loans"] if l["returned"] is None
                and (member is None or l["member"] == member)
                and (book is None or l["book"] == book)]

    def available(self, bid):
        return self._book(bid)["copies"] - len(self.active_loans(book=bid))

    def checkout(self, bid, mid, today=None):
        bid, mid = bid.strip().upper(), mid.strip().upper()
        self._member(mid)
        today = today or date.today()
        if self.available(bid) < 1:
            raise ValueError("no copies available")
        mine = self.active_loans(member=mid)
        if any(l["book"] == bid for l in mine):
            raise ValueError("member already has this book")
        if len(mine) >= LOAN_LIMIT:
            raise ValueError(f"loan limit reached ({LOAN_LIMIT})")
        loan = {"book": bid, "member": mid, "out": today.isoformat(),
                "due": (today + timedelta(days=LOAN_DAYS)).isoformat(), "returned": None}
        self.data["loans"].append(loan)
        queue = self.data["holds"].get(bid, [])
        if mid in queue:  # their hold is fulfilled
            queue.remove(mid)
            if not queue:
                del self.data["holds"][bid]
        self.save()
        return loan

    def return_book(self, bid, mid, today=None):
        bid, mid = bid.strip().upper(), mid.strip().upper()
        loans = self.active_loans(member=mid, book=bid)
        if not loans:
            raise ValueError("no active loan for that book and member")
        loans[0]["returned"] = (today or date.today()).isoformat()
        self.save()
        return loans[0], self._assign_hold(bid, today)

    # ---- reservations ----
    def reserve(self, bid, mid):
        """Join the FIFO hold queue for a book with no copies on the shelf."""
        bid, mid = bid.strip().upper(), mid.strip().upper()
        self._member(mid)
        if self.available(bid) > 0:
            raise ValueError("a copy is available - check it out instead")
        if mid in self.data["holds"].get(bid, []):
            raise ValueError("member already has a hold on this book")
        if self.active_loans(member=mid, book=bid):
            raise ValueError("member already has this book")
        queue = self.data["holds"].setdefault(bid, [])
        queue.append(mid)
        self.save()
        return len(queue)

    def cancel_hold(self, bid, mid):
        bid, mid = bid.strip().upper(), mid.strip().upper()
        queue = self.data["holds"].get(bid, [])
        if mid not in queue:
            raise ValueError("no such hold")
        queue.remove(mid)
        if not queue:
            del self.data["holds"][bid]
        self.save()

    def holds(self, bid):
        return list(self.data["holds"].get(bid.strip().upper(), []))

    def _assign_hold(self, bid, today):
        """Give a freed copy to the first eligible member in the queue.

        Members who can't take it right now (e.g. at loan limit) keep their place.
        Returns the member ID that got the book, or None.
        """
        queue = deque(self.data["holds"].get(bid, []))
        skipped = []
        winner = None
        while queue and winner is None:
            mid = queue.popleft()
            try:
                self.checkout(bid, mid, today=today)
                winner = mid
            except ValueError:
                skipped.append(mid)
        rest = skipped + list(queue)
        if rest:
            self.data["holds"][bid] = rest
        else:
            self.data["holds"].pop(bid, None)
        self.save()
        return winner

    # ---- CSV import / export ----
    def import_books_csv(self, path):
        """Add books from a CSV with header title,author[,isbn][,copies].

        Rows whose ISBN already exists are skipped. Returns (added_ids, errors).
        """
        known = {b["isbn"].replace("-", "") for b in self.data["books"].values() if b["isbn"]}
        added, errors = [], []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            missing = {"title", "author"} - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"CSV missing column(s): {', '.join(sorted(missing))}")
            for n, row in enumerate(reader, start=2):  # line 1 is the header
                try:
                    isbn = (row.get("isbn") or "").strip()
                    if isbn and isbn.replace("-", "") in known:
                        raise ValueError(f"duplicate ISBN {isbn}")
                    copies = int((row.get("copies") or "1").strip())
                    added.append(self.add_book(row["title"] or "", row["author"] or "", isbn, copies))
                    if isbn:
                        known.add(isbn.replace("-", ""))
                except ValueError as e:
                    errors.append(f"line {n}: {e}")
        return added, errors

    def loan_rows(self, today=None):
        """Every loan, flattened with titles/names, for export."""
        today = today or date.today()
        books, members = self.data["books"], self.data["members"]
        return [{"book": l["book"], "title": books.get(l["book"], {}).get("title", "?"),
                 "member": l["member"], "name": members.get(l["member"], {}).get("name", "?"),
                 "out": l["out"], "due": l["due"], "returned": l["returned"] or "",
                 "days_late": self.days_late(l, today), "fine": f"{self.fine(l, today):.2f}"}
                for l in self.data["loans"]]

    @staticmethod
    def export_csv(path, rows, fields):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return len(rows)

    def export_loans_csv(self, path, today=None):
        return self.export_csv(path, self.loan_rows(today), LOAN_FIELDS)

    def export_overdue_csv(self, path, today=None):
        rows = [r for r in self.loan_rows(today) if not r["returned"] and r["days_late"] > 0]
        rows.sort(key=lambda r: (-r["days_late"], r["book"]))
        return self.export_csv(path, rows, LOAN_FIELDS)

    # ---- search ----
    def search(self, query, limit=10, cutoff=0.6):
        """Rank books by how well `query` matches title/author/ISBN.

        Substring hits score highest (earlier = better); otherwise the best
        difflib ratio against the whole field or any of its words is used,
        so typos like "dnue" still find "Dune".
        """
        q = query.strip().lower()
        if not q:
            return []
        q_isbn = q.replace("-", "").replace(" ", "")
        results = []
        for bid, b in self.data["books"].items():
            best = 0.0
            for field, weight in (("title", 1.0), ("author", 0.9)):
                text = b[field].lower()
                pos = text.find(q)
                if pos >= 0:
                    score = 2.0 - pos / (len(text) + 1)
                else:
                    score = max(difflib.SequenceMatcher(None, q, w).ratio()
                                for w in [text] + text.split())
                    if score < cutoff:
                        score = 0.0
                best = max(best, score * weight)
            isbn = b["isbn"].replace("-", "").replace(" ", "").lower()
            if q_isbn and isbn and q_isbn in isbn:
                best = max(best, 3.0 if q_isbn == isbn else 1.5)
            if best > 0:
                results.append((best, bid))
        results.sort(key=lambda r: (-r[0], self.data["books"][r[1]]["title"].lower()))
        return [(bid, self.data["books"][bid], round(score, 3)) for score, bid in results[:limit]]

    # ---- overdue & fines ----
    @staticmethod
    def days_late(loan, today):
        end = date.fromisoformat(loan["returned"]) if loan["returned"] else today
        return max(0, (end - date.fromisoformat(loan["due"])).days)

    @classmethod
    def fine(cls, loan, today):
        return min(cls.days_late(loan, today) * FINE_PER_DAY, FINE_CAP)

    def overdue(self, today=None):
        """Active loans past due, most days late first."""
        today = today or date.today()
        rows = [(l, self.days_late(l, today)) for l in self.active_loans()]
        rows = [(l, d) for l, d in rows if d > 0]
        rows.sort(key=lambda r: (-r[1], r[0]["book"]))
        return [{**l, "days_late": d, "fine": self.fine(l, today)} for l, d in rows]

    def member_history(self, mid, today=None):
        """All loans (past and present) for a member, newest first, plus total fines."""
        mid = mid.strip().upper()
        self._member(mid)
        today = today or date.today()
        loans = [l for l in self.data["loans"] if l["member"] == mid]
        loans.sort(key=lambda l: l["out"], reverse=True)
        rows = [{**l, "title": self.data["books"].get(l["book"], {}).get("title", "?"),
                 "days_late": self.days_late(l, today), "fine": self.fine(l, today)} for l in loans]
        return rows, round(sum(r["fine"] for r in rows), 2)


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return "q"


def menu(lib):
    actions = {"1": "Add book", "2": "List books", "3": "Add member", "4": "List members",
               "5": "Edit member", "6": "Check out", "7": "Return", "8": "Active loans",
               "9": "Search books", "10": "Overdue report", "11": "Member history",
               "12": "Reserve book", "13": "Cancel reservation", "14": "Import books (CSV)",
               "15": "Export loans (CSV)", "16": "Export overdue (CSV)", "q": "Quit"}
    while True:
        print("\n== Library Manager ==")
        for k, v in actions.items():
            print(f" {k}) {v}")
        choice = ask("> ").strip().lower()
        if choice == "1":
            try:
                copies = int(ask("Copies [1]: ") or 1)
                bid = lib.add_book(ask("Title: "), ask("Author: "), ask("ISBN: "), copies)
                print(f"Added {bid}.")
            except ValueError as e:
                print(f"Error: {e}")
        elif choice == "2":
            books = lib.list_books()
            if not books:
                print("No books yet.")
            for bid, b in books:
                waiting = len(lib.holds(bid))
                hold = f"  [{waiting} waiting]" if waiting else ""
                print(f" {bid}  {b['title']} - {b['author']}  ({lib.available(bid)}/{b['copies']} in){hold}")
        elif choice in ("3", "5", "6", "7", "12", "13", "14", "15", "16"):
            try:
                if choice == "3":
                    print(f"Added {lib.add_member(ask('Name: '), ask('Email (optional): '))}.")
                elif choice == "5":
                    mid = ask("Member ID: ")
                    lib.edit_member(mid, ask("New name (blank = keep): ") or None,
                                    ask("New email (blank = keep): ") or None)
                    print("Updated.")
                elif choice == "6":
                    loan = lib.checkout(ask("Book ID: "), ask("Member ID: "))
                    print(f"Checked out, due {loan['due']}.")
                elif choice == "7":
                    _, nxt = lib.return_book(ask("Book ID: "), ask("Member ID: "))
                    print("Returned." + (f" Reserved copy checked out to {nxt}." if nxt else ""))
                elif choice == "12":
                    print(f"Reserved - position {lib.reserve(ask('Book ID: '), ask('Member ID: '))} in queue.")
                elif choice == "13":
                    lib.cancel_hold(ask("Book ID: "), ask("Member ID: "))
                    print("Reservation cancelled.")
                elif choice == "14":
                    added, errors = lib.import_books_csv(ask("CSV path (title,author,isbn,copies): ").strip())
                    print(f"Imported {len(added)} book(s).")
                    for err in errors:
                        print(f"  skipped {err}")
                elif choice == "15":
                    path = ask("Save to [loans.csv]: ").strip() or "loans.csv"
                    print(f"Wrote {lib.export_loans_csv(path)} loan(s) to {path}.")
                else:
                    path = ask("Save to [overdue.csv]: ").strip() or "overdue.csv"
                    print(f"Wrote {lib.export_overdue_csv(path)} overdue loan(s) to {path}.")
            except (ValueError, OSError) as e:
                print(f"Error: {e}")
        elif choice == "4":
            members = lib.list_members()
            if not members:
                print("No members yet.")
            for mid, m in members:
                print(f" {mid}  {m['name']}  {m['email']}  ({len(lib.active_loans(member=mid))} on loan)")
        elif choice == "8":
            loans = lib.active_loans()
            if not loans:
                print("No active loans.")
            for l in sorted(loans, key=lambda l: l["due"]):
                print(f" {l['book']} -> {l['member']}  due {l['due']}")
        elif choice == "9":
            hits = lib.search(ask("Search title/author/ISBN: "))
            if not hits:
                print("No matches.")
            for bid, b, _ in hits:
                print(f" {bid}  {b['title']} - {b['author']}  {b['isbn']}  ({lib.available(bid)}/{b['copies']} in)")
        elif choice == "10":
            rows = lib.overdue()
            if not rows:
                print("Nothing overdue.")
            for r in rows:
                print(f" {r['book']} -> {r['member']}  due {r['due']}  {r['days_late']}d late  fine {r['fine']:.2f}")
            if rows:
                print(f" Total outstanding: {sum(r['fine'] for r in rows):.2f}")
        elif choice == "11":
            try:
                rows, total = lib.member_history(ask("Member ID: "))
            except ValueError as e:
                print(f"Error: {e}")
                continue
            if not rows:
                print("No loans yet.")
            for r in rows:
                status = f"returned {r['returned']}" if r["returned"] else "on loan"
                late = f"  {r['days_late']}d late, fine {r['fine']:.2f}" if r["days_late"] else ""
                print(f" {r['out']}  {r['book']} {r['title']}  due {r['due']}  {status}{late}")
            print(f" Total fines: {total:.2f}")
        elif choice == "q":
            return
        else:
            print("Unknown option.")


def selftest():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.json")
        lib = Library(path)
        b1 = lib.add_book("Zen", "Pirsig", copies=2)
        b2 = lib.add_book("  dune ", "Herbert")
        assert (b1, b2) == ("B0001", "B0002")
        assert [b["title"] for _, b in lib.list_books()] == ["dune", "Zen"]
        for bad in (("", "x"), ("x", " ")):
            try:
                lib.add_book(*bad)
                raise AssertionError("expected ValueError")
            except ValueError:
                pass
        reloaded = Library(path)  # persistence round-trip
        assert reloaded.data["books"][b1]["copies"] == 2
        assert reloaded._new_id("book", "B") == "B0003"

        def fails(fn, *a, **kw):
            try:
                fn(*a, **kw)
            except ValueError:
                return
            raise AssertionError(f"expected ValueError: {fn.__name__}{a}")

        # members
        m1 = lib.add_member("Ann", "Ann@Example.com")
        m2 = lib.add_member("Bob")
        assert (m1, m2) == ("M0001", "M0002")
        fails(lib.add_member, "", "")
        fails(lib.add_member, "X", "not-an-email")
        fails(lib.add_member, "Dup", "ann@example.com")
        lib.edit_member("m0002", email="bob@example.com")
        assert lib.data["members"][m2] == {"name": "Bob", "email": "bob@example.com"}
        fails(lib.edit_member, m2, email="ann@example.com")
        fails(lib.edit_member, "M9999", name="Nobody")
        assert [m["name"] for _, m in lib.list_members()] == ["Ann", "Bob"]

        # loans
        day = date(2026, 1, 1)
        assert lib.checkout(b1, m1, today=day)["due"] == "2026-01-15"
        assert lib.available(b1) == 1
        lib.checkout(b1, m2, today=day)
        assert lib.available(b1) == 0
        b3 = lib.add_book("Emma", "Austen")
        b4 = lib.add_book("Ulysses", "Joyce")
        fails(lib.checkout, b1, m1, today=day)        # none available
        fails(lib.checkout, "B9999", m1, today=day)   # unknown book
        fails(lib.checkout, b2, "M9999", today=day)   # unknown member
        fails(lib.return_book, b2, m1, today=day)     # not on loan
        lib.checkout(b2, m1, today=day)
        fails(lib.checkout, b2, m1, today=day)        # already has it
        lib.checkout(b3, m1, today=day)
        fails(lib.checkout, b4, m1, today=day)        # loan limit
        lib.return_book("b0001", "m0001", today=day)
        assert lib.available(b1) == 1 and len(lib.active_loans(member=m1)) == 2
        lib.checkout(b4, m1, today=day)               # slot freed
        assert len(Library(path).active_loans()) == 4  # persisted

        # search
        lib.data["books"][b2]["isbn"] = "978-0-441-17271-9"
        assert lib.search("dune")[0][0] == b2
        assert lib.search("DNUE")[0][0] == b2                 # typo -> fuzzy
        assert lib.search("austen")[0][0] == b3               # author
        assert lib.search("9780441172719")[0][0] == b2        # ISBN, dashes ignored
        assert lib.search("zzzzqq") == [] and lib.search("  ") == []
        assert len(lib.search("e", limit=2)) == 2

        # overdue & fines (b1 returned on day 0; m1 holds b2,b3,b4; m2 holds b1)
        later = day + timedelta(days=LOAN_DAYS + 4)   # 4 days past due
        rows = lib.overdue(today=later)
        assert len(rows) == 4 and all(r["days_late"] == 4 for r in rows)
        assert rows[0]["fine"] == 4 * FINE_PER_DAY
        assert lib.overdue(today=day) == []
        lib.return_book(b2, m1, today=later - timedelta(days=2))  # returned 2 days late
        rows = lib.overdue(today=later)
        assert len(rows) == 3 and b2 not in [r["book"] for r in rows]
        assert lib.fine({"due": "2026-01-01", "returned": None}, date(2027, 1, 1)) == FINE_CAP
        hist, total = lib.member_history("m0001", today=later)
        assert len(hist) == 4                                 # b1, b2, b3, b4
        assert total == 2 * FINE_PER_DAY + 2 * 4 * FINE_PER_DAY  # b2 late 2d; b3,b4 4d; b1 on time
        fails(lib.member_history, "M9999")

        # reservations: b3 (1 copy) is on loan to m1
        m3 = lib.add_member("Cy")
        fails(lib.reserve, b2, m2)                    # b2 is on the shelf
        fails(lib.reserve, b3, m1)                    # m1 already has it
        assert lib.reserve(b3, m2) == 1 and lib.reserve(b3, m3) == 2
        fails(lib.reserve, b3, m2)                    # duplicate hold
        assert lib.holds(b3) == [m2, m3]
        _, nxt = lib.return_book(b3, m1, today=later)
        assert nxt == m2 and lib.holds(b3) == [m3]    # FIFO auto-assign
        assert lib.active_loans(member=m2, book=b3)
        lib.cancel_hold(b3, m3)
        assert lib.holds(b3) == [] and b3 not in lib.data["holds"]
        fails(lib.cancel_hold, b3, m3)
        # ineligible member at front keeps their place; next in line gets the copy
        lib.reserve(b4, m2)                           # m2 holds b1,b3 -> will be at limit
        lib.reserve(b4, m3)
        lib.checkout(b2, m2, today=later)             # m2 now at LOAN_LIMIT
        _, nxt = lib.return_book(b4, m1, today=later)
        assert nxt == m3 and lib.holds(b4) == [m2]
        assert Library(path).holds(b4) == [m2]        # persisted

        # CSV import / export
        src = os.path.join(d, "in.csv")
        with open(src, "w", newline="", encoding="utf-8") as f:
            f.write("title,author,isbn,copies\n"
                    "Beloved,Morrison,111,2\n"
                    "Dune again,Herbert,978-0441172719,1\n"   # dup ISBN of b2
                    ",NoTitle,,1\n"
                    "Bad copies,X,,zero\n"
                    "Walden,Thoreau,,\n")
        added, errors = lib.import_books_csv(src)
        assert [lib.data["books"][b]["title"] for b in added] == ["Beloved", "Walden"]
        assert len(errors) == 3 and errors[0].startswith("line 3")
        assert lib.data["books"][added[0]]["copies"] == 2
        bad = os.path.join(d, "bad.csv")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("name,writer\nA,B\n")
        fails(lib.import_books_csv, bad)
        out = os.path.join(d, "loans.csv")
        assert lib.export_loans_csv(out, today=later) == len(lib.data["loans"])
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["title"] == "Zen" and rows[0]["name"] == "Ann"
        od = os.path.join(d, "overdue.csv")
        n = lib.export_overdue_csv(od, today=later)
        assert n == len(lib.overdue(today=later)) > 0
    print("selftest OK")


def main():
    p = argparse.ArgumentParser(description="Library Manager")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    if args.selftest:
        selftest()
        return
    menu(Library(args.db))


if __name__ == "__main__":
    sys.exit(main())
