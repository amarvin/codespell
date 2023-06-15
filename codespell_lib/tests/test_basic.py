import contextlib
import inspect
import os
import os.path as op
import re
import subprocess
import sys
from io import StringIO
from pathlib import Path
from shutil import copyfile
from typing import Any, Generator, Optional, Tuple, Union

import pytest

import codespell_lib as cs_
from codespell_lib._codespell import EX_DATAERR, EX_OK, EX_USAGE, uri_regex_def


def test_constants() -> None:
    """Test our EX constants."""
    assert EX_OK == 0
    assert EX_USAGE == 64
    assert EX_DATAERR == 65


class MainWrapper:
    """Compatibility wrapper for when we used to return the count."""

    @staticmethod
    def main(
        *args: Any,
        count: bool = True,
        std: bool = False,
    ) -> Union[int, Tuple[int, str, str]]:
        args = tuple(str(arg) for arg in args)
        if count:
            args = ("--count",) + args
        code = cs_.main(*args)
        frame = inspect.currentframe()
        assert frame is not None
        frame = frame.f_back
        assert frame is not None
        capsys = frame.f_locals["capsys"]
        stdout, stderr = capsys.readouterr()
        assert code in (EX_OK, EX_USAGE, EX_DATAERR)
        if code == EX_DATAERR:  # have some misspellings
            code = int(stderr.split("\n")[-2])
        elif code == EX_OK and count:
            code = int(stderr.split("\n")[-2])
            assert code == 0
        if std:
            return (code, stdout, stderr)
        return code


cs = MainWrapper()


def run_codespell(
    args: Tuple[Any, ...] = (),
    cwd: Optional[Path] = None,
) -> int:
    """Run codespell."""
    args = tuple(str(arg) for arg in args)
    proc = subprocess.run(
        ["codespell", "--count", *args],  # noqa: S603, S607
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
    )
    count = int(proc.stderr.split("\n")[-2])
    return count


def test_command(tmp_path: Path) -> None:
    """Test running the codespell executable."""
    # With no arguments does "."
    assert run_codespell(cwd=tmp_path) == 0
    (tmp_path / "bad.txt").write_text("abandonned\nAbandonned\nABANDONNED\nAbAnDoNnEd")
    assert run_codespell(cwd=tmp_path) == 4


def test_basic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test some basic functionality."""
    assert cs.main("_does_not_exist_") == 0
    fname = tmp_path / "tmp"
    fname.touch()
    result = cs.main("-D", "foo", fname, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == EX_USAGE, "missing dictionary"
    assert "cannot find dictionary" in stderr
    assert cs.main(fname) == 0, "empty file"
    with fname.open("a") as f:
        f.write("this is a test file\n")
    assert cs.main(fname) == 0, "good"
    with fname.open("a") as f:
        f.write("var = 'nwe must check codespell likes escapes nin strings'\n")
    assert cs.main(fname) == 2, "checking our string escape test words are bad"
    with fname.open("a") as f:
        f.write("var = '\\nwe must check codespell likes escapes \\nin strings'\n")
    assert cs.main(fname) == 0, "with string escape"
    with fname.open("a") as f:
        f.write("abandonned\n")
    assert cs.main(fname) == 1, "bad"
    with fname.open("a") as f:
        f.write("abandonned\n")
    assert cs.main(fname) == 2, "worse"
    with fname.open("a") as f:
        f.write("tim\ngonna\n")
    assert cs.main(fname) == 2, "with a name"
    assert cs.main("--builtin", "clear,rare,names,informal", fname) == 4
    result = cs.main(fname, "--builtin", "foo", std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == EX_USAGE  # bad type
    assert "Unknown builtin dictionary" in stderr
    result = cs.main(fname, "-D", tmp_path / "foo", std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == EX_USAGE  # bad dict
    assert "cannot find dictionary" in stderr
    fname.unlink()

    with (tmp_path / "bad.txt").open("w", newline="") as f:
        f.write(
            "abandonned\nAbandonned\nABANDONNED\nAbAnDoNnEd\nabandonned\rAbandonned\r\nABANDONNED \n AbAnDoNnEd"  # noqa: E501
        )
    assert cs.main(tmp_path) == 8
    result = cs.main("-w", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == 0
    assert "FIXED:" in stderr
    with (tmp_path / "bad.txt").open(newline="") as f:
        new_content = f.read()
    assert cs.main(tmp_path) == 0
    assert (
        new_content
        == "abandoned\nAbandoned\nABANDONED\nabandoned\nabandoned\rAbandoned\r\nABANDONED \n abandoned"  # noqa: E501
    )

    (tmp_path / "bad.txt").write_text("abandonned abandonned\n")
    assert cs.main(tmp_path) == 2
    result = cs.main("-q", "16", "-w", tmp_path, count=False, std=True)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert code == 0
    assert not stdout and not stderr
    assert cs.main(tmp_path) == 0

    # empty directory
    (tmp_path / "empty").mkdir()
    assert cs.main(tmp_path) == 0


def test_bad_glob(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # disregard invalid globs, properly handle escaped globs
    g = tmp_path / "glob"
    g.mkdir()
    fname = g / "[b-a].txt"
    fname.write_text("abandonned\n")
    assert cs.main(g) == 1
    # bad glob is invalid
    result = cs.main("--skip", "[b-a].txt", g, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    if sys.hexversion < 0x030A05F0:  # Python < 3.10.5 raises re.error
        assert code == EX_USAGE, "invalid glob"
        assert "invalid glob" in stderr
    else:  # Python >= 3.10.5 does not match
        assert code == 1
    # properly escaped glob is valid, and matches glob-like file name
    assert cs.main("--skip", "[[]b-a[]].txt", g) == 0


@pytest.mark.skipif(sys.platform != "linux", reason="Only supported on Linux")
def test_permission_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test permission error handling."""
    fname = tmp_path / "unreadable.txt"
    fname.write_text("abandonned\n")
    result = cs.main(fname, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert "WARNING:" not in stderr
    fname.chmod(0o000)
    result = cs.main(fname, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert "WARNING:" in stderr


def test_interactivity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test interaction"""
    # Windows can't read a currently-opened file, so here we use
    # NamedTemporaryFile just to get a good name
    fname = tmp_path / "tmp"
    fname.touch()
    try:
        assert cs.main(fname) == 0, "empty file"
        fname.write_text("abandonned\n")
        assert cs.main("-i", "-1", fname) == 1, "bad"
        with FakeStdin("y\n"):
            assert cs.main("-i", "3", fname) == 1
        with FakeStdin("n\n"):
            result = cs.main("-w", "-i", "3", fname, std=True)
            assert isinstance(result, tuple)
            code, stdout, _ = result
            assert code == 0
        assert "==>" in stdout
        with FakeStdin("x\ny\n"):
            assert cs.main("-w", "-i", "3", fname) == 0
        assert cs.main(fname) == 0
    finally:
        fname.unlink()

    # New example
    fname = tmp_path / "tmp2"
    fname.write_text("abandonned\n")
    try:
        assert cs.main(fname) == 1
        with FakeStdin(" "):  # blank input -> Y
            assert cs.main("-w", "-i", "3", fname) == 0
        assert cs.main(fname) == 0
    finally:
        fname.unlink()

    # multiple options
    fname = tmp_path / "tmp3"
    fname.write_text("ackward\n")
    try:
        assert cs.main(fname) == 1
        with FakeStdin(" \n"):  # blank input -> nothing
            assert cs.main("-w", "-i", "3", fname) == 0
        assert cs.main(fname) == 1
        with FakeStdin("0\n"):  # blank input -> nothing
            assert cs.main("-w", "-i", "3", fname) == 0
        assert cs.main(fname) == 0
        assert fname.read_text() == "awkward\n"
        fname.write_text("ackward\n")
        assert cs.main(fname) == 1
        with FakeStdin("x\n1\n"):  # blank input -> nothing
            result = cs.main("-w", "-i", "3", fname, std=True)
            assert isinstance(result, tuple)
            code, stdout, _ = result
            assert code == 0
        assert "a valid option" in stdout
        assert cs.main(fname) == 0
        assert fname.read_text() == "backward\n"
    finally:
        fname.unlink()


def test_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test summary functionality."""
    fname = tmp_path / "tmp"
    fname.touch()
    result = cs.main(fname, std=True, count=False)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert code == 0
    assert not stdout and not stderr, "no output"
    result = cs.main(fname, "--summary", std=True)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert code == 0
    assert stderr == "0\n"
    assert "SUMMARY" in stdout
    assert len(stdout.split("\n")) == 5
    fname.write_text("abandonned\nabandonned")
    assert code == 0
    result = cs.main(fname, "--summary", std=True)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert stderr == "2\n"
    assert "SUMMARY" in stdout
    assert len(stdout.split("\n")) == 7
    assert "abandonned" in stdout.split()[-2]


def test_ignore_dictionary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test ignore dictionary functionality."""
    bad_name = tmp_path / "bad.txt"
    bad_name.write_text("1 abandonned 1\n2 abandonned 2\nabondon\n")
    assert cs.main(bad_name) == 3
    fname = tmp_path / "ignore.txt"
    fname.write_text("abandonned\n")
    assert cs.main("-I", fname, bad_name) == 1


def test_ignore_word_list(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test ignore word list functionality."""
    (tmp_path / "bad.txt").write_text("abandonned\nabondon\nabilty\n")
    assert cs.main(tmp_path) == 3
    assert cs.main("-Labandonned,someword", "-Labilty", tmp_path) == 1


def test_custom_regex(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test custom word regex."""
    (tmp_path / "bad.txt").write_text("abandonned_abondon\n")
    assert cs.main(tmp_path) == 0
    assert cs.main("-r", "[a-z]+", tmp_path) == 2
    result = cs.main("-r", "[a-z]+", "--write-changes", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == EX_USAGE
    assert "ERROR:" in stderr


def test_exclude_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test exclude file functionality."""
    bad_name = tmp_path / "bad.txt"
    bad_name.write_bytes(b"1 abandonned 1\n2 abandonned 2\n")
    assert cs.main(bad_name) == 2
    fname = tmp_path / "tmp.txt"
    fname.write_bytes(b"1 abandonned 1\n")
    assert cs.main(bad_name) == 2
    assert cs.main("-x", fname, bad_name) == 1


def test_encoding(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test encoding handling."""
    # Some simple Unicode things
    fname = tmp_path / "tmp"
    fname.touch()
    # with CaptureStdout() as sio:
    assert cs.main(fname) == 0
    fname.write_bytes("naïve\n".encode())
    assert cs.main(fname) == 0
    assert cs.main("-e", fname) == 0
    with fname.open("ab") as f:
        f.write(b"naieve\n")
    assert cs.main(fname) == 1
    # Encoding detection (only try ISO 8859-1 because UTF-8 is the default)
    fname.write_bytes(b"Speling error, non-ASCII: h\xe9t\xe9rog\xe9n\xe9it\xe9\n")
    # check warnings about wrong encoding are enabled with "-q 0"
    result = cs.main("-q", "0", fname, std=True, count=True)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert code == 1
    assert "Speling" in stdout
    assert "iso-8859-1" in stderr
    # check warnings about wrong encoding are disabled with "-q 1"
    result = cs.main("-q", "1", fname, std=True, count=True)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert code == 1
    assert "Speling" in stdout
    assert "iso-8859-1" not in stderr
    # Binary file warning
    fname.write_bytes(b"\x00\x00naiive\x00\x00")
    result = cs.main(fname, std=True, count=False)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert code == 0
    assert not stdout and not stderr
    result = cs.main("-q", "0", fname, std=True, count=False)
    assert isinstance(result, tuple)
    code, stdout, stderr = result
    assert code == 0
    assert not stdout
    assert "WARNING: Binary file" in stderr


def test_unknown_encoding_chardet(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test opening a file with unknown encoding using chardet"""
    fname = tmp_path / "tmp"
    fname.touch()
    assert cs.main("--hard-encoding-detection", fname) == 0


def test_ignore(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test ignoring of files and directories."""
    goodtxt = tmp_path / "good.txt"
    goodtxt.write_text("this file is okay")
    assert cs.main(tmp_path) == 0
    badtxt = tmp_path / "bad.txt"
    badtxt.write_text("abandonned")
    assert cs.main(tmp_path) == 1
    assert cs.main("--skip=bad*", tmp_path) == 0
    assert cs.main("--skip=bad.txt", tmp_path) == 0
    subdir = tmp_path / "ignoredir"
    subdir.mkdir()
    (subdir / "bad.txt").write_text("abandonned")
    assert cs.main(tmp_path) == 2
    assert cs.main("--skip=bad*", tmp_path) == 0
    assert cs.main("--skip=*ignoredir*", tmp_path) == 1
    assert cs.main("--skip=ignoredir", tmp_path) == 1
    assert cs.main("--skip=*ignoredir/bad*", tmp_path) == 1
    badjs = tmp_path / "bad.js"
    copyfile(badtxt, badjs)
    assert cs.main("--skip=*.js", goodtxt, badtxt, badjs) == 1


def test_check_filename(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test filename check."""
    fname = tmp_path / "abandonned.txt"
    # Empty file
    fname.touch()
    assert cs.main("-f", tmp_path) == 1
    # Normal file with contents
    fname.write_text(".")
    assert cs.main("-f", tmp_path) == 1
    # Normal file with binary contents
    fname.write_bytes(b"\x00\x00naiive\x00\x00")
    assert cs.main("-f", tmp_path) == 1


@pytest.mark.skipif(
    (not hasattr(os, "mkfifo") or not callable(os.mkfifo)), reason="requires os.mkfifo"
)
def test_check_filename_irregular_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test irregular file filename check."""
    # Irregular file (!isfile())
    os.mkfifo(tmp_path / "abandonned")
    assert cs.main("-f", tmp_path) == 1


def test_check_hidden(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test ignoring of hidden files."""
    # visible file
    #
    #         tmp_path
    #         └── test.txt
    #
    fname = tmp_path / "test.txt"
    fname.write_text("erorr\n")
    assert cs.main(fname) == 1
    assert cs.main(tmp_path) == 1

    # hidden file
    #
    #         tmp_path
    #         └── .test.txt
    #
    hidden_file = tmp_path / ".test.txt"
    fname.rename(hidden_file)
    assert cs.main(hidden_file) == 0
    assert cs.main(tmp_path) == 0
    assert cs.main("--check-hidden", hidden_file) == 1
    assert cs.main("--check-hidden", tmp_path) == 1

    # hidden file with typo in name
    #
    #         tmp_path
    #         └── .abandonned.txt
    #
    typo_file = tmp_path / ".abandonned.txt"
    hidden_file.rename(typo_file)
    assert cs.main(typo_file) == 0
    assert cs.main(tmp_path) == 0
    assert cs.main("--check-hidden", typo_file) == 1
    assert cs.main("--check-hidden", tmp_path) == 1
    assert cs.main("--check-hidden", "--check-filenames", typo_file) == 2
    assert cs.main("--check-hidden", "--check-filenames", tmp_path) == 2

    # hidden directory
    #
    #         tmp_path
    #         ├── .abandonned
    #         │   ├── .abandonned.txt
    #         │   └── subdir
    #         │       └── .abandonned.txt
    #         └── .abandonned.txt
    #
    assert cs.main(tmp_path) == 0
    assert cs.main("--check-hidden", tmp_path) == 1
    assert cs.main("--check-hidden", "--check-filenames", tmp_path) == 2
    hidden = tmp_path / ".abandonned"
    hidden.mkdir()
    copyfile(typo_file, hidden / typo_file.name)
    subdir = hidden / "subdir"
    subdir.mkdir()
    copyfile(typo_file, subdir / typo_file.name)
    assert cs.main(tmp_path) == 0
    assert cs.main("--check-hidden", tmp_path) == 3
    assert cs.main("--check-hidden", "--check-filenames", tmp_path) == 8
    # check again with a relative path
    try:
        rel = op.relpath(tmp_path)
    except ValueError:
        # Windows: path is on mount 'C:', start on mount 'D:'
        pass
    else:
        assert cs.main(rel) == 0
        assert cs.main("--check-hidden", rel) == 3
        assert cs.main("--check-hidden", "--check-filenames", rel) == 8

    # hidden subdirectory
    #
    #         tmp_path
    #         ├── .abandonned
    #         │   ├── .abandonned.txt
    #         │   └── subdir
    #         │       └── .abandonned.txt
    #         ├── .abandonned.txt
    #         └── subdir
    #             └── .abandonned
    #                 └── .abandonned.txt
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    hidden = subdir / ".abandonned"
    hidden.mkdir()
    copyfile(typo_file, hidden / typo_file.name)
    assert cs.main(tmp_path) == 0
    assert cs.main("--check-hidden", tmp_path) == 4
    assert cs.main("--check-hidden", "--check-filenames", tmp_path) == 11


def test_case_handling(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that capitalized entries get detected properly."""
    # Some simple Unicode things
    fname = tmp_path / "tmp"
    fname.touch()
    # with CaptureStdout() as sio:
    assert cs.main(fname) == 0
    fname.write_bytes(b"this has an ACII error")
    result = cs.main(fname, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 1
    assert "ASCII" in stdout
    result = cs.main("-w", fname, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == 0
    assert "FIXED" in stderr
    assert fname.read_text(encoding="utf-8") == "this has an ASCII error"


def _helper_test_case_handling_in_fixes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reason: bool,
) -> None:
    dictionary_name = tmp_path / "dictionary.txt"
    if reason:
        dictionary_name.write_text("adoptor->adopter, adaptor, reason\n")
    else:
        dictionary_name.write_text("adoptor->adopter, adaptor,\n")

    # the mispelled word is entirely lowercase
    fname = tmp_path / "bad.txt"
    fname.write_text("early adoptor\n")
    result = cs.main("-D", dictionary_name, fname, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    # all suggested fixes must be lowercase too
    assert "adopter, adaptor" in stdout
    # the reason, if any, must not be modified
    if reason:
        assert "reason" in stdout

    # the mispelled word is capitalized
    fname.write_text("Early Adoptor\n")
    result = cs.main("-D", dictionary_name, fname, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    # all suggested fixes must be capitalized too
    assert "Adopter, Adaptor" in stdout
    # the reason, if any, must not be modified
    if reason:
        assert "reason" in stdout

    # the mispelled word is entirely uppercase
    fname.write_text("EARLY ADOPTOR\n")
    result = cs.main("-D", dictionary_name, fname, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    # all suggested fixes must be uppercase too
    assert "ADOPTER, ADAPTOR" in stdout
    # the reason, if any, must not be modified
    if reason:
        assert "reason" in stdout

    # the mispelled word mixes lowercase and uppercase
    fname.write_text("EaRlY AdOpToR\n")
    result = cs.main("-D", dictionary_name, fname, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    # all suggested fixes should be lowercase
    assert "adopter, adaptor" in stdout
    # the reason, if any, must not be modified
    if reason:
        assert "reason" in stdout


def test_case_handling_in_fixes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that the case of fixes is similar to the mispelled word."""
    _helper_test_case_handling_in_fixes(tmp_path, capsys, reason=False)
    _helper_test_case_handling_in_fixes(tmp_path, capsys, reason=True)


def test_context(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test context options."""
    (tmp_path / "context.txt").write_text(
        "line 1\nline 2\nline 3 abandonned\nline 4\nline 5"
    )

    # symmetric context, fully within file
    result = cs.main("-C", "1", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 1
    lines = stdout.split("\n")
    assert len(lines) == 5
    assert lines[0] == ": line 2"
    assert lines[1] == "> line 3 abandonned"
    assert lines[2] == ": line 4"

    # requested context is bigger than the file
    result = cs.main("-C", "10", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 1
    lines = stdout.split("\n")
    assert len(lines) == 7
    assert lines[0] == ": line 1"
    assert lines[1] == ": line 2"
    assert lines[2] == "> line 3 abandonned"
    assert lines[3] == ": line 4"
    assert lines[4] == ": line 5"

    # only before context
    result = cs.main("-B", "2", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 1
    lines = stdout.split("\n")
    assert len(lines) == 5
    assert lines[0] == ": line 1"
    assert lines[1] == ": line 2"
    assert lines[2] == "> line 3 abandonned"

    # only after context
    result = cs.main("-A", "1", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 1
    lines = stdout.split("\n")
    assert len(lines) == 4
    assert lines[0] == "> line 3 abandonned"
    assert lines[1] == ": line 4"

    # asymmetric context
    result = cs.main("-B", "2", "-A", "1", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 1
    lines = stdout.split("\n")
    assert len(lines) == 6
    assert lines[0] == ": line 1"
    assert lines[1] == ": line 2"
    assert lines[2] == "> line 3 abandonned"
    assert lines[3] == ": line 4"

    # both '-C' and '-A' on the command line
    result = cs.main("-C", "2", "-A", "1", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == EX_USAGE
    lines = stderr.split("\n")
    assert "ERROR" in lines[0]

    # both '-C' and '-B' on the command line
    result = cs.main("-C", "2", "-B", "1", tmp_path, std=True)
    assert isinstance(result, tuple)
    code, _, stderr = result
    assert code == EX_USAGE
    lines = stderr.split("\n")
    assert "ERROR" in lines[0]


def test_ignore_regex_option(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test ignore regex option functionality."""

    # Invalid regex.
    result = cs.main("--ignore-regex=(", std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == EX_USAGE
    assert "usage:" in stdout

    fname = tmp_path / "flag.txt"
    fname.write_text("# Please see http://example.com/abandonned for info\n")
    # Test file has 1 invalid entry, and it's not ignored by default.
    assert cs.main(fname) == 1
    # An empty regex is the default value, and nothing is ignored.
    assert cs.main(fname, "--ignore-regex=") == 1
    assert cs.main(fname, '--ignore-regex=""') == 1
    # Non-matching regex results in nothing being ignored.
    assert cs.main(fname, "--ignore-regex=^$") == 1
    # A word can be ignored.
    assert cs.main(fname, "--ignore-regex=abandonned") == 0
    # Ignoring part of the word can result in odd behavior.
    assert cs.main(fname, "--ignore-regex=nn") == 0

    fname.write_text("abandonned donn\n")
    # Test file has 2 invalid entries.
    assert cs.main(fname) == 2
    # Ignoring donn breaks them both.
    assert cs.main(fname, "--ignore-regex=donn") == 0
    # Adding word breaks causes only one to be ignored.
    assert cs.main(fname, r"--ignore-regex=\bdonn\b") == 1


def test_uri_regex_option(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --uri-regex option functionality."""

    # Invalid regex.
    result = cs.main("--uri-regex=(", std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == EX_USAGE
    assert "usage:" in stdout

    fname = tmp_path / "flag.txt"
    fname.write_text("# Please see http://abandonned.com for info\n")

    # By default, the standard regex is used.
    assert cs.main(fname) == 1
    assert cs.main(fname, "--uri-ignore-words-list=abandonned") == 0

    # If empty, nothing matches.
    assert cs.main(fname, "--uri-regex=", "--uri-ignore-words-list=abandonned") == 0

    # Can manually match urls.
    assert (
        cs.main(fname, "--uri-regex=\\bhttp.*\\b", "--uri-ignore-words-list=abandonned")
        == 0
    )

    # Can also match arbitrary content.
    fname.write_text("abandonned")
    assert cs.main(fname) == 1
    assert cs.main(fname, "--uri-ignore-words-list=abandonned") == 1
    assert cs.main(fname, "--uri-regex=.*") == 1
    assert cs.main(fname, "--uri-regex=.*", "--uri-ignore-words-list=abandonned") == 0


def test_uri_ignore_words_list_option_uri(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test ignore regex option functionality."""

    fname = tmp_path / "flag.txt"
    fname.write_text("# Please see http://example.com/abandonned for info\n")
    # Test file has 1 invalid entry, and it's not ignored by default.
    assert cs.main(fname) == 1
    # An empty list is the default value, and nothing is ignored.
    assert cs.main(fname, "--uri-ignore-words-list=") == 1
    # Non-matching regex results in nothing being ignored.
    assert cs.main(fname, "--uri-ignore-words-list=foo,example") == 1
    # A word can be ignored.
    assert cs.main(fname, "--uri-ignore-words-list=abandonned") == 0
    assert cs.main(fname, "--uri-ignore-words-list=foo,abandonned,bar") == 0
    assert cs.main(fname, "--uri-ignore-words-list=*") == 0
    # The match must be for the complete word.
    assert cs.main(fname, "--uri-ignore-words-list=abandonn") == 1

    fname.write_text("abandonned http://example.com/abandonned\n")
    # Test file has 2 invalid entries.
    assert cs.main(fname) == 2
    # Ignoring the value in the URI won't ignore the word completely.
    assert cs.main(fname, "--uri-ignore-words-list=abandonned") == 1
    assert cs.main(fname, "--uri-ignore-words-list=*") == 1
    # The regular --ignore-words-list will ignore both.
    assert cs.main(fname, "--ignore-words-list=abandonned") == 0

    variation_option = "--uri-ignore-words-list=abandonned"

    # Variations where an error is ignored.
    for variation in (
        "# Please see http://abandonned for info\n",
        '# Please see "http://abandonned" for info\n',
        # This variation could be un-ignored, but it'd require a
        # more complex regex as " is valid in parts of URIs.
        '# Please see "http://foo"abandonned for info\n',
        "# Please see https://abandonned for info\n",
        "# Please see ftp://abandonned for info\n",
        "# Please see http://example/abandonned for info\n",
        "# Please see http://example.com/abandonned for info\n",
        "# Please see http://exam.com/ple#abandonned for info\n",
        "# Please see http://exam.com/ple?abandonned for info\n",
        "# Please see http://127.0.0.1/abandonned for info\n",
        "# Please see http://[2001:0db8:85a3:0000:0000:8a2e:0370"
        ":7334]/abandonned for info\n",
    ):
        fname.write_text(variation)
        assert cs.main(fname) == 1, variation
        assert cs.main(fname, variation_option) == 0, variation

    # Variations where no error is ignored.
    for variation in (
        "# Please see abandonned/ for info\n",
        "# Please see http:abandonned for info\n",
        "# Please see foo/abandonned for info\n",
        "# Please see http://foo abandonned for info\n",
    ):
        fname.write_text(variation)
        assert cs.main(fname) == 1, variation
        assert cs.main(fname, variation_option) == 1, variation


def test_uri_ignore_words_list_option_email(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test ignore regex option functionality."""

    fname = tmp_path / "flag.txt"
    fname.write_text("# Please see example@abandonned.com for info\n")
    # Test file has 1 invalid entry, and it's not ignored by default.
    assert cs.main(fname) == 1
    # An empty list is the default value, and nothing is ignored.
    assert cs.main(fname, "--uri-ignore-words-list=") == 1
    # Non-matching regex results in nothing being ignored.
    assert cs.main(fname, "--uri-ignore-words-list=foo,example") == 1
    # A word can be ignored.
    assert cs.main(fname, "--uri-ignore-words-list=abandonned") == 0
    assert cs.main(fname, "--uri-ignore-words-list=foo,abandonned,bar") == 0
    assert cs.main(fname, "--uri-ignore-words-list=*") == 0
    # The match must be for the complete word.
    assert cs.main(fname, "--uri-ignore-words-list=abandonn") == 1

    fname.write_text("abandonned example@abandonned.com\n")
    # Test file has 2 invalid entries.
    assert cs.main(fname) == 2
    # Ignoring the value in the URI won't ignore the word completely.
    assert cs.main(fname, "--uri-ignore-words-list=abandonned") == 1
    assert cs.main(fname, "--uri-ignore-words-list=*") == 1
    # The regular --ignore-words-list will ignore both.
    assert cs.main(fname, "--ignore-words-list=abandonned") == 0

    variation_option = "--uri-ignore-words-list=abandonned"

    # Variations where an error is ignored.
    for variation in (
        "# Please see example@abandonned for info\n",
        "# Please see abandonned@example for info\n",
        "# Please see abandonned@example.com for info\n",
        "# Please see mailto:abandonned@example.com?subject=Test for info\n",
    ):
        fname.write_text(variation)
        assert cs.main(fname) == 1, variation
        assert cs.main(fname, variation_option) == 0, variation

    # Variations where no error is ignored.
    for variation in (
        "# Please see example @ abandonned for info\n",
        "# Please see abandonned@ example for info\n",
        "# Please see mailto:foo@example.com?subject=Test abandonned for info\n",
    ):
        fname.write_text(variation)
        assert cs.main(fname) == 1, variation
        assert cs.main(fname, variation_option) == 1, variation


def test_uri_regex_def() -> None:
    uri_regex = re.compile(uri_regex_def)

    # Tests based on https://mathiasbynens.be/demo/url-regex
    true_positives = (
        "http://foo.com/blah_blah",
        "http://foo.com/blah_blah/",
        "http://foo.com/blah_blah_(wikipedia)",
        "http://foo.com/blah_blah_(wikipedia)_(again)",
        "http://www.example.com/wpstyle/?p=364",
        "https://www.example.com/foo/?bar=baz&inga=42&quux",
        "http://✪df.ws/123",
        "http://userid:password@example.com:8080",
        "http://userid:password@example.com:8080/",
        "http://userid@example.com",
        "http://userid@example.com/",
        "http://userid@example.com:8080",
        "http://userid@example.com:8080/",
        "http://userid:password@example.com",
        "http://userid:password@example.com/",
        "http://142.42.1.1/",
        "http://142.42.1.1:8080/",
        "http://➡.ws/䨹",
        "http://⌘.ws",
        "http://⌘.ws/",
        "http://foo.com/blah_(wikipedia)#cite-1",
        "http://foo.com/blah_(wikipedia)_blah#cite-1",
        "http://foo.com/unicode_(✪)_in_parens",
        "http://foo.com/(something)?after=parens",
        "http://☺.damowmow.com/",
        "http://code.google.com/events/#&product=browser",
        "http://j.mp",
        "ftp://foo.bar/baz",
        "http://foo.bar/?q=Test%20URL-encoded%20stuff",
        "http://مثال.إختبار",
        "http://例子.测试",
        "http://उदाहरण.परीक्षा",
        "http://-.~_!$&'()*+,;=:%40:80%2f::::::@example.com",
        "http://1337.net",
        "http://a.b-c.de",
        "http://223.255.255.254",
    )
    true_negatives = (
        "http://",
        "//",
        "//a",
        "///a",
        "///",
        "foo.com",
        "rdar://1234",
        "h://test",
        "://should.fail",
        "ftps://foo.bar/",
    )
    false_positives = (
        "http://.",
        "http://..",
        "http://../",
        "http://?",
        "http://??",
        "http://??/",
        "http://#",
        "http://##",
        "http://##/",
        "http:///a",
        "http://-error-.invalid/",
        "http://a.b--c.de/",
        "http://-a.b.co",
        "http://a.b-.co",
        "http://0.0.0.0",
        "http://10.1.1.0",
        "http://10.1.1.255",
        "http://224.1.1.1",
        "http://1.1.1.1.1",
        "http://123.123.123",
        "http://3628126748",
        "http://.www.foo.bar/",
        "http://www.foo.bar./",
        "http://.www.foo.bar./",
        "http://10.1.1.1",
    )

    boilerplate = "Surrounding text %s more text"

    for uri in true_positives + false_positives:
        assert uri_regex.findall(uri) == [uri], uri
        assert uri_regex.findall(boilerplate % uri) == [uri], uri

    for uri in true_negatives:
        assert not uri_regex.findall(uri), uri
        assert not uri_regex.findall(boilerplate % uri), uri


def test_quiet_option_32(
    tmp_path: Path,
    tmpdir: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    d = tmp_path / "files"
    d.mkdir()
    conf = str(tmp_path / "setup.cfg")
    with open(conf, "w") as f:
        # It must contain a "codespell" section.
        f.write("[codespell]\n")
    args = ("--config", conf)

    # Config files should NOT be in output.
    result = cs.main(str(d), *args, "--quiet-level=32", std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 0
    assert "Used config files:" not in stdout

    # Config files SHOULD be in output.
    result = cs.main(str(d), *args, "--quiet-level=2", std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 0
    assert "Used config files:" in stdout
    assert "setup.cfg" in stdout


@pytest.mark.parametrize("kind", ("toml", "cfg"))
def test_config_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    """Test loading options from a config file or toml."""
    d = tmp_path / "files"
    d.mkdir()
    (d / "bad.txt").write_text("abandonned donn\n")
    (d / "good.txt").write_text("good")

    # Should fail when checking both.
    result = cs.main(d, count=True, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    # Code in this case is not exit code, but count of misspellings.
    assert code == 2
    assert "bad.txt" in stdout

    if kind == "cfg":
        conffile = tmp_path / "setup.cfg"
        args = ("--config", conffile)
        conffile.write_text(
            """\
[codespell]
skip = bad.txt, whatever.txt
count =
"""
        )
    else:
        assert kind == "toml"
        if sys.version_info < (3, 11):
            pytest.importorskip("tomli")
        tomlfile = tmp_path / "pyproject.toml"
        args = ("--toml", tomlfile)
        tomlfile.write_text(
            """\
[tool.codespell]
skip = 'bad.txt,whatever.txt'
count = false
"""
        )

    # Should pass when skipping bad.txt
    result = cs.main(d, *args, count=True, std=True)
    assert isinstance(result, tuple)
    code, stdout, _ = result
    assert code == 0
    assert "bad.txt" not in stdout

    # And both should automatically work if they're in cwd
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = cs.main(d, count=True, std=True)
        assert isinstance(result, tuple)
        code, stdout, _ = result
    finally:
        os.chdir(cwd)
    assert code == 0
    assert "bad.txt" not in stdout


@contextlib.contextmanager
def FakeStdin(text: str) -> Generator[None, None, None]:
    oldin = sys.stdin
    try:
        in_ = StringIO(text)
        sys.stdin = in_
        yield
    finally:
        sys.stdin = oldin
