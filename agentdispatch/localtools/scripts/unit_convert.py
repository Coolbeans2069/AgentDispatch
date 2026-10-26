"""unit conversion. Prefers astropy.units, then pint (with dask stubbed if broken), then a small built-in table."""
import json, re, sys


_BUILTIN = {
    ("m", "inch"): 39.3700787402, ("inch", "m"): 0.0254, ("m", "ft"): 3.280839895, ("ft", "m"): 0.3048,
    ("km", "mile"): 0.621371192, ("mile", "km"): 1.609344, ("kg", "lb"): 2.20462262, ("lb", "kg"): 0.45359237,
    ("g", "oz"): 0.0352739619, ("oz", "g"): 28.3495231, ("l", "gal"): 0.264172052, ("gal", "l"): 3.78541178,
    ("m/s", "km/h"): 3.6, ("km/h", "m/s"): 1 / 3.6, ("degc", "degf"): None, ("degf", "degc"): None,
}


def _norm(u):
    u = u.strip().lower().replace("°", "deg").replace("celsius", "degc").replace("fahrenheit", "degf")
    u = u.replace("metres", "m").replace("meters", "m").replace("kilometres", "km").replace("kilometers", "km")
    u = u.replace("miles", "mile").replace("inches", "inch").replace("feet", "ft").replace("pounds", "lb")
    return u.replace(" ", "")


def _builtin(val, src, dst):
    s, d = _norm(src), _norm(dst)
    if s in ("degc", "c") and d in ("degf", "f"):
        return val * 9 / 5 + 32, "degF"
    if s in ("degf", "f") and d in ("degc", "c"):
        return (val - 32) * 5 / 9, "degC"
    key = (s, d)
    if key in _BUILTIN and _BUILTIN[key] is not None:
        return val * _BUILTIN[key], d
    return None


def main(argv):
    q = " ".join(argv).strip()
    if not q:
        print(json.dumps(dict(ok=False, error="give e.g. '2.5 mile to km' or '100 degC to degF'")))
        return 2
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(.+?)\s+to\s+(.+?)\s*$", q, re.I)
    if not m:
        print(json.dumps(dict(ok=False, error="format: '<number> <from_unit> to <to_unit>'")))
        return 4
    val, src, dst = float(m.group(1)), m.group(2).strip(), m.group(3).strip()

    # 1) astropy
    try:
        import astropy.units as u
        qsrc = u.Unit(src)
        qdst = u.Unit(dst)
        out = (val * qsrc).to(qdst)
        print(json.dumps(dict(ok=True, engine="astropy", input=q, magnitude=float(out.value), unit=str(out.unit), text=str(out)), ensure_ascii=False))
        return 0
    except Exception as e_astro:
        astro_err = str(e_astro)[:120]
    else:
        astro_err = ""

    # 2) pint with dask stubbed (broken pandas/numpy stacks otherwise explode on import)
    try:
        if "dask" not in sys.modules:
            stub = type(sys)("dask")
            stub.array = type(sys)("dask.array")
            sys.modules["dask"] = stub
            sys.modules["dask.array"] = stub.array
        import pint
        ureg = pint.UnitRegistry()
        src_q = float(val) * ureg.parse_expression(src)
        out = src_q.to(ureg.parse_expression(dst))
        print(json.dumps(dict(ok=True, engine="pint", input=q, magnitude=float(out.magnitude), unit=str(out.units), text=str(out)), ensure_ascii=False))
        return 0
    except Exception as e_pint:
        pint_err = str(e_pint)[:120]

    # 3) tiny built-in table
    hit = _builtin(val, src, dst)
    if hit:
        mag, unit = hit
        print(json.dumps(dict(ok=True, engine="builtin", input=q, magnitude=mag, unit=unit, text="%s %s" % (mag, unit)), ensure_ascii=False))
        return 0

    print(json.dumps(dict(ok=False, error="conversion failed", astropy=astro_err, pint=pint_err), ensure_ascii=False))
    return 5


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
