"""Library Manager — books, members and loans in a JSON file (stdlib only).

Run:        python library.py [--db library.json]
Self-check: python library.py --selftest
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta

DEFAULT_DB = "library.json"
LOAN_DAYS = 14
LOAN_LIMIT = 3
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


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return "q"


def menu(lib):
    actions = {"1": "Add book", "2": "List books", "3": "Add member", "4": "List members",
               "5": "Edit member", "6": "Check out", "7": "Return", "8": "Active loans",
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
