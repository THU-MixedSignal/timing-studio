"""Assistant handoff API: read latest state, then apply with its revision."""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from local_window.launch import DEFAULT_SESSION, connect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', type=Path, default=DEFAULT_SESSION)
    parser.add_argument('action', choices=['state', 'replace', 'edit', 'export', 'stop'])
    parser.add_argument('--revision', type=int)
    parser.add_argument('--file', type=Path, help='Project JSON for replace; operation JSON for edit')
    parser.add_argument('--output', type=Path, help='Write state to an explicit local file')
    parser.add_argument('--label', default='助手更新时序')
    args = parser.parse_args()
    info, state = connect(args.session)
    if args.action == 'state':
        result = state
    else:
        if args.action != 'stop' and args.revision is None:
            parser.error('编辑和导出必须提供刚刚读取的 --revision；不会自动使用最新版本覆盖用户修改。')
        data = {'revision': args.revision}
        endpoint = {'replace': 'project', 'edit': 'edit', 'export': 'export', 'stop': 'shutdown'}[args.action]
        if args.action in ('replace', 'edit'):
            if not args.file:
                parser.error('需要 --file')
            content = json.loads(args.file.read_text(encoding='utf-8-sig'))
            data['project' if args.action == 'replace' else 'operation'] = content
            data['label'] = args.label
        request = Request(f"http://127.0.0.1:{info['port']}/api/{endpoint}",
                          data=json.dumps(data, ensure_ascii=False).encode('utf-8'),
                          headers={'X-Timing-Token': info['token'], 'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=15) as response:
                result = json.load(response)
        except HTTPError as exc:
            print(exc.read().decode('utf-8'), file=sys.stderr)
            raise SystemExit(2)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
        print(str(args.output.resolve()))
    else:
        print(text)


if __name__ == '__main__':
    main()
