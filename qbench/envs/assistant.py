"""A personal-assistant workspace: calendar, contacts, mailbox, notes, to-dos.

State is seeded from the case (env.state) and mutated only through tools, so the
env_state scorer can compare the final state with the goal state, the tau-bench idea.
"""

import copy
import re

from ..util import fold
from . import Env

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


def _dt(value, field):
    if not isinstance(value, str) or not DT_RE.match(value):
        raise ValueError(f"{field} must be 'YYYY-MM-DDTHH:MM' (got {value!r})")
    return value


def _str(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


S = {"type": "string"}


class AssistantEnv(Env):
    type = "assistant"
    TOOLSETS = {
        "calendar": ["list_events", "create_event", "update_event", "delete_event"],
        "contacts": ["search_contacts"],
        "mail": ["list_emails", "read_email", "send_email"],
        "notes": ["list_notes", "create_note"],
        "todos": ["list_todos", "add_todo", "complete_todo", "update_todo"],
        "freebusy": ["get_free_busy"],
        "rooms": ["list_rooms", "book_room"],
        "expenses": ["list_expenses", "submit_expense"],
        "travel": ["search_trains", "book_train", "search_hotels", "book_hotel"],
    }

    def __init__(self, spec, lang):
        super().__init__(spec, lang)
        seed = spec.get("state") or {}
        self.st = {
            "calendar": copy.deepcopy(seed.get("calendar", [])),
            "contacts": copy.deepcopy(seed.get("contacts", [])),
            "inbox": copy.deepcopy(seed.get("inbox", [])),
            "sent": [],
            "notes": copy.deepcopy(seed.get("notes", [])),
            "todos": copy.deepcopy(seed.get("todos", [])),
            "others": copy.deepcopy(seed.get("others", {})),        # email -> [busy {start, end}]
            "rooms": copy.deepcopy(seed.get("rooms", [])),          # {id, name, capacity, equipment, bookings}
            "expenses": copy.deepcopy(seed.get("expenses", [])),
            "trains": copy.deepcopy(seed.get("trains", [])),        # {id, from, to, date, departs, arrives, price, seats}
            "hotels": copy.deepcopy(seed.get("hotels", [])),        # {id, city, name, price_per_night, refundable, distance_km, rooms_left}
            "bookings": [],
        }
        self.enabled = spec.get("toolsets") or list(self.TOOLSETS)
        self._counter = 0

    def _id(self, prefix):
        self._counter += 1
        return f"{prefix}{100 + self._counter}"

    def tools(self):
        schemas = {
            "list_events": _fn("list_events", "List calendar events that overlap a date range (inclusive dates).",
                               {"date_from": {**S, "description": "YYYY-MM-DD"}, "date_to": {**S, "description": "YYYY-MM-DD"}},
                               ["date_from", "date_to"]),
            "create_event": _fn("create_event", "Create a calendar event.",
                                {"title": S, "start": {**S, "description": "YYYY-MM-DDTHH:MM"},
                                 "end": {**S, "description": "YYYY-MM-DDTHH:MM"},
                                 "attendees": {"type": "array", "items": S, "description": "email addresses"},
                                 "location": S}, ["title", "start", "end"]),
            "update_event": _fn("update_event", "Change fields of an existing event. Only the given fields change.",
                                {"event_id": S, "title": S, "start": {**S, "description": "YYYY-MM-DDTHH:MM"},
                                 "end": {**S, "description": "YYYY-MM-DDTHH:MM"},
                                 "attendees": {"type": "array", "items": S}, "location": S}, ["event_id"]),
            "delete_event": _fn("delete_event", "Delete a calendar event.", {"event_id": S}, ["event_id"]),
            "search_contacts": _fn("search_contacts", "Find contacts by name, company or email fragment.",
                                   {"query": S}, ["query"]),
            "list_emails": _fn("list_emails", "List emails in the inbox, newest first, optionally filtered by a text query.",
                               {"query": S}, []),
            "read_email": _fn("read_email", "Read one email by id.", {"email_id": S}, ["email_id"]),
            "send_email": _fn("send_email", "Send an email.",
                              {"to": {"type": "array", "items": S, "description": "recipient email addresses"},
                               "subject": S, "body": S}, ["to", "subject", "body"]),
            "list_notes": _fn("list_notes", "List notes.", {}, []),
            "create_note": _fn("create_note", "Create a note.", {"title": S, "body": S}, ["title", "body"]),
            "list_todos": _fn("list_todos", "List to-do items.", {}, []),
            "add_todo": _fn("add_todo", "Add a to-do item.", {"text": S, "due": {**S, "description": "YYYY-MM-DD, optional"}}, ["text"]),
            "complete_todo": _fn("complete_todo", "Mark a to-do item as done.", {"todo_id": S}, ["todo_id"]),
            "update_todo": _fn("update_todo", "Change the text and/or due date of a to-do item.",
                               {"todo_id": S, "text": S, "due": {**S, "description": "YYYY-MM-DD"}}, ["todo_id"]),
            "get_free_busy": _fn("get_free_busy", "Busy intervals of a colleague's calendar between two dates (inclusive). "
                                 "Free time is everything else.",
                                 {"email": S, "date_from": {**S, "description": "YYYY-MM-DD"},
                                  "date_to": {**S, "description": "YYYY-MM-DD"}}, ["email", "date_from", "date_to"]),
            "list_rooms": _fn("list_rooms", "Meeting rooms that are free for the whole interval, with capacity and equipment.",
                              {"start": {**S, "description": "YYYY-MM-DDTHH:MM"}, "end": {**S, "description": "YYYY-MM-DDTHH:MM"},
                               "min_capacity": {"type": "integer"}}, ["start", "end"]),
            "book_room": _fn("book_room", "Book a meeting room for an interval.",
                             {"room_id": S, "start": {**S, "description": "YYYY-MM-DDTHH:MM"},
                              "end": {**S, "description": "YYYY-MM-DDTHH:MM"}, "title": S}, ["room_id", "start", "end", "title"]),
            "list_expenses": _fn("list_expenses", "Expense claims already submitted.", {}, []),
            "submit_expense": _fn("submit_expense", "Submit one expense claim to accounting.",
                                  {"employee": {**S, "description": "employee email"},
                                   "invoice_no": S, "amount_pln": {"type": "number", "description": "gross amount in PLN"},
                                   "category": {**S, "enum": ["travel", "meals", "hotel", "software", "equipment", "other"]},
                                   "status": {**S, "enum": ["submitted", "needs_approval"]}},
                                  ["employee", "invoice_no", "amount_pln", "category", "status"]),
            "search_trains": _fn("search_trains", "Train connections on a date.",
                                 {"from": S, "to": S, "date": {**S, "description": "YYYY-MM-DD"}}, ["from", "to", "date"]),
            "book_train": _fn("book_train", "Book a seat on a connection returned by search_trains.", {"train_id": S}, ["train_id"]),
            "search_hotels": _fn("search_hotels", "Hotels with free rooms in a city for a stay.",
                                 {"city": S, "check_in": {**S, "description": "YYYY-MM-DD"},
                                  "check_out": {**S, "description": "YYYY-MM-DD"}}, ["city", "check_in", "check_out"]),
            "book_hotel": _fn("book_hotel", "Book a hotel returned by search_hotels.",
                              {"hotel_id": S, "check_in": {**S, "description": "YYYY-MM-DD"},
                               "check_out": {**S, "description": "YYYY-MM-DD"}}, ["hotel_id", "check_in", "check_out"]),
        }
        names = [n for ts in self.enabled for n in self.TOOLSETS[ts]]
        return [schemas[n] for n in names]

    def handlers(self):
        allowed = {n for ts in self.enabled for n in self.TOOLSETS[ts]}
        return {n: getattr(self, n) for n in allowed}

    # calendar
    def _event(self, event_id):
        for e in self.st["calendar"]:
            if e["id"] == event_id:
                return e
        raise KeyError(f"no event with id {event_id!r}")

    def list_events(self, date_from, date_to):
        lo, hi = str(date_from)[:10], str(date_to)[:10]
        return {"events": [e for e in self.st["calendar"] if e["start"][:10] <= hi and e["end"][:10] >= lo]}

    def create_event(self, title, start, end, attendees=None, location=None):
        ev = {"id": self._id("ev"), "title": _str(title, "title"), "start": _dt(start, "start"),
              "end": _dt(end, "end"), "attendees": list(attendees or []), "location": location or ""}
        if ev["end"] <= ev["start"]:
            raise ValueError("end must be after start")
        self.st["calendar"].append(ev)
        return {"created": ev}

    def update_event(self, event_id, title=None, start=None, end=None, attendees=None, location=None):
        ev = self._event(event_id)
        new = dict(ev)
        if title is not None:
            new["title"] = _str(title, "title")
        if start is not None:
            new["start"] = _dt(start, "start")
        if end is not None:
            new["end"] = _dt(end, "end")
        if attendees is not None:
            new["attendees"] = list(attendees)
        if location is not None:
            new["location"] = location
        if new["end"] <= new["start"]:
            raise ValueError("end must be after start")
        ev.update(new)
        return {"updated": ev}

    def delete_event(self, event_id):
        ev = self._event(event_id)
        self.st["calendar"].remove(ev)
        return {"deleted": event_id}

    # contacts
    def search_contacts(self, query):
        """Word search like a real address book: a contact matches if ANY query word starts a word of
        its name, email or company ('Tomek IT' finds both IT people; 'Kasia' alone finds no Katarzyna)."""
        words = [w for w in re.findall(r"\w+", fold(query)) if len(w) > 1]
        scored = []
        for c in self.st["contacts"]:
            hay = re.findall(r"\w+", fold(" ".join(str(c.get(k, "")) for k in ("name", "email", "company"))))
            hits = sum(1 for w in words if any(h.startswith(w) for h in hay))
            if hits:
                scored.append((-hits, c["id"], c))
        scored.sort(key=lambda t: (t[0], t[1]))
        return {"contacts": [c for _, _, c in scored]}

    # mail
    def list_emails(self, query=None):
        mails = sorted(self.st["inbox"], key=lambda m: m["date"], reverse=True)
        if query:
            q = fold(query)
            mails = [m for m in mails if q in fold(m["subject"] + " " + m["from"] + " " + m["body"])]
        return {"emails": [{k: m[k] for k in ("id", "from", "subject", "date")} for m in mails]}

    def read_email(self, email_id):
        for m in self.st["inbox"]:
            if m["id"] == email_id:
                return m
        raise KeyError(f"no email with id {email_id!r}")

    def send_email(self, to, subject, body):
        if isinstance(to, str):
            to = [to]
        if not to or not all(isinstance(a, str) and "@" in a for a in to):
            raise ValueError("to must be a list of email addresses")
        msg = {"id": self._id("sent"), "to": to, "subject": _str(subject, "subject"), "body": _str(body, "body")}
        self.st["sent"].append(msg)
        return {"sent": msg["id"]}

    # notes and to-dos
    def list_notes(self):
        return {"notes": self.st["notes"]}

    def create_note(self, title, body):
        note = {"id": self._id("n"), "title": _str(title, "title"), "body": _str(body, "body")}
        self.st["notes"].append(note)
        return {"created": note}

    def list_todos(self):
        return {"todos": self.st["todos"]}

    def add_todo(self, text, due=None):
        if due is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(due)):
            raise ValueError("due must be YYYY-MM-DD")
        todo = {"id": self._id("t"), "text": _str(text, "text"), "due": due, "done": False}
        self.st["todos"].append(todo)
        return {"created": todo}

    def complete_todo(self, todo_id):
        for t in self.st["todos"]:
            if t["id"] == todo_id:
                t["done"] = True
                return {"completed": todo_id}
        raise KeyError(f"no to-do with id {todo_id!r}")

    def update_todo(self, todo_id, text=None, due=None):
        for t in self.st["todos"]:
            if t["id"] == todo_id:
                if due is not None:
                    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(due)):
                        raise ValueError("due must be YYYY-MM-DD")
                    t["due"] = due
                if text is not None:
                    t["text"] = _str(text, "text")
                return {"updated": t}
        raise KeyError(f"no to-do with id {todo_id!r}")

    # colleagues' calendars (read-only)
    def get_free_busy(self, email, date_from, date_to):
        busy = self.st["others"].get(str(email).strip().lower())
        if busy is None:
            raise KeyError(f"no calendar access for {email!r}")
        lo, hi = str(date_from)[:10], str(date_to)[:10]
        return {"email": email, "busy": [b for b in busy if b["start"][:10] <= hi and b["end"][:10] >= lo]}

    # rooms
    def _room(self, room_id):
        for r in self.st["rooms"]:
            if r["id"] == room_id:
                return r
        raise KeyError(f"no room with id {room_id!r}")

    def list_rooms(self, start, end, min_capacity=0):
        _dt(start, "start"), _dt(end, "end")
        free = []
        for r in self.st["rooms"]:
            if r["capacity"] < int(min_capacity or 0):
                continue
            if all(not (b["start"] < end and b["end"] > start) for b in r.get("bookings", [])):
                free.append({k: r[k] for k in ("id", "name", "capacity", "equipment")})
        return {"rooms": free}

    def book_room(self, room_id, start, end, title):
        r = self._room(room_id)
        _dt(start, "start"), _dt(end, "end"), _str(title, "title")
        if end <= start:
            raise ValueError("end must be after start")
        if any(b["start"] < end and b["end"] > start for b in r.get("bookings", [])):
            raise ValueError(f"room {room_id} is already booked in that interval")
        r.setdefault("bookings", []).append({"start": start, "end": end, "title": title, "by": "me"})
        self.st["bookings"].append({"type": "room", "id": room_id, "start": start, "end": end, "title": title})
        return {"booked": room_id, "start": start, "end": end}

    # expenses
    def list_expenses(self):
        return {"expenses": self.st["expenses"]}

    def submit_expense(self, employee, invoice_no, amount_pln, category, status):
        if category not in ("travel", "meals", "hotel", "software", "equipment", "other"):
            raise ValueError("unknown category")
        if status not in ("submitted", "needs_approval"):
            raise ValueError("status must be submitted or needs_approval")
        if not isinstance(amount_pln, (int, float)) or isinstance(amount_pln, bool) or amount_pln <= 0:
            raise ValueError("amount_pln must be a positive number")
        e = {"id": self._id("x"), "employee": _str(employee, "employee").lower(), "invoice_no": _str(invoice_no, "invoice_no"),
             "amount_pln": round(float(amount_pln), 2), "category": category, "status": status}
        self.st["expenses"].append(e)
        return {"submitted": e["id"]}

    # travel
    def search_trains(self, date, **kw):
        frm, to = fold(kw.get("from", "")), fold(kw.get("to", ""))
        hits = [t for t in self.st["trains"] if t["date"] == str(date)[:10]
                and fold(t["from"]).startswith(frm[:5]) and fold(t["to"]).startswith(to[:5]) and t.get("seats", 1) > 0]
        return {"connections": [{k: t[k] for k in ("id", "from", "to", "date", "departs", "arrives", "price", "class")
                                 if k in t} for t in sorted(hits, key=lambda t: t["departs"])]}

    def book_train(self, train_id):
        for t in self.st["trains"]:
            if t["id"] == train_id:
                if t.get("seats", 1) <= 0:
                    raise ValueError("sold out")
                t["seats"] = t.get("seats", 1) - 1
                self.st["bookings"].append({"type": "train", "id": train_id})
                return {"booked": train_id, "departs": t["departs"], "arrives": t["arrives"], "price": t["price"]}
        raise KeyError(f"no train with id {train_id!r}")

    def search_hotels(self, city, check_in, check_out):
        c = fold(city)[:5]
        hits = [h for h in self.st["hotels"] if fold(h["city"]).startswith(c) and h.get("rooms_left", 1) > 0]
        nights = (int(str(check_out)[8:10]) - int(str(check_in)[8:10])) if str(check_in)[:7] == str(check_out)[:7] else None
        return {"hotels": [{**{k: h[k] for k in h if k not in ("rooms_left",)}, "nights": nights} for h in hits]}

    def book_hotel(self, hotel_id, check_in, check_out):
        for h in self.st["hotels"]:
            if h["id"] == hotel_id:
                if h.get("rooms_left", 1) <= 0:
                    raise ValueError("no rooms left")
                h["rooms_left"] = h.get("rooms_left", 1) - 1
                self.st["bookings"].append({"type": "hotel", "id": hotel_id, "check_in": check_in, "check_out": check_out})
                return {"booked": hotel_id, "check_in": check_in, "check_out": check_out}
        raise KeyError(f"no hotel with id {hotel_id!r}")

    def state(self):
        return copy.deepcopy(self.st)
