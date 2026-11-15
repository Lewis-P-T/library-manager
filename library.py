"""Library Manager — books, members and loans in a JSON file (stdlib only).

Run:        python library.py [--db library.json]
Self-check: python library.py --selftest
"""
import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta

DEFAULT_DB = "library.json"
LOAN_DAYS = 14
LOAN_LIMIT = 3
FINE_PER_DAY = 0.25
FINE_CAP = 10.00
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")  # ponytail: shape check only, not RFC 5322


def empty_data():
    return {"books": {}, "members": {}, "loans": [], "next_id": {"book": 1, "member": 1}}


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
        self.save()
        return loan

    def return_book(self, bid, mid, today=None):
        bid, mid = bid.strip().upper(), mid.strip().upper()
        loans = self.active_loans(member=mid, book=bid)
        if not loans:
            raise ValueError("no active loan for that book and member")
        loans[0]["returned"] = (today or date.today()).isoformat()
        self.save()
        return loans[0]

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
               "q": "Quit"}
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
                print(f" {bid}  {b['title']} - {b['author']}  ({lib.available(bid)}/{b['copies']} in)")
        elif choice in ("3", "5", "6", "7"):
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
                else:
                    lib.return_book(ask("Book ID: "), ask("Member ID: "))
                    print("Returned.")
            except ValueError as e:
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
