"""Rule-based crash patches ("playbook").

Each fixer looks at the exception and the exact source line that crashed and returns a
minimal, behaviour-preserving guard for that line, or None if it doesn't apply.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

STR_METHODS = {m for m in dir(str) if not m.startswith("_")}
LIST_METHODS = {m for m in dir(list) if not m.startswith("_")}
DICT_METHODS = {m for m in dir(dict) if not m.startswith("_")}

Patch = Tuple[str, str]  # (new_line, explanation)


def _receiver_start(line: str, end: int) -> int:
    """Scan left from `end` over a Python primary expression (names, attributes, calls,
    subscripts, string literals) and return where it starts."""
    i = end - 1
    while i >= 0:
        c = line[i]
        if c in ")]}":
            close, opn, depth = c, {")": "(", "]": "[", "}": "{"}[c], 0
            while i >= 0:
                if line[i] == close:
                    depth += 1
                elif line[i] == opn:
                    depth -= 1
                    if depth == 0:
                        break
                i -= 1
            i -= 1
            continue
        if c in "\"'":
            q = c
            i -= 1
            while i >= 0 and line[i] != q:
                i -= 1
            i -= 1
            continue
        if c.isalnum() or c in "_.":
            i -= 1
            continue
        break
    return i + 1


def _none_attr(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    m = re.search(r"'NoneType' object has no attribute '(\w+)'", msg)
    if exc_type != "AttributeError" or not m:
        return None
    attr = m.group(1)
    default = '""' if attr in STR_METHODS else "{}" if attr in DICT_METHODS else "[]" if attr in LIST_METHODS else None
    if default is None:
        return None
    for am in re.finditer(r"\." + re.escape(attr) + r"\b", line):
        start = _receiver_start(line, am.start())
        recv = line[start:am.start()]
        if not recv.strip() or recv.endswith(f" or {default})"):
            continue
        new = line[:start] + f"({recv} or {default})" + line[am.start():]
        kind = {'""': "an empty string", "{}": "an empty dict", "[]": "an empty list"}[default]
        return new, (f"`{recv}` can be None, so calling `.{attr}()` on it raised AttributeError. "
                     f"Fall back to {kind} when the value is missing.")
    return None


def _index(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    if exc_type != "IndexError" or "index out of range" not in msg:
        return None
    is_str = msg.startswith("string")
    for im in re.finditer(r"\[(-?\d+)\]", line):
        start = _receiver_start(line, im.start())
        recv = line[start:im.start()]
        if not recv.strip() or " or [" in recv[-12:]:
            continue
        if is_str:
            default = '" "'
        else:
            default = '[""]' if recv.rstrip().endswith((".split()", ".splitlines()")) else "[None]"
        new = line[:start] + f"({recv} or {default})" + line[im.start():]
        return new, (f"`{recv}` can be empty, so indexing `[{im.group(1)}]` raised IndexError. "
                     f"Use a safe default element when it is empty.")
    return None


def _key(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    if exc_type != "KeyError":
        return None
    key = msg.strip()
    if not (len(key) >= 2 and key[0] == key[-1] and key[0] in "'\""):
        return None
    k = key[1:-1]
    for km in re.finditer(r"\[\s*(['\"])" + re.escape(k) + r"\1\s*\]", line):
        after = line[km.end():].lstrip()
        if after.startswith("=") and not after.startswith("=="):
            continue  # assignment target — not a read
        new = line[:km.start()] + f'.get("{k}")' + line[km.end():]
        return new, f"The key `{k}` may be absent, so `[\"{k}\"]` raised KeyError. Read it with `.get()` instead."
    return None


def _operand_end(line: str, i: int) -> int:
    """Scan right from i over a primary expression with balanced brackets."""
    n = len(line)
    while i < n and line[i] == " ":
        i += 1
    while i < n:
        c = line[i]
        if c in "([{":
            close, depth = {"(": ")", "[": "]", "{": "}"}[c], 0
            while i < n:
                if line[i] == c:
                    depth += 1
                elif line[i] == close:
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            i += 1
            continue
        if c.isalnum() or c in "_.":
            i += 1
            continue
        break
    return i


def _zero_div(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    if exc_type != "ZeroDivisionError":
        return None
    for m in re.finditer(r"(?<!/)/(?!/)", line):
        j = m.start()
        k = j
        while k > 0 and line[k - 1] == " ":
            k -= 1
        a_start = _receiver_start(line, k)
        b_end = _operand_end(line, j + 1)
        a, b = line[a_start:k], line[j + 1:b_end].strip()
        if not a.strip() or not b:
            continue
        new = line[:a_start] + f"({a} / {b} if {b} else 0)" + line[b_end:]
        return new, f"`{b}` can be zero, so `{a} / {b}` raised ZeroDivisionError. Return 0 when there is nothing to divide by."
    return None


FIXERS = [_none_attr, _index, _key, _zero_div]


def propose(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    for f in FIXERS:
        r = f(line, exc_type, msg)
        if r and r[0] != line:
            return r
    return None


def patch_source(source: str, lineno: int, exc_type: str, msg: str) -> Optional[Tuple[str, str, str, str]]:
    """Returns (new_source, old_line, new_line, explanation) or None."""
    lines = source.splitlines(keepends=True)
    if not 1 <= lineno <= len(lines):
        return None
    raw = lines[lineno - 1]
    body = raw.rstrip("\r\n")
    nl = raw[len(body):]
    r = propose(body, exc_type, msg)
    if not r:
        return None
    lines[lineno - 1] = r[0] + nl
    return "".join(lines), body.strip(), r[0].strip(), r[1]


# ---------------------------------------------------------------- Kotlin / Android
KT_STRING_METHODS = {"trim", "lowercase", "uppercase", "length", "isBlank", "isEmpty", "split", "replace",
                     "substring", "startsWith", "endsWith", "contains", "toInt", "toLong", "trimStart", "trimEnd"}


def _kt_get_value(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    """`map.getValue(key)` throws NoSuchElementException (or NPE on platform maps) when the key is missing."""
    if not re.search(r"NoSuchElement|NullPointer", exc_type):
        return None
    m = re.search(r"([\w\.]+)\.getValue\(([^()]+)\)", line)
    if not m:
        return None
    recv, key = m.group(1), m.group(2)
    after = line[m.end():]
    sm = re.match(r"\.(\w+)", after)
    if sm and sm.group(1) in KT_STRING_METHODS:
        repl = f"{recv}[{key}].orEmpty()"
    else:
        repl = f"{recv}[{key}]"
    return (line[:m.start()] + repl + after,
            f"`{recv}` doesn't always contain {key}, so `getValue` threw. Read it with `[{key}]` and default a missing value.")


def _kt_npe(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    if "NullPointerException" not in exc_type:
        return None
    m = re.search(r"!!\.(\w+)", line)
    if m and m.group(1) in KT_STRING_METHODS:
        start = _receiver_start(line, m.start())
        recv = line[start:m.start()]
        new = line[:start] + f"{recv}.orEmpty()" + line[m.start() + 2:]
        return new, (f"`{recv}` can be null, so the `!!` assertion threw NullPointerException. "
                     f"Use `.orEmpty()` so a missing value becomes an empty string.")
    m = re.search(r"([\w\.\[\]\"\(\)]+)!!", line)
    if m:
        expr = m.group(1)
        new = line.replace(expr + "!!", f"{expr}?", 1)
        if re.match(r"\s*(val|var)\s+\w+\s*=", new) and "?:" not in new:
            new = new.rstrip() + " ?: return@launch" if "launch" in line else new
        return new, f"`{expr}` can be null, so `!!` threw NullPointerException. Use a safe call instead."
    m = re.search(r"invoke (?:virtual|interface) method '[^']*?(\w+)\(\)' on a null object reference", msg)
    if m:
        meth = m.group(1)
        mm = re.search(r"\." + re.escape(meth) + r"\(", line)
        if mm:
            new = line[:mm.start()] + "?." + line[mm.start() + 1:]
            return new, f"The receiver of `{meth}()` can be null. Use a safe call (`?.{meth}()`)."
    return None


def _kt_index(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    if exc_type in ("NoSuchElementException",) and re.search(r"\.first\(\)", line):
        return line.replace(".first()", ".firstOrNull()", 1), "The collection can be empty, so `first()` threw. Use `firstOrNull()`."
    if exc_type in ("IndexOutOfBoundsException", "ArrayIndexOutOfBoundsException", "StringIndexOutOfBoundsException"):
        m = re.search(r"(\w[\w\.]*)\[(\w+)\]", line)
        if m:
            return (line[:m.start()] + f"{m.group(1)}.getOrNull({m.group(2)})" + line[m.end():],
                    f"`{m.group(1)}` can be shorter than expected. Use `getOrNull()`.")
    if exc_type == "NumberFormatException" and ".toInt()" in line:
        return line.replace(".toInt()", ".toIntOrNull() ?: 0", 1), "The text isn't always a number. Use `toIntOrNull()` with a default."
    return None


def _kt_div_zero(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    """ArithmeticException: divide by zero. A literal `/0` can never succeed — it's leftover debug code, so the
    statement is disabled. A variable divisor gets a zero guard."""
    if exc_type != "ArithmeticException" and "by zero" not in msg:
        return None
    code = line.split("//", 1)[0]
    if re.search(r"/\s*0(?![\w.])", code):
        indent = line[:len(line) - len(line.lstrip())]
        stmt = re.sub(r"\s*//.*$", "", line.strip())          # drop old trailing comments (MH-DEMO-BUG, earlier notes)
        return (indent + "// " + stmt + "  // disabled by MobileHeal: divides by zero",
                "This statement divides by the literal 0, so it always throws ArithmeticException. It is debug code — "
                "disable it.")
    m = re.search(r"(\w[\w.]*)\s*/\s*(\w[\w.]*)", code)
    if m:
        a, b = m.group(1), m.group(2)
        return (line[:m.start()] + f"(if ({b} != 0) {a} / {b} else 0)" + line[m.end():],
                f"`{b}` can be 0, so `{a} / {b}` threw ArithmeticException. Guard the division.")
    return None


def propose_kotlin(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    for f in (_kt_div_zero, _kt_get_value, _kt_npe, _kt_index):
        r = f(line, exc_type, msg)
        if r and r[0] != line:
            return r
    return None


def propose_swift(line: str, exc_type: str, msg: str) -> Optional[Patch]:
    """Swift playbook: a force unwrap (`x!`) that found nil → nil-coalesce to a safe default."""
    if not re.search(r"nil|unwrap|EXC_BREAKPOINT|SIGTRAP|Fatal error", f"{exc_type} {msg}", re.I):
        return None
    m = re.search(r"((?:[A-Za-z_][\w.]*)(?:\[[^\]]+\])?)!(?=\s*[.)\s,]|$)", line)
    if not m:
        return None
    expr = m.group(1)
    default = '""' if re.search(r"\[\s*\"", expr) or "String" in line or "trimming" in line else None
    if default is None:
        new = line[:m.start()] + expr + "?" + line[m.end():]
        why = f"`{expr}` was nil, so the force unwrap crashed. Use optional chaining instead of `!`."
    else:
        new = line[:m.start()] + f"({expr} ?? {default})" + line[m.end():]
        why = f"`{expr}` was nil, so the force unwrap crashed. Fall back to an empty string with `?? \"\"`."
    return (new, why)


def patch_source_lang(source: str, lineno: int, exc_type: str, msg: str, lang: str):
    if lang == "python":
        return patch_source(source, lineno, exc_type, msg)
    lines = source.splitlines(keepends=True)
    if not 1 <= lineno <= len(lines):
        return None
    raw = lines[lineno - 1]
    body = raw.rstrip("\r\n")
    r = propose_swift(body, exc_type, msg) if lang == "swift" else propose_kotlin(body, exc_type, msg)
    if not r:
        return None
    lines[lineno - 1] = r[0] + raw[len(body):]
    return "".join(lines), body.strip(), r[0].strip(), r[1]


def static_validate(source: str, lang: str) -> Optional[str]:
    """Cheap structural check; returns an error message or None."""
    if lang == "python":
        import ast
        try:
            ast.parse(source)
        except SyntaxError as e:
            return f"syntax error at line {e.lineno}: {e.msg}"
        return None
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', source)
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    for o, c in ("()", "{}", "[]"):
        if text.count(o) != text.count(c):
            return f"unbalanced {o}{c} ({text.count(o)} vs {text.count(c)})"
    return None
