"""
Tests: the identity a method binding is keyed on.

The key decides which files share a binding, so every way two spellings of
one method could key apart - a path, a case difference, a trailing space -
is what these pin. The two that matter for routing are the constant method
name, which must key as no name at all, and the chemistry key, which must
read two identically-built modes as one chemistry.
"""

from mascope_backend.method_keys import (
    CENSUS_BEARING_INSTRUMENT_TYPES,
    CONSTANT_METHOD_NAMES,
    METHOD_KEY_COLUMN,
    SIGNATURE_CLASS_COLUMN,
    binding_digest,
    chemistry_key,
    clipped,
    method_key,
    signature_class,
)


#: An instrument type whose reader records a census, and one whose does not.
CENSUS_BEARING = "orbi"
CENSUS_LESS = "tof"


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

    def test_a_reader_that_takes_no_census_keys_on_polarity(self):
        # A TofDaq h5 is one acquisition on one mass axis: the polarity is
        # the whole of what it varies, so it is the class.
        assert signature_class(None, "-", CENSUS_LESS) == "-"
        assert signature_class([], "+", CENSUS_LESS) == "+"

    def test_a_missing_census_where_one_was_expected_is_unknown(self):
        # An Orbitrap file converted before the census existed. Keying it on
        # anything else would split this method's history between the guess
        # and the census that later files of it carry.
        assert signature_class(None, "-", CENSUS_BEARING) is None
        assert signature_class([], "-", CENSUS_BEARING) is None

    def test_the_census_bearing_types_are_the_ones_checked(self):
        assert CENSUS_BEARING in CENSUS_BEARING_INSTRUMENT_TYPES
        assert CENSUS_LESS not in CENSUS_BEARING_INSTRUMENT_TYPES

    def test_an_unknown_instrument_type_keys_on_polarity(self):
        # Not knowing the type is not the same as knowing a census was due.
        assert signature_class([], "-", None) == "-"

    def test_the_file_s_own_mass_range_is_not_part_of_the_class(self):
        # It is an outcome, not an instruction: a TofDaq file records the ends
        # of its mass axis, which move with each file's mass calibration, so
        # keying on it gave nearly every file a binding of its own.
        assert signature_class([], "-", CENSUS_LESS) == "-"
        assert "[" not in signature_class([], "-", CENSUS_LESS)

    def test_the_class_is_not_clipped(self):
        # The digest is taken from the whole value; only the column is clipped.
        long_key = "F" * (SIGNATURE_CLASS_COLUMN + 50)
        assert len(signature_class([_stream(long_key, "-")], "-")) == len(long_key)


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

    def test_a_repeated_mechanism_counts_once(self):
        # The modes API compares mechanism lists as sets and its create schema
        # accepts a repeat, so a mode stored with one must not read as a
        # second chemistry and record a disagreement that did not happen.
        assert chemistry_key(["a", "a"]) == chemistry_key(["a"])
        assert chemistry_key(["a", "b", "a"]) == chemistry_key(["b", "a"])


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

    def test_identities_that_differ_only_past_the_column_still_key_apart(self):
        # Clipping before digesting would give two multi-window SIM methods
        # one binding between them.
        long_a = "m" * METHOD_KEY_COLUMN + "a.meth"
        long_b = "m" * METHOD_KEY_COLUMN + "b.meth"
        assert binding_digest("X", long_a, "s") != binding_digest("X", long_b, "s")
        assert clipped(long_a, METHOD_KEY_COLUMN) == clipped(long_b, METHOD_KEY_COLUMN)


class TestMethodKeyLength:
    def test_the_key_is_not_clipped(self):
        long_name = "m" * (METHOD_KEY_COLUMN + 50) + ".meth"
        assert method_key(long_name) == long_name

    def test_the_column_clip_is_the_caller_s(self):
        assert len(clipped("m" * 900, METHOD_KEY_COLUMN)) == METHOD_KEY_COLUMN
