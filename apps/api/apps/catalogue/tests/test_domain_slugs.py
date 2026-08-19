"""URL slugs — SRS §7.5.6, §4.2, §4.3.

The Zanzibar names are the easy half. The other half is the §4.2 discipline: a
slugifier that works for "Nungwi" and returns "" for a name in another script
is a Zanzibar-shaped assumption that would surface as a NOT NULL violation in
production, in a market nobody was testing.
"""

from __future__ import annotations

import pytest

from apps.catalogue.domain.slugs import (
    MAX_SLUG_LENGTH,
    SlugError,
    slugify_name,
    unique_slug,
)


class TestTheSeedCatalogue:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Stone Town", "stone-town"),
            ("Nungwi", "nungwi"),
            ("Kendwa", "kendwa"),
            ("Paje", "paje"),
            ("Jambiani", "jambiani"),
            ("Matemwe", "matemwe"),
            ("Kiwengwa", "kiwengwa"),
            ("Michamvi", "michamvi"),
            ("Pemba (Chake Chake)", "pemba-chake-chake"),
            ("Abeid Amani Karume Intl. Airport (ZNZ)", "abeid-amani-karume-intl-airport-znz"),
            ("Jozani Forest", "jozani-forest"),
            ("Zanzibar Urban/West", "zanzibar-urban-west"),
        ],
    )
    def test_seed_names(self, name: str, expected: str) -> None:
        assert slugify_name(name) == expected

    def test_arusha_works_without_a_code_change(self) -> None:
        # §41.12's acceptance test creates Arusha through the console.
        assert slugify_name("Arusha") == "arusha"
        assert slugify_name("Arusha National Park") == "arusha-national-park"


class TestNormalisation:
    def test_case_is_lowered(self) -> None:
        assert slugify_name("STONE TOWN") == "stone-town"

    def test_runs_of_whitespace_collapse(self) -> None:
        assert slugify_name("Stone    Town") == "stone-town"

    def test_leading_and_trailing_whitespace_is_dropped(self) -> None:
        assert slugify_name("  Nungwi  ") == "nungwi"

    def test_underscores_become_hyphens(self) -> None:
        assert slugify_name("stone_town") == "stone-town"

    def test_punctuation_is_removed(self) -> None:
        assert slugify_name("Freddie Mercury's House") == "freddie-mercurys-house"

    def test_ampersands_are_removed_not_transliterated(self) -> None:
        # "spice-farm-stone-town" rather than "spice-farm-and-stone-town":
        # inventing the word "and" would be wrong in a Kiswahili name.
        assert slugify_name("Spice Farm & Stone Town") == "spice-farm-stone-town"

    def test_repeated_separators_collapse_to_one(self) -> None:
        assert slugify_name("Paje -- Jambiani") == "paje-jambiani"

    def test_digits_survive(self) -> None:
        assert slugify_name("Dhow Cruise 2") == "dhow-cruise-2"

    def test_an_em_dash_is_a_separator(self) -> None:
        assert slugify_name("Nungwi — Kendwa") == "nungwi-kendwa"


class TestExpansionMarkets:
    """§4.3: East Africa, then further international markets."""

    def test_diacritics_fold_to_ascii(self) -> None:
        assert slugify_name("Zanzíbar") == "zanzibar"
        assert slugify_name("Réunion") == "reunion"

    def test_german_sharp_s_expands(self) -> None:
        assert slugify_name("Straße") == "strasse"

    def test_scandinavian_letters_fold(self) -> None:
        assert slugify_name("Ø Island") == "o-island"
        assert slugify_name("Ærø") == "aero"

    def test_a_non_latin_name_produces_a_stable_slug_not_an_empty_one(self) -> None:
        # Django's slugify returns "" here, which violates the NOT NULL UNIQUE
        # column the moment two such names exist.
        got = slugify_name("中国")
        assert got == "u4e2d-u56fd"
        assert got != ""

    def test_two_different_non_latin_names_do_not_collide(self) -> None:
        assert slugify_name("中国") != slugify_name("日本")

    def test_the_fallback_is_deterministic(self) -> None:
        assert slugify_name("مدينة") == slugify_name("مدينة")

    def test_a_mixed_script_name_keeps_its_latin_part(self) -> None:
        assert slugify_name("Nungwi 中") == "nungwi"


class TestRejection:
    def test_an_empty_name_is_rejected(self) -> None:
        with pytest.raises(SlugError, match="empty name"):
            slugify_name("")

    def test_a_whitespace_only_name_is_rejected(self) -> None:
        with pytest.raises(SlugError, match="empty name"):
            slugify_name("   ")

    def test_a_punctuation_only_name_is_rejected(self) -> None:
        with pytest.raises(SlugError, match="no usable characters"):
            slugify_name("!!!")


class TestLength:
    def test_a_long_name_is_truncated_not_rejected(self) -> None:
        # An administrator typing a long name should get a working page, not a
        # form error about a column width they cannot see.
        got = slugify_name("Nungwi " * 40)
        assert len(got) <= MAX_SLUG_LENGTH

    def test_truncation_does_not_leave_a_trailing_hyphen(self) -> None:
        got = slugify_name("a" * 139 + " bbbb")
        assert not got.endswith("-")

    def test_a_slug_at_the_limit_is_untouched(self) -> None:
        name = "a" * MAX_SLUG_LENGTH
        assert slugify_name(name) == name


class TestUniqueSlug:
    def test_a_free_slug_is_returned_unchanged(self) -> None:
        assert unique_slug("nungwi", taken=set()) == "nungwi"

    def test_the_first_collision_gets_suffix_two(self) -> None:
        # Starting at 1 would imply the original was numbered too, which reads
        # as a mistake in a URL.
        assert unique_slug("paje-beach", taken={"paje-beach"}) == "paje-beach-2"

    def test_it_walks_past_a_run_of_collisions(self) -> None:
        taken = {"paje-beach", "paje-beach-2", "paje-beach-3"}
        assert unique_slug("paje-beach", taken=taken) == "paje-beach-4"

    def test_a_maximal_length_stem_makes_room_for_the_suffix(self) -> None:
        base = "a" * MAX_SLUG_LENGTH
        got = unique_slug(base, taken={base})
        assert len(got) <= MAX_SLUG_LENGTH
        assert got.endswith("-2")
        assert got != base

    def test_the_shortened_stem_does_not_end_in_a_hyphen_before_the_suffix(self) -> None:
        base = ("a" * 137) + "-bb"
        got = unique_slug(base, taken={base})
        assert "--" not in got

    def test_exhausting_the_candidate_space_raises(self) -> None:
        taken = {"x"} | {f"x-{n}" for n in range(2, 12)}
        with pytest.raises(SlugError, match="exhausted"):
            unique_slug("x", taken=taken, limit=10)

    def test_it_accepts_any_container_not_only_a_set(self) -> None:
        assert unique_slug("nungwi", taken=["nungwi"]) == "nungwi-2"


class TestDeterminism:
    @pytest.mark.parametrize(
        "name",
        ["Stone Town", "Zanzíbar", "中国", "Pemba (Chake Chake)", "Ærø", "Nungwi — Kendwa"],
    )
    def test_repeated_calls_agree(self, name: str) -> None:
        assert slugify_name(name) == slugify_name(name) == slugify_name(name)

    def test_the_output_alphabet_is_url_safe(self) -> None:
        for name in ["Stone Town", "Zanzíbar", "中国", "Freddie Mercury's House", "Ærø"]:
            slug = slugify_name(name)
            assert all(ch.islower() or ch.isdigit() or ch == "-" for ch in slug), slug
            assert not slug.startswith("-")
            assert not slug.endswith("-")
