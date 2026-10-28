"""Portable Timing Studio skill entry point (Python 3.12+, standard library only)."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys


MINIMUM_PYTHON = (3, 12)
RUNTIME_FILES = (
    'model.py', 'edit_ops.py',
    'local_window/__init__.py', 'local_window/demo.py', 'local_window/launch.py',
    'local_window/server.py', 'local_window/session_cli.py', 'local_window/store.py',
    'local_window/static/index.html', 'local_window/static/app.js', 'local_window/static/app.css',
)


def resolve_runtime(script_path=None):
    """Prefer the distributed runtime; only a source checkout uses the fallback."""
    script = Path(script_path or __file__).resolve()
    bundled = script.parent / 'runtime'
    if bundled.exists():
        root, kind = bundled, 'bundled'
    elif len(script.parents) > 3:
        root, kind = script.parents[3], 'source'
    else:
        raise RuntimeError(f'Timing Studio runtime not found beside {script}. Install the complete skill package.')
    missing = [relative for relative in RUNTIME_FILES if not (root / relative).is_file()]
    if missing:
        raise RuntimeError(f'Incomplete Timing Studio {kind} runtime at {root}: ' + ', '.join(missing))
    return root, kind


def activate_runtime(root):
    """An embedded Python may omit both cwd and the entry-point directory."""
    value = str(root)
    if value in sys.path:
        sys.path.remove(value)
    sys.path.insert(0, value)


def print_json(value, output=None):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    if output is not None:
        destination = Path(output).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding='utf-8')
    print(text, end='')


def doctor(browser=None):
    result = {
        'python': {'version': '.'.join(map(str, sys.version_info[:3])),
                   'executable': sys.executable,
                   'supported': sys.version_info[:2] >= MINIMUM_PYTHON},
        'runtime': {'available': False},
        'browser': {'found': False, 'path': None},
    }
    try:
        root, kind = resolve_runtime()
        result['runtime'] = {'available': True, 'path': str(root), 'kind': kind}
        activate_runtime(root)
        launch = importlib.import_module('local_window.launch')
        resolved = launch.find_browser(browser)
        result['browser'] = {'found': resolved is not None,
                             'path': str(resolved) if resolved else None}
    except (OSError, ValueError, RuntimeError) as exc:
        result['error'] = str(exc)
    result['ok'] = (result['python']['supported'] and result['runtime']['available']
                    and 'error' not in result)
    result['ready_for_window'] = result['ok'] and result['browser']['found']
    print_json(result)
    return 0 if result['ok'] else 1


def validate_project(project_path, output=None):
    model = importlib.import_module('model')
    prepare = importlib.import_module('local_window.store')._prepare
    source = Path(project_path).resolve()
    if output is not None and Path(output).resolve() == source:
        raise ValueError('--output must not overwrite the input project.')
    result = {'project': str(source), 'valid': False, 'errors': [], 'warnings': [],
              'compile_check': {'ok': False, 'kind': 'Verilog-A generation only; no simulator run'}}
    try:
        project = prepare(model.load_project(source.read_text(encoding='utf-8-sig')))
        result.update(model.validate(project))
        if not result['errors']:
            code = model.compile_va(project)
            result['compile_check'].update(ok=True, bytes=len(code.encode('utf-8')))
            result['valid'] = True
    except (OSError, ValueError) as exc:
        result['errors'].append(str(exc))
    print_json(result, output)
    return 0 if result['valid'] else 1


def launch_session(args):
    launch = importlib.import_module('local_window.launch')
    session = args.session.resolve()
    browser = None
    if not args.no_open:
        browser = launch.find_browser(args.browser)
        if browser is None:
            raise RuntimeError('No Edge/Chrome/Chromium browser found. Use --browser PATH or --no-open.')
    info = launch.start(session, args.project)
    if not args.no_open:
        launch.open_window(info, browser=browser, browser_data=session / 'browser-data')
    # The connection token belongs only in connection.json and the browser URL.
    print_json({'session': info['session'], 'port': info['port'], 'pid': info['pid'],
                'window_requested': not args.no_open})
    return 0


def session_command(args):
    cli = importlib.import_module('local_window.session_cli')
    argv = [str(Path(cli.__file__)), '--session', str(args.session.resolve()), args.action]
    for name in ('revision', 'file', 'output', 'label'):
        value = getattr(args, name)
        if value is not None:
            argv.extend(['--' + name, str(value)])
    previous = sys.argv
    try:
        sys.argv = argv
        cli.main()
    finally:
        sys.argv = previous
    return 0


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('doctor', help='Check Python, runtime files, and browser without starting services')
    check.add_argument('--browser', type=Path)
    validate = commands.add_parser('validate', help='Validate a project and check Verilog-A generation locally')
    validate.add_argument('--project', type=Path, required=True)
    validate.add_argument('--output', type=Path, help='Optional validation report JSON; does not export Verilog-A')
    launch = commands.add_parser('launch', help='Start/reuse a session and optionally open its browser window')
    launch.add_argument('--session', type=Path, required=True)
    launch.add_argument('--project', type=Path)
    launch.add_argument('--no-open', action='store_true')
    launch.add_argument('--browser', type=Path)
    session = commands.add_parser('session', help='Read or edit an existing session with revision checks')
    session.add_argument('--session', type=Path, required=True)
    session.add_argument('action', choices=['state', 'replace', 'edit', 'export', 'stop'])
    session.add_argument('--revision', type=int)
    session.add_argument('--file', type=Path, help='Project JSON for replace; operation JSON for edit')
    session.add_argument('--output', type=Path, help='Write response JSON to an explicit local file')
    session.add_argument('--label', default=None)
    demo = commands.add_parser('demo', help='Write the existing six-signal demonstration to an explicit path')
    demo.add_argument('--output', type=Path, required=True)
    return parser


def main(argv=None):
    # Installed skill directories can be read-only; doctor must leave no caches.
    sys.dont_write_bytecode = True
    # Embedded Python ignores PYTHONIOENCODING; make piped JSON portable too.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.command == 'doctor':
        return doctor(args.browser)
    if sys.version_info[:2] < MINIMUM_PYTHON:
        parser.error('Timing Studio requires Python 3.12 or newer.')
    if args.command == 'session':
        if args.action in ('edit', 'replace', 'export') and args.revision is None:
            parser.error('Editing and exporting require the --revision just read from session state.')
        if args.action in ('edit', 'replace') and args.file is None:
            parser.error('Editing and replacing require --file.')
    try:
        root, _ = resolve_runtime()
        activate_runtime(root)
        if args.command == 'validate':
            return validate_project(args.project, args.output)
        if args.command == 'launch':
            return launch_session(args)
        if args.command == 'session':
            return session_command(args)
        if args.command == 'demo':
            demo = importlib.import_module('local_window.demo').make_demo()
            print_json(demo, args.output)
            return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print_json({'error': str(exc)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
