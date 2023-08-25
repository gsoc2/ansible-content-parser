"""A runpy entry point for ansible-content-parser.

This makes it possible to invoke CLI
via :command:`python -m ansible_content_parser`.
"""
import argparse
import contextlib
import errno
import json
import logging
import os
import sys
import typing

from collections.abc import Generator
from importlib import import_module
from pathlib import Path

from ansiblelint.constants import RC
from ansiblelint.file_utils import Lintable

from .downloader import Downloader
from .lint import ansiblelint_main
from .report import main as report_main


_logger = logging.getLogger(__name__)


class LintableDict(dict[str, typing.Any]):
    """The LintableDict class."""

    def __init__(self, lintable: Lintable) -> None:
        """Initialize LintableDict."""
        self["base_kind"] = str(lintable.base_kind)
        self["dir"] = str(lintable.dir)
        self["exc"] = None if lintable.exc is None else str(lintable.exc)
        self["filename"] = str(lintable.filename)
        self["kind"] = str(lintable.kind)
        self["name"] = str(lintable.name)
        self["parent"] = None if lintable.parent is None else str(lintable.parent.name)
        self["role"] = str(lintable.role)
        self["stop_processing"] = bool(lintable.stop_processing)
        self["updated"] = bool(lintable.updated)


# https://stackoverflow.com/questions/6194499/pushd-through-os-system
@contextlib.contextmanager
def pushd(new_dir: str) -> Generator[None, None, None]:
    """Use this in a with block to run some operations from a given directory."""
    previous_dir = Path.cwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)


def execute_ansiblelint(
    argv: list[str],
    work_dir: str,
) -> dict[str, list[LintableDict]]:
    """Execute ansible-lint."""
    with pushd(work_dir):
        result, mark_as_success = ansiblelint_main(argv)
        return {
            "files": [LintableDict(lintable) for lintable in result.files],
        }


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse arguments."""
    parser = argparse.ArgumentParser(description="TODO")
    parser.add_argument("-d", "--dir", help="root directory for scan")
    parser.add_argument("-t", "--source-type", help="source type")
    parser.add_argument(
        "-r",
        "--repo-name",
        help='repo name (e.g."IBM/Ansible-OpenShift-Provisioning")',
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        default="",
        help="output directory for the rule evaluation result",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="explain what is being done",
    )
    parser.add_argument("-u", "--url", help="repository URL")
    args = parser.parse_args(argv)
    if not (args.out_dir and (args.dir or args.url)):
        parser.print_help()
        sys.exit(1)
    return args


def main(argv: list[str]) -> int:
    """Parse arguments and execute the content parser."""
    args = parse_args(argv)

    if args.url:
        downloader = Downloader(args.out_dir)
        repo_name = downloader.extract(args.url)
        out_path = Path(args.out_dir)
        args.dir = str(out_path / repo_name)

    sarif_file = str(out_path / "sarif.json")

    argv = ["__DUMMY__", "--sarif-file", sarif_file, "--write"]
    if args.verbose:
        argv.append("-v")

    try:
        serializable_result = execute_ansiblelint(argv, args.dir)
        path_out_dir = Path(args.out_dir)
        if not path_out_dir.exists():
            path_out_dir.mkdir(parents=True)
        with Path(args.out_dir / "lint-result.json").open("w", encoding="utf-8") as f:
            f.write(json.dumps(serializable_result, indent=2))
    except Exception:
        _logger.exception("An exception was thrown while running ansible-lint.")
        return -1

    return 0


def _run_cli_entrypoint() -> None:  # noqa: C901
    """Invoke the main entrypoint with current CLI args.

    This function also processes the runtime exceptions.
    """
    try:
        argv = sys.argv[1:]
        options = parse_args(argv)
        return_code = main(argv)
        if return_code != 0:
            sys.exit(return_code)
    except OSError as exc:
        # NOTE: Only "broken pipe" is acceptable to ignore
        if exc.errno != errno.EPIPE:  # pragma: no cover
            raise
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(RC.EXIT_CONTROL_C)
    except RuntimeError as exc:  # pragma: no cover
        raise SystemExit(exc) from exc

    # If ansible-lint was executed successfully, run the sage pipeline.
    options.ari_kb_data_dir = os.getenv("ARI_KB_DATA_DIR", None)
    if options.url:
        options.repo_name = Downloader.get_repo_name(options.url)
        options.dir = str(Path(options.out_dir) / options.repo_name)

    try:
        sage_scanning_args = {
            "ari_kb_data_dir": options.ari_kb_data_dir,
            "target_dir": options.dir,
            "output_dir": options.out_dir,
            "repo_name": options.repo_name,
            "source_type": options.source_type,
            "repo_url": options.url,
        }
        if options.verbose:
            os.environ["SAGE_LOG_LEVEL"] = "debug"

        sage = import_module("ansible_customization_data.sage")
        return_code = sage.run_pipeline(sage_scanning_args)
        if return_code is not None and return_code != 0:
            sys.exit(return_code)
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(RC.EXIT_CONTROL_C)
    except RuntimeError as exc:  # pragma: no cover
        raise SystemExit(exc) from exc

    # If the sage pipeline was executed successfully, generate the final report.
    _logger.info("Generate parser-report.txt...")
    try:
        out_path = Path(options.out_dir)
        report_main(
            [
                str(out_path / "lint-result.json"),
                str(out_path / "sarif.json"),
                str(out_path / "sage-objects.json"),
                str(out_path / "parser-report.txt"),
            ],
        )
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(RC.EXIT_CONTROL_C)
    except RuntimeError as exc:  # pragma: no cover
        raise SystemExit(exc) from exc
    sys.exit(return_code)


if __name__ == "__main__":
    _run_cli_entrypoint()
