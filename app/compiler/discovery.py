"""Fast, compiler-independent discovery of modules in Verilog sources.

This is only used to populate the UI (module list, top-level candidates) before
compilation. Exact port widths/directions always come from the real compiler
(see compiler.py), never from this parser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "logic", "assign", "always",
    "always_ff", "always_comb", "always_latch", "initial", "begin", "end", "if", "else", "case",
    "casez", "casex", "endcase", "for", "while", "repeat", "forever", "function", "endfunction",
    "task", "endtask", "generate", "endgenerate", "genvar", "integer", "real", "parameter",
    "localparam", "defparam", "default", "posedge", "negedge", "or", "and", "not", "nand", "nor",
    "xor", "xnor", "buf", "bufif0", "bufif1", "notif0", "notif1", "supply0", "supply1", "tri",
    "signed", "unsigned", "return", "typedef", "enum", "struct", "packed", "wait", "disable",
    "fork", "join", "specify", "endspecify", "time", "event", "force", "release", "deassign",
    "do", "unique", "priority", "import", "export", "package", "endpackage", "interface",
    "endinterface", "modport", "int", "bit", "byte", "shortint", "longint", "automatic",
}

_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_STRING_RE = re.compile(r'"(?:\\.|[^"\\\n])*"')
_MODULE_RE = re.compile(r"\b(?:module|macromodule)\s+(?:automatic\s+|static\s+)?([A-Za-z_][\w$]*)(.*?)\bendmodule\b", re.S)
# <type> [#(...)] <instance> (   -- parameter list may contain one nesting level of parens
_INST_RE = re.compile(r"(?<![\w$.`'])([A-Za-z_][\w$]*)\s*(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?([A-Za-z_][\w$]*)\s*(?:\[[^\]]*\]\s*)?\(")


@dataclass
class ModuleInfo:
    name: str
    file: Path
    line: int
    instantiates: set[str] = field(default_factory=set)


def strip_comments(text: str) -> str:
    # Keep newlines so line numbers stay correct.
    text = _COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return _STRING_RE.sub('""', text)


def discover_modules(files: list[Path]) -> dict[str, ModuleInfo]:
    """Return {module_name: ModuleInfo} across all files."""
    modules: dict[str, ModuleInfo] = {}
    bodies: dict[str, str] = {}
    for f in files:
        try:
            text = strip_comments(Path(f).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for m in _MODULE_RE.finditer(text):
            name = m.group(1)
            line = text.count("\n", 0, m.start()) + 1
            modules[name] = ModuleInfo(name, Path(f), line)
            bodies[name] = m.group(2)
    for name, body in bodies.items():
        for m in _INST_RE.finditer(body):
            typ, inst = m.group(1), m.group(2)
            if typ in _KEYWORDS or inst in _KEYWORDS:
                continue
            modules[name].instantiates.add(typ)
    return modules


def top_candidates(modules: dict[str, ModuleInfo]) -> list[str]:
    """Modules that are never instantiated by another known module, sorted by name."""
    used = set()
    for m in modules.values():
        used |= {t for t in m.instantiates if t in modules and t != m.name}
    tops = sorted(n for n in modules if n not in used)
    return tops or sorted(modules)


def missing_modules(modules: dict[str, ModuleInfo]) -> dict[str, set[str]]:
    """Instantiated module types that are not defined in any source: {type: {parents}}."""
    missing: dict[str, set[str]] = {}
    for m in modules.values():
        for t in m.instantiates:
            if t not in modules:
                missing.setdefault(t, set()).add(m.name)
    return missing
