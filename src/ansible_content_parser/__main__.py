import argparse
import contextlib
import errno
import json
import os
import sys

from ansiblelint.constants import RC
from ansiblelint.file_utils import Lintable

from .lint import ansiblelint_main
from pathlib import Path


class LintableDict(dict):
    def __init__(self, lintable: Lintable):
        self['base_kind'] = str(lintable.base_kind)
        self['dir'] = str(lintable.dir)
        self['exc'] = None if lintable.exc is None else str(lintable.exc)
        self['filename'] = str(lintable.filename)
        self['kind'] = str(lintable.kind)
        self['name'] = str(lintable.name)
        self['parent'] = None if lintable.parent is None else str(lintable.parent.name)
        self['role'] = str(lintable.role)
        self['stop_processing'] = bool(lintable.stop_processing)
        self['updated'] = bool(lintable.updated)


# https://stackoverflow.com/questions/6194499/pushd-through-os-system
@contextlib.contextmanager
def pushd(new_dir):
    previous_dir = os.getcwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)


def execute_ansiblelint(argv, work_dir):
    with pushd(work_dir):
        result, mark_as_success = ansiblelint_main(argv)
        serializable_result = {
            "files": [LintableDict(lintable) for lintable in result.files],
        }
    return serializable_result

def parse_args(argv):
    parser = argparse.ArgumentParser(description="TODO")
    parser.add_argument("-d", "--dir", help='root direcotry for scan')
    parser.add_argument("-t", "--source-type", help='source type (e.g."GitHub-RHIBM")')
    parser.add_argument("-r", "--repo-name", help='repo name (e.g."IBM/Ansible-OpsnShift-Provisioning")')
    parser.add_argument("-o", "--out-dir", default="", help="output directory for the rule evaluation result")
    parser.add_argument("-v", "--verbose",  action='store_true', help="explain what is being done")
    parser.add_argument("-u", "--url", help="repository URL")
    args = parser.parse_args(argv)
    if not (args.out_dir and (args.dir or args.url)) :
        parser.print_help()
        return None
    return args

def main(argv) -> int:
    args = parse_args(argv)
    if args is None:
        exit(1)
    print(args)

    if args.url:
        from .downloader import Downloader
        d = Downloader(args.out_dir)
        repo_name = d.extract(args.url)
        args.dir = os.path.join(args.out_dir, repo_name)

    sarif_file = os.path.join(args.out_dir, 'sarif.json')

    argv = ['__DUMMY__', '--sarif-file', sarif_file, '--write']
    if args.verbose:
        argv.append('-v')

    try:
        serializable_result = execute_ansiblelint(argv, args.dir)
        if not os.path.exists(args.out_dir):
            os.makedirs(args.out_dir)
        with open(os.path.join(args.out_dir, 'lint-result.json'), 'w') as f:
            f.write(json.dumps(serializable_result, indent=2))
        return 0
    except Exception as exc:
        print(str(exc), sys.stderr)
        return -1

def _run_cli_entrypoint() -> None:
    """Invoke the main entrypoint with current CLI args.

    This function also processes the runtime exceptions.
    """
    try:
        argv = sys.argv[1:]
        args = parse_args(argv)
        rc = main(argv)
        if rc != 0:
            sys.exit(rc)
    except OSError as exc:
        # NOTE: Only "broken pipe" is acceptable to ignore
        if exc.errno != errno.EPIPE:  # pragma: no cover
            raise
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(RC.EXIT_CONTROL_C)
    except RuntimeError as exc:  # pragma: no cover
        raise SystemExit(exc) from exc

    # If ansible-lint was executed successfully, run the sage pipeline.
    args.ari_kb_data_dir = os.getenv("ARI_KB_DATA_DIR", None)
    if args.url:
        from .downloader import Downloader
        args.repo_name = Downloader.get_repo_name(args.url)
        args.dir = os.path.join(args.out_dir, args.repo_name)

    try:
        sage_scanning_args = {
            "ari_kb_data_dir": args.ari_kb_data_dir,
            "target_dir": args.dir,
            "output_dir": args.out_dir,
            "repo_name": args.repo_name,
            "source_type": args.source_type,
            "repo_url": args.url,
        }
        if args.verbose:
            os.environ['SAGE_LOG_LEVEL'] = 'debug'

        from importlib import import_module
        sage = import_module('ansible_customization_data.sage')
        rc = sage.run_pipeline(sage_scanning_args)
        if rc is not None and rc != 0: # TODO
            sys.exit(rc)
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(RC.EXIT_CONTROL_C)
    except RuntimeError as exc:  # pragma: no cover
        raise SystemExit(exc) from exc

    # If the sage pipeline was executed successfully, generate the final report.
    print('Generate parser-report.txt...')
    try:
        from .report import main as report_main
        report_main([
            os.path.join(args.out_dir, 'lint-result.json'),
            os.path.join(args.out_dir, 'sarif.json'),
            os.path.join(args.out_dir, 'sage-objects.json'),
            os.path.join(args.out_dir, 'parser-report.txt')
        ])
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(RC.EXIT_CONTROL_C)
    except RuntimeError as exc:  # pragma: no cover
        raise SystemExit(exc) from exc
    sys.exit(rc)

if __name__ == '__main__':
    # main(sys.argv[1:])
    _run_cli_entrypoint()
