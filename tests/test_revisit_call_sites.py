"""Every self.<method>() call in revisit.py must match the method's signature.

The bug this exists for: _turn() gained a `now` argument when it was rerouted
through the motion layer (c9593a8), and two of its five call sites — the entity
and legacy target-turn branches — were left calling it with two arguments. Both
branches were untested, so the mismatch sat in the tree until the first time
curiosity re-acquired a target after tracking was lost, and then it ended the
process (2026-09-25 07:35:49, frames=3104).

A behavioural test only covers the branches somebody remembered to write. This
one covers the whole file: it parses the source, finds every `self.<name>(...)`
call whose name is a method of RevisitController, and checks the call against
the real signature from the imported class, so a renamed or added argument
cannot silently outrun its call sites again.

Calls that use *args / **kwargs, or that resolve to an attribute rather than a
method (self._motion.track, self._servo_ptz.pan_to), are skipped — this checks
arity, not behaviour.
"""
import ast
import inspect
import pathlib
import sys

sys.path.insert(0, '.')

from runtime.interest.revisit import RevisitController

SOURCE = pathlib.Path("runtime/interest/revisit.py")


def calls_with_mismatched_arity():
    """[(line, method, given, signature)] for every self.<method>() mismatch."""
    cls = next(n for n in ast.parse(SOURCE.read_text(encoding="utf-8")).body
               if isinstance(n, ast.ClassDef))
    bad = []
    for node in ast.walk(cls):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "self"):
            continue
        method = getattr(RevisitController, f.attr, None)
        if not callable(method):
            continue
        if any(isinstance(a, ast.Starred) for a in node.args):
            continue
        if any(k.arg is None for k in node.keywords):
            continue
        params = list(inspect.signature(method).parameters.values())
        if params and params[0].name == "self":
            params = params[1:]        # @staticmethod has no self
        if any(p.kind == p.VAR_POSITIONAL for p in params):
            continue
        positional = [p for p in params
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        keywords = {k.arg for k in node.keywords if k.arg}
        given = len(node.args)

        missing = [p.name for i, p in enumerate(positional)
                   if p.default is p.empty and i >= given and p.name not in keywords]
        if missing or given > len(positional):
            bad.append((node.lineno, f.attr, given,
                        str(inspect.signature(method))))
    return bad


def test_every_self_method_call_matches_its_signature():
    bad = calls_with_mismatched_arity()
    assert not bad, "arity mismatches in revisit.py:\n" + "\n".join(
        f"  line {ln}: self.{name}(...) with {n} positional args, but {sig}"
        for ln, name, n, sig in bad)
