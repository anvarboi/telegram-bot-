"""Restoran xarajatlari: matn parser, SQLite baza va hisobotlar."""
import html
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5))  # Toshkent


class InputError(ValueError):
    """Foydalanuvchi kiritgan ma'lumot noto'g'ri."""


class ParseError(InputError):
    pass


# ---------------------------------------------------------------- parser
_APOS = str.maketrans({c: "'" for c in "’ʻʼ‘`´ʹ′"})
_TOKEN = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)*)"
    r"|(?P<word>[^\W\d_]+(?:['\-][^\W\d_]+)*'?)"
    r"|(?P<eq>=)"
)
_GROUP = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")

_MULT = {
    "ming": 1_000, "k": 1_000, "минг": 1_000,
    "mln": 1_000_000, "mlyon": 1_000_000, "million": 1_000_000, "млн": 1_000_000,
}
_STOP = {"dan", "so'm", "som", "sum", "сум", "сўм", "so`m"}
_EQ_WORDS = {"jami", "jam", "umumiy", "итого", "жами"}
_UNITS = {
    "kg": ("kg", 1), "kilo": ("kg", 1), "kilogramm": ("kg", 1), "кг": ("kg", 1),
    "gr": ("kg", 0.001), "g": ("kg", 0.001), "gram": ("kg", 0.001), "гр": ("kg", 0.001),
    "litr": ("litr", 1), "l": ("litr", 1), "lt": ("litr", 1), "liter": ("litr", 1), "л": ("litr", 1),
    "ta": ("dona", 1), "dona": ("dona", 1), "dn": ("dona", 1), "шт": ("dona", 1), "дона": ("dona", 1),
}


def _dec(raw):
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise ParseError(f"Raqam noto'g'ri: {raw}")


def _price_value(rec):
    raw = rec["raw"]
    if rec["mult"] != 1:
        return _dec(raw) * rec["mult"]
    if _GROUP.match(raw):
        return float(re.sub(r"[.,]", "", raw))
    return _dec(raw)


def parse_line(s):
    s = s.translate(_APOS)
    s = re.sub(r"/\s*[^\W\d_]+", " ", s)  # "so'm/kg" -> "so'm"
    seq = []
    unit = None
    pending_total = False
    for m in _TOKEN.finditer(s):
        kind = m.lastgroup
        if kind == "num":
            seq.append(("num", {"raw": m.group(), "mult": 1,
                                "explicit": pending_total, "total": pending_total}))
            pending_total = False
        elif kind == "eq":
            pending_total = True
        else:
            w = m.group().lower()
            last = seq[-1] if seq else None
            if w in _EQ_WORDS:
                pending_total = True
            elif w in _MULT and last and last[0] == "num" and last[1]["mult"] == 1:
                last[1]["mult"] = _MULT[w]
                last[1]["explicit"] = True
            elif w == "dan" and last and last[0] == "num":
                last[1]["explicit"] = True
            elif w in _STOP:
                pass
            elif w in _UNITS and unit is None:
                unit = _UNITS[w]
                seq.append(("unit", None))
            else:
                seq.append(("word", w))

    nums = [r for t, r in seq if t == "num"]
    names = [w for t, w in seq if t == "word"]
    if not nums:
        raise ParseError("Raqam topilmadi. Namuna: Piyoz 10 kg 8000")
    if not names:
        raise ParseError("Mahsulot nomi yo'q. Namuna: Piyoz 10 kg 8000")

    qty = None
    if unit:
        i = next(k for k, (t, _) in enumerate(seq) if t == "unit")
        for j in (i - 1, i + 1):
            if 0 <= j < len(seq) and seq[j][0] == "num" and not seq[j][1]["explicit"]:
                qty = seq[j][1]
                break
    if qty is None:
        free = [r for r in nums if not r["explicit"]]
        if len(nums) == 2 and len(free) == 2:
            qty = nums[0]
        elif len(nums) == 2 and len(free) == 1:
            qty = free[0]
        elif len(nums) == 1 and not unit:
            raise ParseError("Narx yo'q. Namuna: Piyoz 10 kg 8000")
        elif len(nums) > 2:
            raise ParseError("Ortiqcha raqam bor. Namuna: Piyoz 10 kg 8000")
        else:
            raise ParseError("Miqdor topilmadi. Namuna: Piyoz 10 kg 8000")

    rest = [r for r in nums if r is not qty]
    if not rest:
        raise ParseError("Narx yo'q. Namuna: Piyoz 10 kg 8000")
    if len(rest) > 1:
        raise ParseError("Ortiqcha raqam bor. Namuna: Piyoz 10 kg 8000")

    price_rec = rest[0]
    price = _price_value(price_rec)
    q = _dec(qty["raw"])
    unit_name = ""
    if unit:
        unit_name, k = unit
        q = q * k
    if q <= 0:
        raise ParseError("Miqdor 0 dan katta bo'lishi kerak")
    if price <= 0:
        raise ParseError("Narx 0 dan katta bo'lishi kerak")

    total = price if price_rec["total"] else q * price
    return {
        "name": " ".join(names).capitalize(),
        "qty": q,
        "unit": unit_name,
        "total": int(round(total)),
    }


# ------------------------------------------------------------------ baza
RETENTION_DAYS = 31      # kunlik ma'lumot saqlanish muddati
MONTH_DAYS = 30          # "oylik" hisobot: oxirgi 30 kun
MAX_RESTAURANTS = 10     # bitta profilga ko'pi bilan


def today():
    return datetime.now(TZ).date()


def esc(s):
    return html.escape(str(s))


def init_db(path="data.db"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chats(
            chat_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS restaurants(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rest_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            name TEXT NOT NULL,
            qty REAL NOT NULL,
            unit TEXT NOT NULL,
            total INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_rest_day ON items(rest_id, day);
        CREATE INDEX IF NOT EXISTS idx_rest_user ON restaurants(user_id);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conn.commit()
    return conn


# --- profil (telefon raqam orqali)
def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:          # 901234567 -> 998901234567
        digits = "998" + digits
    if not 10 <= len(digits) <= 15:
        raise InputError("Telefon raqami noto'g'ri")
    return "+" + digits


def get_or_create_user(conn, phone):
    phone = normalize_phone(phone)
    row = conn.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO users(phone, created) VALUES(?, ?)", (phone, today().isoformat())
    )
    conn.commit()
    return cur.lastrowid


def link_chat(conn, chat_id, user_id):
    conn.execute(
        "INSERT OR REPLACE INTO chats(chat_id, user_id) VALUES(?, ?)", (chat_id, user_id)
    )
    conn.commit()


def get_user_by_chat(conn, chat_id):
    row = conn.execute("SELECT user_id FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
    return row["user_id"] if row else None


# --- restoranlar
def add_restaurant(conn, user_id, name):
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        raise InputError("Nom bo'sh bo'lmasin ✍️")
    if len(name) > 40:
        raise InputError("Nom juda uzun (ko'pi bilan 40 belgi)")
    existing = list_restaurants(conn, user_id)
    if len(existing) >= MAX_RESTAURANTS:
        raise InputError(f"Ko'pi bilan {MAX_RESTAURANTS} ta joy qo'shish mumkin 🏪")
    if any(r["name"].lower() == name.lower() for r in existing):
        raise InputError("Bunday nomli joy allaqachon bor 🔁")
    cur = conn.execute(
        "INSERT INTO restaurants(user_id, name, created) VALUES(?, ?, ?)",
        (user_id, name, today().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def list_restaurants(conn, user_id):
    rows = conn.execute(
        "SELECT * FROM restaurants WHERE user_id=? ORDER BY id", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_restaurant(conn, rest_id, user_id):
    """Faqat shu foydalanuvchiga tegishli restoranni qaytaradi."""
    row = conn.execute(
        "SELECT * FROM restaurants WHERE id=? AND user_id=?", (rest_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, str(value)))
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_selected(conn, chat_id, rest_id):
    set_setting(conn, f"sel:{chat_id}", rest_id)


def get_selected(conn, chat_id):
    v = get_setting(conn, f"sel:{chat_id}")
    return int(v) if v is not None else None


# --- yozuvlar
def add_item(conn, rest_id, parsed, day=None):
    day = (day or today()).isoformat()
    cur = conn.execute(
        "INSERT INTO items(rest_id, day, name, qty, unit, total) VALUES(?,?,?,?,?,?)",
        (rest_id, day, parsed["name"], parsed["qty"], parsed["unit"], parsed["total"]),
    )
    conn.commit()
    return cur.lastrowid


def day_items(conn, rest_id, day):
    return conn.execute(
        "SELECT * FROM items WHERE rest_id=? AND day=? ORDER BY id",
        (rest_id, day.isoformat()),
    ).fetchall()


def day_total(conn, rest_id, day=None):
    row = conn.execute(
        "SELECT COALESCE(SUM(total), 0) AS s FROM items WHERE rest_id=? AND day=?",
        (rest_id, (day or today()).isoformat()),
    ).fetchone()
    return int(row["s"])


def delete_item(conn, item_id):
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.commit()
    return dict(row)


def delete_last(conn, rest_id):
    row = conn.execute(
        "SELECT id FROM items WHERE rest_id=? ORDER BY id DESC LIMIT 1", (rest_id,)
    ).fetchone()
    return delete_item(conn, row["id"]) if row else None


def purge_old(conn, now=None):
    """RETENTION_DAYS dan eski kunlik ma'lumotlarni o'chiradi."""
    cutoff = (now or today()) - timedelta(days=RETENTION_DAYS)
    cur = conn.execute("DELETE FROM items WHERE day < ?", (cutoff.isoformat(),))
    conn.commit()
    return cur.rowcount


# --- hisobotlar
def fmt(n):
    return f"{int(round(n)):,}".replace(",", " ")


def fmt_qty(q):
    return f"{q:.3f}".rstrip("0").rstrip(".")


def fmt_date(d):
    return d.strftime("%d.%m.%Y")


def _qty_unit(qty, unit):
    return fmt_qty(qty) + (f" {unit}" if unit else "")


def day_report(conn, rest, day):
    rows = day_items(conn, rest["id"], day)
    head = f"📅 <b>{fmt_date(day)}</b> — {esc(rest['name'])}"
    if not rows:
        return head + "\n\n📭 Ma'lumot yo'q."
    lines = [head, ""]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {esc(r['name'])} — {_qty_unit(r['qty'], r['unit'])}"
            f" × {fmt(r['total'] / r['qty'])} = {fmt(r['total'])}"
        )
    lines += ["", f"🧾 Jami: <b>{fmt(sum(r['total'] for r in rows))}</b> so'm"]
    return "\n".join(lines)


def _table(rows, unit):
    header = ("Sana", "Miqdor", "Narx", "Summa")
    data = [
        (
            date.fromisoformat(r["day"]).strftime("%d.%m"),
            _qty_unit(r["qty"], unit),
            fmt(r["total"] / r["qty"]),
            fmt(r["total"]),
        )
        for r in rows
    ]
    tq = sum(r["qty"] for r in rows)
    tt = sum(r["total"] for r in rows)
    total = ("Jami", _qty_unit(tq, unit), fmt(tt / tq), fmt(tt))
    allrows = [header] + data + [total]
    w = [max(len(row[c]) for row in allrows) for c in range(4)]

    def line(row):
        return "  ".join(
            row[c].ljust(w[c]) if c == 0 else row[c].rjust(w[c]) for c in range(4)
        )

    sep = "─" * len(line(header))
    return "\n".join([line(header), sep] + [line(r) for r in data] + [sep, line(total)])


def monthly_blocks(conn, rest, d1, d2):
    """Mahsulotlar kesimida jadval; har bir blok mustaqil HTML bo'lak."""
    rows = conn.execute(
        "SELECT * FROM items WHERE rest_id=? AND day BETWEEN ? AND ? ORDER BY day, id",
        (rest["id"], d1.isoformat(), d2.isoformat()),
    ).fetchall()
    head = (
        f"📆 <b>Oylik ma'lumot</b> — {esc(rest['name'])}\n"
        f"{fmt_date(d1)} – {fmt_date(d2)}"
    )
    if not rows:
        return [head + "\n\n📭 Ma'lumot yo'q."]
    groups = {}
    for r in rows:
        groups.setdefault((r["name"], r["unit"]), []).append(r)
    blocks = [head + "\n<i>«Jami» qatorida narx — o'rtacha narx.</i>"]
    for (name, unit), grp in sorted(groups.items()):
        blocks.append(f"<b>{esc(name)}</b>\n<pre>{_table(grp, unit)}</pre>")
    grand = sum(r["total"] for r in rows)
    blocks.append(
        f"💰 Umumiy xarajat: <b>{fmt(grand)}</b> so'm  ({len(groups)} xil mahsulot)"
    )
    return blocks


def pack_blocks(blocks, limit=4000):
    out, cur = [], ""
    for b in blocks:
        add = ("\n\n" if cur else "") + b
        if cur and len(cur) + len(add) > limit:
            out.append(cur)
            cur = b
        else:
            cur += add
    if cur:
        out.append(cur)
    return out


def parse_date(text):
    """23.05.2026, 23/05/2026, 23-05-26, 23.05 (joriy yil), 2026-05-23."""
    t = re.sub(r"[/\-,\s]", ".", (text or "").strip())
    try:
        m = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", t)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?", t)
        if m:
            y = m[3]
            year = today().year if y is None else (2000 + int(y) if len(y) == 2 else int(y))
            return date(year, int(m[2]), int(m[1]))
    except ValueError:
        return None
    return None


def split_message(text, limit=4000):
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts
