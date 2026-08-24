"""Mutant generation for the whole-codebase sweep (mutation-sweep-S.md §2).

Two disjoint passes over one module:

  * the **Python pass** (§2.1), kept byte-identical to the bounded run's
    operator table so the two sweeps' kill rates stay comparable. Comments and
    string literals are masked, so a Python operator never applies inside a
    string.
  * the **SQL pass** (§2.2), which applies *only* inside string literals, and
    only those holding a SQL keyword -- which is what keeps template names,
    URLs and log messages out of it.

Every mutant is a single substring swap on one line. That is the property that
makes a survivor inspectable by eye rather than a puzzle, and it holds for the
SQL pass too: a triple-quoted query is mutated one line at a time.
"""
import ast
import io
import re
import tokenize

# --- Python pass (§2.1) -- byte-identical to the bounded run -----------------
# (name, pattern, replacement) -- applied to code text only.
OPS = [
    ("cmp<=",   r"(?<![<>=!])<=",        "<"),
    ("cmp<",    r"(?<![<>=!])<(?![=])",  "<="),
    ("cmp>=",   r"(?<![<>=!])>=",        ">"),
    ("cmp>",    r"(?<![<>=!-])>(?![=])", ">="),
    ("eq",      r"(?<![<>=!])==",        "!="),
    ("ne",      r"!=",                   "=="),
    ("isnot",   r"\bis not\b",           "is"),
    ("is",      r"\bis\b(?! not)",       "is not"),
    ("and",     r"\band\b",              "or"),
    ("or",      r"\bor\b",               "and"),
    ("not_in",  r"\bnot in\b",           "in"),
    ("in",      r"(?<!not )\bin\b",      "not in"),
    ("true",    r"\bTrue\b",             "False"),
    ("false",   r"\bFalse\b",            "True"),
    ("none_chk",r"\bis None\b",          "is not None"),
    ("max",     r"\bmax\(",              "min("),
    ("min",     r"\bmin\(",              "max("),
    ("revT",    r"reverse=True",         "reverse=False"),
    ("neg",     r"key=lambda ([a-z_]+): -", r"key=lambda \1: +"),
    ("num",     r"(?<![\w.])(\d+)(?![\w.])", None),   # n -> n+1
]

# --- SQL pass (§2.2) --------------------------------------------------------
#: A string literal is eligible only if it contains one of these. **Case
#: sensitive, and that is the whole of its precision**: the codebase writes SQL
#: keywords in upper case, while `delete`, `from`, `where` and `update` are
#: ordinary English words that appear in docstrings and log messages. Matching
#: case-insensitively made `normalize.py` -- which contains no SQL whatsoever --
#: report 11 SQL mutants off the phrase "delete punctuation" in its docstring.
SQL_ELIGIBLE = re.compile(
    r"\b(SELECT|FROM|WHERE|JOIN|GROUP\s+BY|ORDER\s+BY|INSERT|UPDATE|DELETE)\b"
)

SQL_OPS = [
    # `=` -> `<>` is ~half of this pass and was kept knowingly: most should die
    # easily, and a survivor is the interesting case -- a filter or join
    # condition nothing asserts.
    ("sql=",        r"(?<![<>=!])=(?![=<>])",       "<>"),
    ("sql>=",       r"(?<![<>=!])>=",               ">"),
    ("sql>",        r"(?<![<>=!])>(?![=<])",        ">="),
    ("sql<=",       r"(?<![<>=!])<=",               "<"),
    ("sql<",        r"(?<![<>=!])<(?![=>])",        "<="),
    ("sqlMIN",      r"\bMIN\s*\(",                  "MAX("),
    ("sqlMAX",      r"\bMAX\s*\(",                  "MIN("),
    ("sqlASC",      r"\bASC\b",                     "DESC"),
    ("sqlDESC",     r"\bDESC\b",                    "ASC"),
    ("sqlLEFTJOIN", r"\bLEFT\s+JOIN\b",             "JOIN"),
    ("sqlDISTINCT", r"\bDISTINCT\s+",               ""),
    ("sqlAND",      r"\bAND\b",                     "OR"),
    ("sqlOR",       r"\bOR\b",                      "AND"),
    ("sqlISNOTNULL",r"\bIS\s+NOT\s+NULL\b",         "IS NULL"),
    ("sqlISNULL",   r"\bIS\s+NULL\b",               "IS NOT NULL"),
    ("sqlNOTIN",    r"\bNOT\s+IN\b",                "IN"),
    ("sqlIN",       r"(?<!NOT )\bIN\b",             "NOT IN"),
    ("sqlnum",      r"(?<![\w.])(\d+)(?![\w.])",    None),   # n -> n+1
]
#: The operators are case sensitive for the same reason the eligibility test
#: is: inside an upper-case query, a lower-case `in` or `and` is prose in a SQL
#: comment or part of an identifier, not the keyword.
_SQL_CASE_INSENSITIVE = frozenset()

#: `in` inside a `for` header is a loop keyword, not a membership test (§2.1).
FOR_IN = re.compile(r"\bfor\s+[\w,\s()\*]+?\s+(in)\b")


def _tokens(src):
    try:
        return list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []


def _spans_by_line(tok):
    """The (line, col_start, col_end) ranges one token covers."""
    if tok.start[0] == tok.end[0]:
        return [(tok.start[0], tok.start[1], tok.end[1])]
    out = []
    for ln in range(tok.start[0], tok.end[0] + 1):
        lo = tok.start[1] if ln == tok.start[0] else 0
        hi = tok.end[1] if ln == tok.end[0] else 10 ** 6
        out.append((ln, lo, hi))
    return out


def string_comment_ranges(src):
    """{line: [(col_start, col_end), ...]} covered by strings/comments."""
    out = {}
    for t in _tokens(src):
        if t.type in (tokenize.COMMENT, tokenize.STRING):
            for ln, lo, hi in _spans_by_line(t):
                out.setdefault(ln, []).append((lo, hi))
    return out


def schema_span(src):
    """(first_line, last_line) of a module-level `SCHEMA = ...` assignment.

    §2.2 excludes `db.SCHEMA` from the SQL pass: mutating `CREATE TABLE` DDL
    yields broken-or-equivalent mutants and nothing else. Located **in the
    AST**, never as a hardcoded line range, which drifts on the next migration.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SCHEMA":
                    return (node.value.lineno, node.value.end_lineno)
    return None


def sql_string_ranges(src):
    """{line: [(col_start, col_end), ...]} covered by SQL-bearing strings."""
    out = {}
    excluded = schema_span(src)
    for t in _tokens(src):
        if t.type != tokenize.STRING:
            continue
        if not SQL_ELIGIBLE.search(t.string):
            continue
        if excluded and excluded[0] <= t.start[0] <= excluded[1]:
            continue
        for ln, lo, hi in _spans_by_line(t):
            out.setdefault(ln, []).append((lo, hi))
    return out


def _apply(ops, line, allowed, inside, lineno, insensitive=()):
    """Mutants for one line. `inside` picks whether a match must be covered by
    `allowed` (the SQL pass) or must not be (the Python pass)."""
    found = []
    for name, pat, rep in ops:
        flags = re.I if name in insensitive else 0
        for m in re.finditer(pat, line, flags):
            covered = any(s <= m.start() and m.end() <= e for s, e in allowed)
            if covered is not inside:
                continue
            if rep is None:                       # numeric bump
                new_tok = str(int(m.group(1)) + 1)
            else:
                new_tok = m.expand(rep) if "\\" in rep else rep
            new_line = line[:m.start()] + new_tok + line[m.end():]
            if new_line == line:
                continue
            found.append({
                "op": name, "line": lineno, "col": m.start(),
                "before": line.strip()[:90], "after": new_line.strip()[:90],
                "new_line": new_line,
            })
    return found


def generate(path):
    """(src, lines, mutants) for one module -- both passes, filtered."""
    with open(path) as fh:
        src = fh.read()
    lines = src.splitlines(keepends=True)
    masked = string_comment_ranges(src)
    sql = sql_string_ranges(src)

    mutants = []
    for i, line in enumerate(lines, start=1):
        py = _apply(OPS, line, masked.get(i, []), False, i)
        # The `for ... in` filter, applied here so the count the generator
        # reports is the count the sweep actually runs.
        py = [m for m in py if not _is_for_keyword(line, m)]
        for m in py:
            m["pass"] = "py"
        sq = _apply(SQL_OPS, line, sql.get(i, []), True, i,
                    insensitive=_SQL_CASE_INSENSITIVE)
        for m in sq:
            m["pass"] = "sql"
        mutants.extend(py)
        mutants.extend(sq)
    return src, lines, mutants


def _is_for_keyword(line, m):
    if m["op"] not in ("in", "not_in"):
        return False
    return any(fm.start(1) == m["col"] for fm in FOR_IN.finditer(line))


if __name__ == "__main__":
    import collections
    import sys
    total = 0
    for path in sys.argv[1:]:
        _, _, ms = generate(path)
        total += len(ms)
        c = collections.Counter(m["pass"] for m in ms)
        print(f"{path}: {len(ms)} mutants  "
              f"(py={c.get('py', 0)}, sql={c.get('sql', 0)})")
    if len(sys.argv) > 2:
        print(f"TOTAL: {total}")
