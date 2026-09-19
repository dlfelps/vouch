"""``@vouch.track``: record what an experiment function returns, keyed by its arguments.

::

    @vouch.track(over="seed")
    def evaluate(dataset, model, seed=0, lr=1e-3):
        ...
        return {"acc": acc, "loss": loss}

    evaluate("cifar", "resnet", seed=3)
    # -> evaluate.cifar.resnet.lr_0_001.acc / .loss, a Stat over every seed called

The key is the function's name followed by **every argument**, defaults included, in
signature order. A string argument is written as its value (``cifar``); a number,
bool or None as ``name_value`` (``lr_0_001``, ``seed_3``), because a bare number says
nothing about what it is. Arguments that can't be written into a key -- arrays,
models, ``self`` -- are left out of it and described in the call metadata instead.
``key="{dataset}.{model}"`` overrides the scheme.

With ``over=``, calls that differ only in those arguments are combined when the run
ends: numbers become a ``Stat`` (mean, std, n). Without it, every call is its own key.

Every value records the call that produced it -- the function, its arguments, and
where it was called -- so ``vouch trace`` and the PDF appendix can say
``evaluate(dataset=cifar, model=resnet, lr=0.001) over seed=0..4``.
"""

from __future__ import annotations

import enum
import functools
import hashlib
import inspect
import os
from typing import Any, Callable

from .values import (Stat, as_mapping, coerce_scalar, encode_param, flatten, is_number,
                     sanitize_key, slug_segment)

KEY_BUDGET = 100          # leave room under the 128-char key limit for output field names


class TrackError(TypeError):
    pass


# ---------------------------------------------------------------------------
# arguments -> key
# ---------------------------------------------------------------------------

def _scalar_text(v: Any) -> str | None:
    """How a simple value is written into a key segment; None if it isn't simple."""
    v = coerce_scalar(v)
    if isinstance(v, enum.Enum):
        return slug_segment(v.name)
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return slug_segment(repr(v))
    if isinstance(v, str):
        return slug_segment(v) if v else "empty"
    if isinstance(v, os.PathLike):
        return slug_segment(os.fspath(v))
    return None


def segment(name: str, value: Any) -> str | None:
    """One key segment for one argument, or None if it can't go in a key."""
    value = coerce_scalar(value)
    if isinstance(value, (str, enum.Enum, os.PathLike)) and not isinstance(value, bool):
        return _scalar_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return f"{name}_{_scalar_text(value)}"
    if isinstance(value, (list, tuple)) and 0 < len(value) <= 6:
        parts = [_scalar_text(x) for x in value]
        if all(p is not None for p in parts):
            return f"{name}_" + "-".join(parts)
    return None


def describe(value: Any) -> Any:
    """An argument as the record shows it: the value if simple, else a short description."""
    value = coerce_scalar(value)
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return encode_param(value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, (list, tuple)) and len(value) <= 6 and \
            all(_scalar_text(x) is not None for x in value):
        return [describe(x) for x in value]
    shape = getattr(value, "shape", None)
    if shape is not None:
        try:
            return f"<{type(value).__name__} shape {tuple(shape)}>"
        except TypeError:
            pass
    try:
        n = len(value)
        return f"<{type(value).__name__} of {n}>"
    except TypeError:
        return f"<{type(value).__name__}>"


def function_name(fn: Callable) -> str:
    qual = getattr(fn, "__qualname__", fn.__name__)
    kept = [seg for seg in qual.split(".") if not seg.startswith("<")]
    return sanitize_key(".".join(kept) or fn.__name__)


def function_name_of(qualname: str) -> str:
    kept = [seg for seg in qualname.split(".") if not seg.startswith("<")]
    return sanitize_key(".".join(kept) or qualname)


def _shorten(key: str) -> str:
    if len(key) <= KEY_BUDGET:
        return key
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return key[:KEY_BUDGET - 9].rstrip("._-") + "_" + digest


def call_key(name: str, arguments: dict[str, Any], template: str | None) -> tuple[str, list[str]]:
    """(base key, argument names that could not be written into it)."""
    if template:
        fields = {k: (_scalar_text(v) or slug_segment(type(v).__name__)) for k, v in arguments.items()}
        key = template.format(**fields)          # field names were checked at decoration
        return _shorten(sanitize_key(key)), []
    parts, left_out = [name], []
    for arg, value in arguments.items():
        seg = segment(arg, value)
        if seg is None:
            left_out.append(arg)
        else:
            parts.append(seg)
    return _shorten(sanitize_key(".".join(parts))), left_out


# ---------------------------------------------------------------------------
# results -> values
# ---------------------------------------------------------------------------

def _flat_result(result: Any) -> dict[str, Any] | None:
    """{relative key: value}; "" is the base key itself (a scalar result)."""
    result = coerce_scalar(result)
    if isinstance(result, (Stat, bool, int, float, str, tuple)):
        return {"": result}
    from .api import _as_rows
    rows = _as_rows(result)
    if rows is not None:
        return {f"{i}.{slug_segment(col)}": v for i, row in enumerate(rows) for col, v in row.items()}
    mapping = as_mapping(result)
    if mapping is not None and not isinstance(result, Stat):
        return flatten(mapping)
    if isinstance(result, list) and all(is_number(x) for x in result):
        return {"": tuple(result)}
    return None


class _Combined:
    """Calls that differ only in their ``over=`` arguments, combined at run end."""

    def __init__(self, base: str, fn_ref: str, arguments: dict, over: tuple[str, ...],
                 meta: dict, site: str):
        self.base, self.fn_ref, self.arguments, self.over = base, fn_ref, arguments, over
        self.meta, self.site = meta, site
        self.over_values: list[dict] = []
        self.results: list[dict[str, Any]] = []
        self.call_sites: list[str] = []
        self.not_in_key: list[str] = []


def flush(run) -> None:
    """Turn a run's combined calls into values (numbers -> Stat). Called at finalize."""
    from .api import _Notes
    for base, comb in sorted(run._tracked.items()):
        notes = _Notes()
        fields: dict[str, list[Any]] = {}
        for res in comb.results:
            for rel, v in res.items():
                fields.setdefault(rel, []).append(coerce_scalar(v))
        flat: dict[str, Any] = {}
        for rel, vals in fields.items():
            if len(vals) != len(comb.results):
                notes.skipped.append((f"{base}.{rel}" if rel else base,
                                      f"returned by {len(vals)} of {len(comb.results)} calls"))
            elif all(is_number(v) and not isinstance(v, Stat) for v in vals):
                flat[rel] = Stat.of(vals)
            elif all(v == vals[0] for v in vals):
                flat[rel] = vals[0]
            else:
                notes.skipped.append((f"{base}.{rel}" if rel else base, "differs across calls "
                                      "and is not a number"))
        over_seen = {name: [ov[name] for ov in comb.over_values] for name in comb.over}
        call = {"function": comb.fn_ref, "args": comb.arguments,
                "over": {k: [describe(v) for v in vs] for k, vs in over_seen.items()},
                "calls": len(comb.results),
                "sites": sorted(set(comb.call_sites))}
        if comb.not_in_key:
            call["not_in_key"] = comb.not_in_key
        recorded = run._record_flat(base, flat, site=comb.site, notes=notes, extra={"call": call},
                                    **comb.meta)
        run._report_bulk(notes, recorded, who=f"@vouch.track {comb.fn_ref}")
    run._tracked.clear()


# ---------------------------------------------------------------------------
# one tracked function: shared by the decorator and by [[track]] in vouch.toml
# ---------------------------------------------------------------------------

DECORATED: set = set()      # code objects wrapped by @vouch.track (config tracking skips them)


def check_names(names: set[str], params: list[str], what: str, fname: str) -> None:
    bad = sorted(names - set(params))
    if bad:
        raise TrackError(f"{what} names {bad}, which {fname}() doesn't take")


def template_fields(key: str | None) -> set[str]:
    if not key:
        return set()
    import string
    return {f for _, f, _, _ in string.Formatter().parse(key) if f}


class Tracked:
    """A function whose results are recorded: how to key a call, and how to record it."""

    def __init__(self, *, name: str, qualname: str, code, over: tuple[str, ...] = (),
                 key: str | None = None, meta: dict | None = None, via: str = "@vouch.track"):
        self.fname, self.qualname, self.code = name, qualname, code
        self.over, self.key, self.via = over, key, via
        self.meta = {k: (meta or {}).get(k) for k in ("fmt", "desc", "unit", "better",
                                                      "include", "exclude")}
        self.warned_none = self.warned_desc = False

    def record(self, arguments: dict[str, Any], result: Any, call_site: str) -> None:
        """Record one call. ``arguments``: signature order, defaults applied, no self."""
        from .api import _Notes, _project, _warn, active_run
        who = f"{self.via} {self.fname}()"
        if result is None:
            if not self.warned_none:
                _warn(f"{who} returned None; nothing recorded")
                self.warned_none = True
            return
        arguments = dict(arguments)
        over_values = {o: arguments.pop(o) for o in self.over if o in arguments}
        base, left_out = call_key(self.fname, arguments, self.key)
        cfg = _project().config
        fn_ref = f"{cfg.rel(self.code.co_filename)}::{self.qualname}"
        site = f"{cfg.rel(self.code.co_filename)}:{self.code.co_firstlineno}"
        described = {k: describe(v) for k, v in arguments.items()}
        flat = _flat_result(result)
        if flat is None:
            _warn(f"{who} returned a {type(result).__name__}; return a number, a dict of "
                  f"numbers, a Stat or a DataFrame")
            return
        run = active_run()
        if self.over:
            comb = run._tracked.get(base)
            if comb is None:
                comb = run._tracked[base] = _Combined(base, fn_ref, described, self.over,
                                                      self.meta, site)
                comb.not_in_key = left_out
            if over_values in comb.over_values:
                _warn(f"{self.fname}() called twice with "
                      f"{', '.join(f'{k}={v!r}' for k, v in over_values.items())}; both are "
                      f"included in {base}")
            comb.over_values.append(over_values)
            comb.results.append(flat)
            comb.call_sites.append(call_site)
            return
        notes = _Notes()
        call = {"function": fn_ref, "args": described, "sites": [call_site]}
        if left_out:
            call["not_in_key"] = left_out
        recorded = run._record_flat(base, flat, site=site, notes=notes, extra={"call": call},
                                    **self.meta)
        if self.warned_desc:
            notes.no_desc.clear()
        elif notes.no_desc:
            self.warned_desc = True
        run._report_bulk(notes, recorded, who=who)

    def record_safely(self, arguments: dict[str, Any], result: Any, call_site: str) -> None:
        """Bookkeeping must never break the experiment: report, don't raise."""
        try:
            self.record(arguments, result, call_site)
        except Exception as exc:  # pragma: no cover - defensive
            from .api import _warn
            _warn(f"{self.via} could not record {self.fname}(): {exc!r}")
            if os.environ.get("VOUCH_STRICT") == "1":
                raise


# ---------------------------------------------------------------------------
# arguments, from a bound call or from a live frame
# ---------------------------------------------------------------------------

def arguments_from_bound(sig: inspect.Signature, bound: inspect.BoundArguments,
                         skip_first: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i, (arg, value) in enumerate(bound.arguments.items()):
        if i == 0 and skip_first:
            continue
        kind = sig.parameters[arg].kind
        if kind is inspect.Parameter.VAR_KEYWORD:
            out.update(sorted(value.items()))
        elif kind is inspect.Parameter.VAR_POSITIONAL:
            if value:
                out[arg] = tuple(value)
        else:
            out[arg] = value
    return out


CO_VARARGS, CO_VARKEYWORDS = 0x04, 0x08


def parameter_names(code) -> tuple[list[str], str | None, str | None, list[str]]:
    """(positional, *args name, **kwargs name, keyword-only) from a code object.

    A code object stores them as positional, keyword-only, *args, **kwargs; a
    signature reads positional, *args, keyword-only, **kwargs.
    """
    names = code.co_varnames
    npos, nkw = code.co_argcount, code.co_kwonlyargcount
    pos, kwonly = list(names[:npos]), list(names[npos:npos + nkw])
    i = npos + nkw
    varargs = varkw = None
    if code.co_flags & CO_VARARGS:
        varargs, i = names[i], i + 1
    if code.co_flags & CO_VARKEYWORDS:
        varkw = names[i]
    return pos, varargs, varkw, kwonly


def signature_order(code) -> list[str]:
    pos, varargs, varkw, kwonly = parameter_names(code)
    return pos + ([varargs] if varargs else []) + kwonly + ([varkw] if varkw else [])


def arguments_from_frame(code, f_locals: dict) -> dict[str, Any]:
    """The arguments of a call that is just starting, read from its frame."""
    pos, varargs, varkw, kwonly = parameter_names(code)
    out: dict[str, Any] = {}
    for i, name in enumerate(pos):
        if i == 0 and name in ("self", "cls"):
            continue
        out[name] = f_locals.get(name)
    if varargs and f_locals.get(varargs):
        out[varargs] = tuple(f_locals[varargs])
    for name in kwonly:
        out[name] = f_locals.get(name)
    if varkw:
        out.update(sorted((f_locals.get(varkw) or {}).items()))
    return out


# ---------------------------------------------------------------------------
# the decorator
# ---------------------------------------------------------------------------

def track(fn: Callable | None = None, *, key: str | None = None, name: str | None = None,
          over: str | tuple[str, ...] = (), fmt: Any = None, desc: Any = None, unit: Any = None,
          better: Any = None, include: Any = None, exclude: Any = None):
    """Record a function's return value on every call, keyed by its arguments.

    ``over=`` names arguments (e.g. ``"seed"``) whose calls are combined into a Stat;
    ``key=`` is a template over argument names; ``fmt``/``desc``/``unit``/``better``/
    ``include``/``exclude`` work as in ``record_all``.
    """
    if fn is None:
        return lambda f: track(f, key=key, name=name, over=over, fmt=fmt, desc=desc, unit=unit,
                               better=better, include=include, exclude=exclude)
    over_names = (over,) if isinstance(over, str) else tuple(over)
    sig = inspect.signature(fn)
    check_names(set(over_names), list(sig.parameters), "@vouch.track(over=...)", fn.__name__)
    check_names(template_fields(key), list(sig.parameters), f"@vouch.track(key={key!r})",
                fn.__name__)
    params = list(sig.parameters.values())
    skip_first = bool(params) and params[0].name in ("self", "cls")
    tracked = Tracked(name=sanitize_key(name) if name else function_name(fn),
                      qualname=getattr(fn, "__qualname__", fn.__name__), code=fn.__code__,
                      over=over_names, key=key,
                      meta={"fmt": fmt, "desc": desc, "unit": unit, "better": better,
                            "include": include, "exclude": exclude})
    DECORATED.add(fn.__code__)

    def arguments(args, kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return arguments_from_bound(sig, bound, skip_first)

    def caller_site() -> str:
        from .api import _site
        return _site()

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            site = caller_site()
            result = await fn(*args, **kwargs)
            tracked.record_safely(arguments(args, kwargs), result, site)
            return result
        async_wrapper.__vouch_track__ = True
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        site = caller_site()
        result = fn(*args, **kwargs)
        tracked.record_safely(arguments(args, kwargs), result, site)
        return result
    wrapper.__vouch_track__ = True
    return wrapper


def _span(values: list) -> str:
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        s = sorted(set(values))
        if len(s) > 2 and s == list(range(s[0], s[-1] + 1)):
            return f"{s[0]}..{s[-1]}"
    shown = ", ".join(str(v) for v in values[:6])
    return shown + (", ..." if len(values) > 6 else "")


def call_text(call: dict) -> str:
    """``evaluate(dataset=cifar, model=resnet, lr=0.001) over seed=0..4``."""
    fn = str(call.get("function", "?")).split("::")[-1]
    args = ", ".join(f"{k}={v}" for k, v in (call.get("args") or {}).items())
    text = f"{fn}({args})"
    for k, vs in (call.get("over") or {}).items():
        text += f" over {k}={_span(list(vs))}"
    return text
