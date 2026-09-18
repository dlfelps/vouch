import textwrap

from vouch.units import MODULE, unit_of_qualname, units_from_source


def units(src: str) -> dict:
    out = units_from_source(textwrap.dedent(src))
    assert out is not None
    return out


BASE = '''
    """Module docstring."""
    import math

    SCALE = 2.0

    def score(x):
        """Score it."""
        return SCALE * x  # doubled

    def unused(y=1):
        return y + 1

    class Model:
        """A model."""
        depth = 3

        def forward(self, x):
            return x * self.depth

        def other(self):
            return 0
'''


def test_units_found():
    u = units(BASE)
    assert set(u) == {MODULE, "score", "unused", "Model", "Model.forward", "Model.other"}
    assert all(len(h) == 16 for h in u.values())


def test_docstrings_comments_blank_lines_are_cosmetic():
    edited = BASE.replace('"""Score it."""', '"""Score it, carefully."""') \
                 .replace("# doubled", "# times two") \
                 .replace('"""A model."""', "") \
                 .replace("    SCALE = 2.0\n", "\n    SCALE = 2.0   # constant\n\n")
    assert units(edited) == units(BASE)


def test_logic_edit_changes_only_that_unit():
    a, b = units(BASE), units(BASE.replace("return SCALE * x", "return SCALE * x + 1"))
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"score"}


def test_method_edit_changes_only_the_method():
    a, b = units(BASE), units(BASE.replace("return x * self.depth", "return x * self.depth ** 2"))
    assert {k for k in a if a[k] != b[k]} == {"Model.forward"}


def test_class_attribute_changes_class_unit_only():
    a, b = units(BASE), units(BASE.replace("depth = 3", "depth = 4"))
    assert {k for k in a if a[k] != b[k]} == {"Model"}


def test_module_constant_changes_module_unit_only():
    a, b = units(BASE), units(BASE.replace("SCALE = 2.0", "SCALE = 3.0"))
    assert {k for k in a if a[k] != b[k]} == {MODULE}


def test_default_argument_is_part_of_the_function():
    a, b = units(BASE), units(BASE.replace("def unused(y=1)", "def unused(y=2)"))
    assert {k for k in a if a[k] != b[k]} == {"unused"}


def test_reordering_functions_changes_nothing():
    reordered = '''
        """Module docstring."""
        import math

        SCALE = 2.0

        def unused(y=1):
            return y + 1

        class Model:
            depth = 3

            def other(self):
                return 0

            def forward(self, x):
                return x * self.depth

        def score(x):
            return SCALE * x
    '''
    assert units(reordered) == units(BASE)


def test_nested_function_belongs_to_its_parent():
    a = units("""
        def outer():
            def inner():
                return 1
            return inner()
    """)
    b = units("""
        def outer():
            def inner():
                return 2
            return inner()
    """)
    assert set(a) == {MODULE, "outer"}
    assert a["outer"] != b["outer"] and a[MODULE] == b[MODULE]


def test_conditional_definitions_are_units_and_duplicates_hash_together():
    src = """
        import sys
        if sys.version_info >= (3, 12):
            def f():
                return 1
        else:
            def f():
                return 2
    """
    u = units(src)
    assert set(u) == {MODULE, "f"}
    assert units(src.replace("return 2", "return 3"))["f"] != u["f"]


def test_syntax_error_returns_none():
    assert units_from_source("def broken(:\n") is None


def test_hash_is_stable_golden():
    # Guards the canonical serializer: if this changes, every recorded run in every
    # project goes stale at once. Bump HASH_VERSION deliberately instead.
    u = units_from_source("def f(x):\n    return x + 1\n")
    assert u["f"] == "92934cbc3ba238b7"


def test_unit_of_qualname():
    assert unit_of_qualname("f") == "f"
    assert unit_of_qualname("Model.forward") == "Model.forward"
    assert unit_of_qualname("f.<locals>.g") == "f"
    assert unit_of_qualname("Model.fit.<locals>.<lambda>") == "Model.fit"
    assert unit_of_qualname("<lambda>") == MODULE
    assert unit_of_qualname("<genexpr>") == MODULE
