"""
Tests: the identity a method binding is keyed on.

The key decides which files share a binding, so every way two spellings of
one method could key apart - a path, a case difference, a trailing space -
is what these pin. The two that matter for routing are the constant method
name, which must key as no name at all, and the chemistry key, which must
read two identically-built modes as one chemistry.
"""

from mascope_backend.method_keys import (
    CONSTANT_METHOD_NAMES,
    binding_digest,
    chemistry_key,
    method_key,
    signature_class,
)


def _stream(key, polarity, ms_order=1):
    return {"key": key, "signature": {"polarity": polarity, "ms_order": ms_order}}


class TestMethodKey:
    def test_a_windows_path_keys_on_its_basename(self):
        assert (
            method_key(r"C:\Xcalibur\methods\Nitrate_survey.meth")
            == "nitrate_survey.meth"
        )

    def test_a_posix_path_keys_on_its_basename(self):
        assert method_key("/data/methods/Nitrate_survey.meth") == "nitrate_survey.meth"

    def test_case_and_surrounding_space_do_not_separate_two_files(self):
        assert method_key("  NITRATE.METH  ") == method_key("nitrate.meth")

    def test_a_bare_name_keys_on_itself(self):
        assert method_key("nitrate.meth") == "nitrate.meth"

    def test_no_name_keys_on_the_signature_class_alone(self):
        assert method_key(None) == ""
        assert method_key("") == ""
        assert method_key("   ") == ""

    def test_a_name_that_never_varies_is_no_name(self):
        # Tofwerk reports this for every acquisition, whatever the reagent,
        # so a binding under it would outrank the token and route on nothing.
        assert method_key("currentacquisition.ini") == ""
        assert method_key(r"C:\TofDaq\CurrentAcquisition.ini") == ""

    def test_every_constant_is_stored_case_folded(self):
        # method_key compares the folded basename against this set, so a
        # constant written with capitals here would never match.
        assert all(name == name.casefold() for name in CONSTANT_METHOD_NAMES)


class TestSignatureClass:
    def test_the_ms1_streams_of_that_polarity_only(self):
        streams = [
            _stream("FTMS - p NSI Full ms [40.0000-600.0000] R=120000", "-"),
            _stream("FTMS + p NSI Full ms [50.0000-750.0000] R=120000", "+"),
        ]
        assert (
            signature_class(streams, "-")
            == "FTMS - p NSI Full ms [40.0000-600.0000] R=120000"
        )

    def test_fragmentation_streams_are_left_out(self):
        streams = [
            _stream("FTMS - p NSI Full ms [40.0000-600.0000] R=120000", "-"),
            _stream("ITMS - c NSI d Full ms2 *@hcd25.00", "-", ms_order=2),
        ]
        assert "ms2" not in signature_class(streams, "-")

    def test_two_pooled_streams_give_one_class_whatever_their_order(self):
        low = _stream("FTMS - p NSI Full ms [40.0000-300.0000] R=120000", "-")
        high = _stream("FTMS - p NSI Full ms [300.0000-600.0000] R=120000", "-")
        assert signature_class([low, high], "-") == signature_class([high, low], "-")

    def test_the_streams_come_out_sorted(self):
        # The de-duplication is a set, whose iteration order is hash-based and
        # randomised per process - so without an explicit sort one backend
        # worker would key a pooled method differently from the next, and the
        # test above would not notice, because both its orders are one set.
        first = _stream("B FTMS - p NSI Full ms", "-")
        second = _stream("A FTMS - p NSI Full ms", "-")
        assert signature_class([first, second], "-").startswith("A ")

    def test_a_repeated_stream_key_is_counted_once(self):
        one = _stream("FTMS - p NSI Full ms [40.0000-600.0000] R=120000", "-")
        assert signature_class([one], "-") == signature_class([one, dict(one)], "-")

    def test_resolution_separates_two_otherwise_equal_methods(self):
        at_120k = _stream("FTMS - p NSI Full ms [40.0000-600.0000] R=120000", "-")
        at_240k = _stream("FTMS - p NSI Full ms [40.0000-600.0000] R=240000", "-")
        assert signature_class([at_120k], "-") != signature_class([at_240k], "-")

    def test_without_a_census_the_file_describes_itself(self):
        # Every Tofwerk h5, and anything converted before the census existed.
        assert signature_class(None, "-", [10.0, 500.0]) == "- [10.0000-500.0000]"
        assert signature_class([], "-", [10.0, 500.0]) == "- [10.0000-500.0000]"

    def test_without_a_census_or_a_range_the_polarity_is_all_there_is(self):
        assert signature_class([], "-", None) == "-"
        assert signature_class([], "-", []) == "-"

    def test_an_unreadable_range_does_not_raise(self):
        assert signature_class([], "-", ["low", "high"]) == "-"

    def test_a_range_renders_to_four_decimals_however_it_is_stored(self):
        # The census writes its scan ranges to four decimals, so a range read
        # off the file row must not key apart for being stored as integers.
        assert signature_class([], "-", [40, 600]) == signature_class(
            [], "-", [40.0, 600.0]
        )
        assert signature_class([], "-", [40, 600]) == "- [40.0000-600.0000]"


class TestChemistryKey:
    def test_two_modes_built_alike_are_one_chemistry(self):
        assert chemistry_key(["b", "a"]) == chemistry_key(["a", "b"])

    def test_different_reagents_are_different_chemistries(self):
        assert chemistry_key(["a", "b"]) != chemistry_key(["a", "c"])

    def test_a_subset_is_not_the_same_chemistry(self):
        assert chemistry_key(["a"]) != chemistry_key(["a", "b"])

    def test_no_mechanisms_keys_on_nothing(self):
        assert chemistry_key(None) == ""
        assert chemistry_key([]) == ""


class TestBindingDigest:
    def test_the_same_identity_digests_alike(self):
        assert binding_digest("X", "m.meth", "sig") == binding_digest(
            "X", "m.meth", "sig"
        )

    def test_the_parts_cannot_run_together(self):
        # Without a separator no two of these could be told apart, and two
        # instruments would share one binding.
        assert binding_digest("ab", "c", "") != binding_digest("a", "bc", "")
        assert binding_digest("a", "", "bc") != binding_digest("a", "b", "c")

    def test_it_fits_the_column(self):
        assert len(binding_digest("X", "m.meth", "sig")) == 64
