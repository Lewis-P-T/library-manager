"""Library Manager — books, members and loans in a JSON file (stdlib only).

Run:        python library.py [--db library.json]
Self-check: python library.py --selftest
"""
import argparse
import json
import os
import sys
import tempfile

DEFAULT_DB = "library.json"


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


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return "q"


def menu(lib):
    actions = {"1": "Add book", "2": "List books", "q": "Quit"}
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
                print(f" {bid}  {b['title']} - {b['author']}  (x{b['copies']})")
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
