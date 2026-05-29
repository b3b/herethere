import pytest

from herethere.there.ai.postprocess import postprocess_code


@pytest.mark.parametrize(
    "raw, expected",
    (
        ("print(1)", "print(1)"),
        ("  print(1)\n", "print(1)"),
        ("```python\nprint(1)\n```", "print(1)"),
        ("```py\nprint(1)\n```", "print(1)"),
        ("```\nprint(1)\n```", "print(1)"),
        ("%%there\nprint(1)", "print(1)"),
        ("%%there shell\nprint(1)", "print(1)"),
        ("```python\n%%there\nprint(1)\n```", "print(1)"),
    ),
)
def test_postprocess_code(raw, expected):
    assert postprocess_code(raw) == expected
