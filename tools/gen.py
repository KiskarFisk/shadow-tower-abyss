#!/usr/bin/env python3
"""Naive decompiler for branch-free functions (leaf, tail-call, or straight-line with jal).
Usage (WSL, repo root): tools/batch.py asm $(tools/batch.py list 0x80 | cut -d" " -f1) | tools/gen.py > /tmp/c.c
                        tools/batch.py apply /tmp/c.c
Reads `batch.py asm` output on stdin, writes `// ---`-separated C candidates (several variants per
function) to stdout. Anything it doesn't understand is skipped; the checker decides what's right."""
import itertools, re, struct, sys

LOADS = {"lb": "signed char", "lbu": "unsigned char", "lh": "short", "lhu": "unsigned short",
         "lw": "int", "lwc1": "float", "ld": "long long"}
STORES = {"sb": "unsigned char", "sh": "unsigned short", "sw": "int", "swc1": "float", "sd": "long long"}
IREGS = ["$a0", "$a1", "$a2", "$a3"]
FREGS = ["$f12", "$f13", "$f14", "$f15"]
SAVED = re.compile(r"\$(ra|fp|s\d|f2\d|f3\d)$")
NARROW = ("signed char", "unsigned char", "short", "unsigned short")
BIN = {"addu": "+", "daddu": "+", "subu": "-", "dsubu": "-", "and": "&", "or": "|", "xor": "^",
       "slt": "<", "sltu": "<", "sllv": "<<", "srav": ">>", "srlv": ">>",
       "add.s": "+", "sub.s": "-", "mul.s": "*", "div.s": "/"}
IMM = {"addiu": "+", "daddiu": "+", "andi": "&", "ori": "|", "xori": "^", "slti": "<", "sltiu": "<",
       "sll": "<<", "sra": ">>", "srl": ">>"}
def INT(s):
    m = re.fullmatch(r"\((0x[0-9A-F]+) (>> 16|& 0xFFFF)\)", s)
    if m:
        v = int(m[1], 16)
        return v >> 16 if m[2] == ">> 16" else v & 0xFFFF
    return int(s, 0)


class Bail(Exception):
    pass


class E:
    def __init__(s, k, *a, t="int"):
        s.k, s.a, s.t = k, a, t


def gen(name, lines, narrow, want_ret):
    regs = {r: E("arg", i) for i, r in enumerate(IREGS)}
    regs.update({r: E("farg", i, t="float") for i, r in enumerate(FREGS)})
    regs["$zero"] = E("const", 0)
    his, externs, bft, protos = {}, {}, set(), {}
    stmts, argt, ptrargs, ntmp = [], {}, set(), [0]
    fresh = set(IREGS + FREGS)  # arg regs still holding entry values
    written = set()
    called = [False]
    tail = None

    ins = [l.strip() for l in lines if l.strip() and not l.strip().startswith((".L", "glabel", "endlabel"))]
    i = 0
    while i < len(ins) - 1:  # delay slots execute before the jump
        if ins[i].split()[0] in ("j", "jr", "jal", "jalr"):
            ins[i], ins[i + 1] = ins[i + 1], ins[i]
            i += 1
        i += 1

    def use_ptr(e):
        if e.k == "arg":
            argt[e.a[0]] = "char *"
            ptrargs.add(e.a[0])
        elif e.k in ("load", "sym", "arr", "tmp", "var") and e.t == "int":
            e.t = "char *"

    def rd(r):
        if r not in regs:
            raise Bail("reg " + r)
        return regs[r]

    def wr(r, e):
        regs[r] = e
        written.add(r)
        fresh.discard(r)

    def mem(base, off):
        m = re.fullmatch(r"%gp_rel\((D_\w+)\)", off)
        if m and base == "$gp":
            return ("sym", m[1])
        m = re.fullmatch(r"%lo\((D_\w+)\)", off)
        if m:
            if his.get(base) != m[1]:
                raise Bail("lo without hi")
            return ("arr", m[1])
        b = rd(base)
        use_ptr(b)
        return ("field", b, INT(off))

    def call_args(nm):
        ni = max([i for i, r in enumerate(IREGS) if r in written] + [-1])
        nf = max([i for i, r in enumerate(FREGS) if r in written] + [-1])
        regs_ = IREGS[:ni + 1] + FREGS[:nf + 1]
        for r in regs_:
            if r not in written and called[0]:
                raise Bail("passthrough after call")
        args = [rd(r) for r in regs_]
        protos.setdefault(nm, args)
        return args

    def clobber():
        for r in list(regs):  # values kept in saved regs across the call become locals
            if re.match(r"\$(s\d|fp)$", r) and regs[r].k not in ("arg", "const", "tmp", "var", "farg"):
                ntmp[0] += 1
                v, old = E("var", f"v{ntmp[0]}", t=regs[r].t), regs[r]
                stmts.insert(len(stmts) - 1, ("assign", v, old))  # before the call just appended
                for q in list(regs):
                    if regs[q] is old:
                        regs[q] = v
        for r in list(regs):
            if r not in ("$zero",) and not re.match(r"\$(s\d|fp|f2\d|f3\d)$", r):
                del regs[r]
        written.clear()
        fresh.clear()
        called[0] = True

    for l in ins:
        p = l.split(None, 1)
        op, a = p[0], [x.strip() for x in p[1].split(",")] if len(p) > 1 else []
        m = re.fullmatch(r"(.*)\((\$\w+)\)", a[-1]) if a else None
        if m and m[2] == "$sp":
            if (op in LOADS or op in STORES or op in ("sq", "lq")) and SAVED.match(a[0]):
                continue
            raise Bail("stack var")
        if op in ("addiu", "daddiu") and a[0] == "$sp" == a[1]:
            continue
        if op in LOADS and m:
            loc = mem(m[2], m[1])
            t = LOADS[op]
            if loc[0] in ("sym", "arr"):
                e = E(loc[0], loc[1], t=t)
                externs.setdefault(loc[1], e)
            else:
                e = E("load", loc[1], loc[2], t=t)
            wr(a[0], e)
        elif op in STORES and m:
            loc = mem(m[2], m[1])
            v = rd(a[0])
            t = STORES[op]
            if v.k == "farg":
                argt[100 + v.a[0]] = "float"
            if loc[0] in ("sym", "arr"):
                externs.setdefault(loc[1], E(loc[0], loc[1], t=t))
                lhs = (loc[1], None) if loc[0] == "sym" else (loc[1] + "[0]", None)
            else:
                lhs = (t, loc[1], loc[2])
                bf = bfstore(v, loc, t)
                if bf:
                    lo, w, v = bf
                    bft.add((lo, w))
                    lhs = ("bf", loc[1], loc[2], lo, w)
            stmts.append(("store", lhs, v))
        elif op == "lui" and a[1].startswith("%hi("):
            his[a[0]] = re.fullmatch(r"%hi\((D_\w+)\)", a[1])[1]
        elif op == "lui":
            wr(a[0], E("const", INT(a[1]) << 16))
        elif op in ("addiu", "daddiu") and a[2].startswith("%lo("):
            d = a[2][4:-1]
            if his.get(a[1]) != d:
                raise Bail("addiu lo")
            externs.setdefault(d, E("arr", d, t="char"))
            wr(a[0], E("symaddr", d, t="char *"))
        elif op in ("addiu", "daddiu", "ori") and a[1] == "$zero":
            wr(a[0], E("const", INT(a[2])))
        elif op in ("addiu", "daddiu"):
            b = rd(a[1])
            if b.k == "const":
                wr(a[0], E("const", b.a[0] + INT(a[2])))
            else:
                use_ptr(b)
                wr(a[0], E("addr", b, INT(a[2]), t="char *"))
        elif op in ("daddu", "addu", "or") and a[2] == "$zero":
            wr(a[0], rd(a[1]))
        elif op in ("daddu", "addu", "or") and a[1] == "$zero":
            wr(a[0], rd(a[2]))
        elif op == "dsll32":
            regs[a[0]] = E("shl", rd(a[1]), INT(a[2]))
        elif op == "dsrl32":
            s = rd(a[1])
            if s.k != "shl" or s.a[0].k != "load" or s.a[0].t != "unsigned char":
                raise Bail("bitfield")
            n, w = s.a[1], 32 - INT(a[2])
            lo = 32 - n - w
            bft.add((lo, w))
            ld = s.a[0]
            wr(a[0], E("bf", ld.a[0], ld.a[1], lo, w, t="unsigned int"))
        elif op == "sltiu" and INT(a[2]) == 1:
            x = rd(a[1])
            wr(a[0], E("bin", "==", x.a[1], x.a[2]) if x.k == "bin" and x.a[0] == "^" else E("not", x))
        elif op == "sltu" and a[1] == "$zero":
            wr(a[0], E("bin", "!=", rd(a[2]), E("const", 0)))
        elif op in IMM:
            wr(a[0], E("bin", IMM[op], rd(a[1]), E("const", INT(a[2]))))
        elif op in BIN:
            wr(a[0], E("bin", BIN[op], rd(a[1]), rd(a[2]), t="float" if op.endswith(".s") else "int"))
        elif op in ("negu", "dnegu"):
            wr(a[0], E("neg", rd(a[1])))
        elif op == "neg.s":
            wr(a[0], E("neg", rd(a[1]), t="float"))
        elif op == "mov.s":
            wr(a[0], rd(a[1]))
        elif op == "mtc1" and rd(a[0]).k == "const":
            bits = rd(a[0]).a[0] & 0xFFFFFFFF
            wr(a[1], E("fconst", struct.unpack("<f", struct.pack("<I", bits))[0], t="float"))
        elif op == "mtc1" and a[0] == "$zero":
            wr(a[1], E("const", 0, t="float"))
        elif op == "jal":
            args = call_args(a[0])
            ntmp[0] += 1
            tmp = E("tmp", f"t{ntmp[0]}")
            stmts.append(("call", a[0], args, tmp))
            clobber()
            regs["$v0"] = tmp
            ftmp = E("tmp", tmp.a[0], t="float")
            ftmp.parent = tmp
            regs["$f0"] = ftmp
        elif op == "jr" and a[0] == "$ra":
            break
        elif op == "j":
            tail = (a[0], call_args(a[0]))
            break
        elif op == "nop":
            pass
        else:
            raise Bail("op " + op)

    ret = None
    if want_ret and not tail:
        for r in ("$v0", "$f0"):
            if r in regs and (r in written or regs[r].k == "tmp"):
                ret = regs[r]
                break
        if ret is None:
            raise Bail("no return value")
    elif not want_ret and not tail and ("$v0" in written or "$f0" in written):
        raise Bail("dead return value")

    # which args are used, which temps are used
    used, utmp = set(ptrargs), {}

    def walk(e):
        if not isinstance(e, E):
            return
        if e.k == "arg":
            used.add(e.a[0])
        elif e.k == "farg":
            used.add(100 + e.a[0])
        elif e.k == "tmp":
            utmp[e.a[0]] = e
        for x in e.a:
            walk(x)

    for s in stmts:
        if s[0] == "store":
            walk(s[2])
            if len(s[1]) >= 3:
                walk(s[1][1])
        elif s[0] == "assign":
            walk(s[2])
        else:
            for x in s[2]:
                walk(x)
    if tail:
        for x in tail[1]:
            walk(x)
    walk(ret)
    if any(i < 100 for i in used):
        used.update(range(max(i for i in used if i < 100)))
    fl = sorted(i for i in used if i >= 100)
    if fl != list(range(100, 100 + len(fl))):
        raise Bail("float arg gap")

    def ctype(c):
        if c.k == "arg":
            return argt.get(c.a[0], "int")
        if c.k == "farg":
            return "float"
        return "int" if not narrow and c.t in NARROW else c.t

    # a call whose value is returned directly at the end: `return f(...)`
    direct = ret is not None and ret.k == "tmp" and stmts and stmts[-1][0] == "call" \
        and getattr(ret, "parent", ret) is stmts[-1][3]
    rtype = {}
    for t in utmp.values():
        base = getattr(t, "parent", t)
        rtype[base.a[0]] = "float" if t.t == "float" else (t.t if t.t != "int" else rtype.get(base.a[0], "int"))

    body, decls = [], []
    for s in stmts:
        if s[0] == "store":
            lhs = s[1]
            l = lhs[0] if len(lhs) == 2 else val(E("bf", *lhs[1:])) if lhs[0] == "bf" else (
                f"*({lhs[0]} *)({ptr(lhs[1])} + 0x{lhs[2]:X})" if lhs[2] else f"*({lhs[0]} *){ptr(lhs[1])}")
            v = val(s[2])
            if s[2].t == "char *" and lhs[0] != "char *" and len(lhs) == 3:
                v = f"({lhs[0]})({v})"
            body.append(f"{l} = {v};")
        elif s[0] == "assign":
            decls.append(f"{s[1].t} {s[1].a[0]};")
            body.append(f"{s[1].a[0]} = {val(s[2])};")
        else:
            _, f, args, tmp = s
            c = f"{f}({', '.join(val(x) for x in args)})"
            if direct and s is stmts[-1]:
                body.append(f"return {c};")
            elif tmp.a[0] in rtype:
                decls.append(f"{rtype[tmp.a[0]]} {tmp.a[0]};")
                body.append(f"{tmp.a[0]} = {c};")
            else:
                body.append(c + ";")
    if tail:
        body.append(f"{tail[0]}({', '.join(val(x) for x in tail[1])});")
    if ret is not None and not direct:
        if stmts and ret.k in ("sym", "arr", "load"):
            decls.append(f"{ret.t} ret;")
            body.insert(0, f"ret = {val(ret)};")
            body.append("return ret;")
        else:
            body.append(f"return {val(ret)};")

    params = [f"{argt.get(i, 'int')} a{i}" for i in sorted(i for i in used if i < 100)]
    params += [f"float f{i - 100}" for i in fl]
    rt = "void" if ret is None else (rtype.get(ret.a[0], "int") if ret.k == "tmp" else ret.t)
    hdr = []
    for lo, w in sorted(bft):
        hdr.append(f"typedef struct {{ {f'unsigned char p : {lo}; ' if lo else ''}unsigned char v : {w}; }} BF_{lo}_{w};")
    for d, x in sorted(externs.items()):
        hdr.append(f"extern {x.t} {d}{'[]' if x.k == 'arr' else ''};")
    for f, args in protos.items():
        r = rtype.get(next((s[3].a[0] for s in stmts if s[0] == "call" and s[1] == f and s[3].a[0] in rtype), ""), "void")
        if direct and stmts[-1][1] == f:
            r = rt
        hdr.append(f"{r} {f}({', '.join(ctype(c) for c in args) or 'void'});")
    return "\n".join(hdr) + f"\n\n{rt} {name}({', '.join(params) or 'void'}) {{\n" + \
        "".join(f"    {d}\n" for d in decls) + "".join(f"    {b}\n" for b in body) + "}\n"


def bfstore(v, loc, t):
    """(load & ~(m << s)) | ((x & m) << s) stored back to the same byte -> bitfield (s, width, x)"""
    if t != "unsigned char" or v.k != "bin" or v.a[0] != "|":
        return None
    for keep, new in ((v.a[1], v.a[2]), (v.a[2], v.a[1])):
        if keep.k == "bin" and keep.a[0] == "&" and keep.a[1].k == "load" and keep.a[1].a[0] is loc[1] \
                and keep.a[1].a[1] == loc[2] and keep.a[2].k == "const":
            s = 0
            if new.k == "bin" and new.a[0] == "<<" and new.a[2].k == "const":
                s, new = new.a[2].a[0], new.a[1]
            if new.k == "bin" and new.a[0] == "&" and new.a[2].k == "const":
                m = new.a[2].a[0]
                w = bin(m).count("1")
                if m == (1 << w) - 1 and (keep.a[2].a[0] & 0xFF) == (~(m << s) & 0xFF):
                    return s, w, new.a[1]
    return None


def ptr(e):
    if e.k == "arg":
        return f"a{e.a[0]}"
    if e.k == "load":
        return f"*(char **){addr_of(e)}" if e.t == "char *" else f"(char *)*({e.t} *){addr_of(e)}"
    if e.k in ("sym", "tmp", "var"):
        return e.a[0] if e.t == "char *" else f"(char *){e.a[0]}"
    if e.k == "arr":
        return f"*(char **){e.a[0]}"
    if e.k in ("addr", "symaddr"):
        return f"({val(e)})"
    raise Bail("ptr " + e.k)


def addr_of(e):
    b, off = e.a
    return f"({ptr(b)} + 0x{off:X})" if off else ptr(b)


def val(e):
    k = e.k
    if k == "arg":
        return f"a{e.a[0]}"
    if k == "farg":
        return f"f{e.a[0]}"
    if k == "const":
        v = e.a[0]
        return ("0.0f" if e.t == "float" else str(v) if -10 < v < 10 else hex(v))
    if k == "fconst":
        s = repr(e.a[0])
        return (s if "e" in s or "." in s else s + ".0") + "f"
    if k in ("sym", "symaddr", "tmp", "var"):
        return e.a[0]
    if k == "arr":
        return f"{e.a[0]}[0]"
    if k == "load":
        return f"*({e.t} *){addr_of(e)}" if e.t != "char *" else f"*(char **){addr_of(e)}"
    if k == "addr":
        return f"{ptr(e.a[0])} + 0x{e.a[1]:X}"
    if k == "bf":
        b, off, lo, w = e.a
        return f"((BF_{lo}_{w} *)({ptr(b)} + 0x{off:X}))->v" if off else f"((BF_{lo}_{w} *){ptr(b)})->v"
    if k == "bin":
        return f"({val(e.a[1])} {e.a[0]} {val(e.a[2])})"
    if k == "not":
        return f"!{val(e.a[0])}"
    if k == "neg":
        return f"-{val(e.a[0])}"
    raise Bail("val " + k)


src = sys.stdin.read()
out = []
for m in re.finditer(r"^== (func_\w+) \S+\n(.*?)(?=^== |\Z)", src, re.S | re.M):
    body = m[2].splitlines()
    if any(l.split()[0].startswith("b") or l.split()[0] == "jalr" for l in body if l.strip()):
        sys.stderr.write(f"skip {m[1]}: branch\n")
        continue
    vs, err = [], None
    for n, r in itertools.product((True, False), (True, False)):
        try:
            v = gen(m[1], body, n, r)
            if v not in vs:
                vs.append(v)
        except (Bail, KeyError, IndexError, ValueError, TypeError) as ex:
            err = ex
    if vs:
        out += vs
    else:
        sys.stderr.write(f"skip {m[1]}: {err}\n")
print("\n// ---\n".join(out))
