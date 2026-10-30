"""osgen markdown renderer (plans/osgen/components.md, "Agent plugins" column): a page or a document as the markdown the
skill prints exactly as it is. The OS's "Copy as Markdown" uses the same rules (runtime/js/render.ts).
"""
from __future__ import annotations

import re

import core as C
import dec as D
from template import render as tpl


def cell(s) -> str:
    """A1's cell(): whitespace collapsed, | escaped."""
    return " ".join(p for p in re.split(r"\s+", "" if s is None else str(s)) if p).replace("|", "\\|")


def fmt(value, fmt_: str | None, ftype: str = "text", currency: str | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(fmt(v, fmt_, ftype, currency) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    f = fmt_ or {"money": "money", "hours": "num", "int": "count", "number": "num", "date": "date", "datetime": "date"}.get(ftype)
    if f in ("count", "num") and D.is_dec(str(value)):
        return C.fmt_num(str(value))
    if f in ("pct", "bar") and D.is_dec(str(value)):
        return C.fmt_pct(str(value))
    if f == "hours" and D.is_dec(str(value)):
        return C.fmt_hours(str(value))
    if f == "money" and D.is_dec(str(value)):
        return C.fmt_money(str(value), currency)
    if f == "date":
        return C.fmt_date(str(value))
    return str(value)


class Renderer:
    def __init__(self, spec: dict, views):
        self.spec, self.vs = spec, views

    def _records(self, src: str) -> dict:
        names = {m.group(1).split(".")[0] for m in re.finditer(r"\{[#/]?(?:each |if |plural )?([a-z_][a-z0-9_.]*)", src)}
        return {n: self.vs.record(n) for n in names if n in self.spec["views"] and self.spec["views"][n].get("record")}

    def text(self, src: str | None) -> str:
        return tpl(src, self._records(src)) if src else ""

    def _rows(self, ref: str) -> tuple[list, dict]:
        if ref.startswith("view:"):
            name = ref[5:]
            return self.vs.view(name), self.spec["views"][name].get("out") or {}
        return self.vs.store.sys(ref[4:]), {}

    def _currency(self, field: str) -> str | None:
        for t in self.spec["tables"].values():
            d = t["fields"].get(field)
            if d and d.get("currency"):
                return d["currency"]
        cur = {d.get("currency") for t in self.spec["tables"].values() for d in t["fields"].values() if d.get("currency")}
        return cur.pop() if len(cur) == 1 else None

    def table(self, ref: str, columns: list, limit: int | None = None) -> list[str]:
        rows, out = self._rows(ref)
        if limit:
            rows = rows[:limit]
        lines = ["| " + " | ".join(c.get("label") or c["field"] for c in columns) + " |",
                 "|" + "|".join("---" for _ in columns) + "|"]
        for r in rows:
            lines.append("| " + " | ".join(cell(fmt(r.get(c["field"]), c.get("format"), out.get(c["field"], "text"),
                                                     self._currency(c["field"]))) for c in columns) + " |")
        return lines

    def component(self, c: dict) -> str:
        t = c["type"]
        if t == "document":
            return self.document(c)
        if t == "stat":
            rec = self.vs.record(c["data"][5:]) or {}
            out = self.spec["views"][c["data"][5:]].get("out") or {}
            v = fmt(rec.get(c["value"]), c.get("format"), out.get(c["value"], "number"), self._currency(c["value"]))
            of = f" of {fmt(rec.get(c['of']), 'count')}" if c.get("of") else ""
            foot = f" ({rec.get(c['foot'])})" if c.get("foot") and rec.get(c["foot"]) is not None else ""
            return f"**{v}**{of} {c['label'].lower()}{foot}"
        if t in ("table", "queue"):
            rows, _ = self._rows(c["data"])
            head = f"## {c['title']}\n\n" if c.get("title") else ""
            if not rows:
                return head + (c.get("empty") or "Nothing here.")
            cols = c["columns"] + ([{"field": "mark", "label": "Status"}] if t == "queue" else [])
            body = "\n".join(self.table(c["data"], cols, c.get("limit")))
            hint = f"\n\nMark one: `app mark <{c['key']}> done`" if t == "queue" else ""
            return head + body + hint
        if t == "chart":
            rows, _ = self._rows(c["data"])
            if not rows:
                return c.get("empty") or "Not enough periods yet."
            periods = list(dict.fromkeys(r["period"] for r in rows))
            bys = list(dict.fromkeys(r["by"] for r in rows))
            val = {(r["period"], r["by"]): r["value"] for r in rows}
            lines = ["| | " + " | ".join(periods) + " |", "|---|" + "|".join("---:" for _ in periods) + "|"]
            for b in bys:
                lines.append(f"| {cell(b)} | " + " | ".join(fmt(val.get((p, b), "0"), "count") for p in periods) + " |")
            return "\n".join(lines)
        if t == "text":
            return c["md"]
        if t == "approvals":
            pend = [a for a in self.vs.store.sys("approvals") if a.get("state") == "pending"]
            if not pend:
                return "Nothing waiting for approval."
            return "\n".join(f"{i}. {a.get('summary') or a['step']}" for i, a in enumerate(pend, 1)) + \
                "\n\nReply `approve 1,3` or `reject 2`."
        return ""

    def document(self, c: dict) -> str:
        out = []
        if c.get("doc_title"):
            out += [f"# {self.text(c['doc_title'])}", ""]
        elif c.get("title"):
            out += [f"# {c['title']}", ""]
        for sec in c["sections"]:
            out.append(f"## {self.text(sec['title'])}")
            if "table" in sec:
                rows, _ = self._rows(sec["table"]["data"])
                out += self.table(sec["table"]["data"], sec["table"]["columns"]) if rows or not sec.get("empty") else [sec["empty"]]
            elif "text" in sec:
                rows, _ = self._rows(sec["text"]["data"])
                v = rows[0].get(sec["text"]["field"]) if rows else None
                out.append(v if v else sec.get("empty") or "")
            elif "list" in sec:
                rows, _ = self._rows(sec["list"]["data"])
                out += [f"- {tpl(sec['list']['item'], r)}" for r in rows] if rows else [sec.get("empty") or "None."]
            elif "cards" in sec:
                rows, _ = self._rows(sec["cards"]["data"])
                if not rows:
                    out.append(sec.get("empty") or "None yet.")
                for i, r in enumerate(rows):
                    title, body = r.get(sec["cards"]["title"]), r.get(sec["cards"]["body"])
                    out += [f"### {'' if title is None else title}", "" if body is None else str(body)]  # render.ts: ?? ""
                    if i < len(rows) - 1:
                        out.append("")
            out.append("")
        if c.get("footer"):
            out.append(self.text(c["footer"]))
        return "\n".join(out).rstrip("\n") + "\n"

    def page(self, page_id: str) -> str:
        page = next((p for p in self.spec["pages"] if p["id"] == page_id), None)
        if page is None:
            comp = next((c for p in self.spec["pages"] for c in p["components"] if c["id"] == page_id), None)
            if comp is None:
                raise KeyError(f"no page or component {page_id}")
            return self.component(comp)
        parts = [f"# {page['title']}"]
        for c in page["components"]:
            if c["type"] in ("upload", "run_button", "mapping", "connection"):
                continue
            md = self.component(c)
            if md:
                parts.append(md.rstrip("\n"))
        return "\n\n".join(parts) + "\n"
