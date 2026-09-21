"""
Names and sizes shared by the mascope_file tests and their fixtures.

A plain module rather than conftest.py content, so the tests can import it:
`from conftest import ...` binds to whichever conftest pytest loaded last,
which in a run spanning several test directories is another directory's.
"""

TEST_FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"
TEST_MZ_SIZE = 20
TEST_TIME_SIZE = 50
