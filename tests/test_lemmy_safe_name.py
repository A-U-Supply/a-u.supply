"""Lemmy 0.19 community names must match ^[a-zA-Z0-9_]{3,20}$; anything
outside that range comes back as 400 invalid_name. Lock the helper in."""

import re

from server.lemmy_client import _lemmy_safe_name

LEMMY_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")


def _ok(name: str) -> bool:
    return bool(LEMMY_RE.match(name))


def test_short_slug_passes_through():
    assert _lemmy_safe_name("test") == "test"


def test_hyphens_become_underscores():
    assert _lemmy_safe_name("my-cool-project") == "my_cool_project"


def test_oversize_slug_truncates_to_20():
    out = _lemmy_safe_name("my-new-latent-project-with-extras")
    assert len(out) <= 20
    assert _ok(out)


def test_truncation_strips_trailing_underscore():
    # 21-char slug whose 20th char is `_` — must not end with `_`.
    out = _lemmy_safe_name("a-b-c-d-e-f-g-h-i-j-k-l-m")
    assert not out.endswith("_")
    assert _ok(out)


def test_undersize_slug_is_padded():
    out = _lemmy_safe_name("ab")
    assert len(out) >= 3
    assert out.startswith("ab")
    assert _ok(out)


def test_single_char_slug_is_padded():
    assert _ok(_lemmy_safe_name("a"))


def test_empty_slug_falls_back_to_latent():
    assert _lemmy_safe_name("") == "latent"
    assert _lemmy_safe_name(None) == "latent"  # type: ignore[arg-type]


def test_only_punctuation_falls_back():
    assert _lemmy_safe_name("---") == "latent"
    assert _lemmy_safe_name("___") == "latent"


def test_uppercase_lowercased():
    assert _lemmy_safe_name("MyProject") == "myproject"


def test_unicode_stripped():
    out = _lemmy_safe_name("café-noir")
    assert _ok(out)
