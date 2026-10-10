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
        "todos": ["list_todos", "add_todo", "complete_todo"],
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

    def state(self):
        return copy.deepcopy(self.st)
