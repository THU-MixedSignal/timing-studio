"""Revision checked edits with immutable snapshots and an atomic HEAD file."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

import edit_ops
import model
from local_window.demo import make_demo


class ConflictError(ValueError):
    def __init__(self, state):
        super().__init__('时序已有新版本。请先查看最新版本，再决定如何处理这次修改。')
        self.state = state


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _integer(value, low=0, high=model.MAX_FS):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'需要 {low}–{high} 范围内的整数。')
    return value


def _prepare(project):
    result = model.migrate_project(project)
    identities = [signal['id'] for signal in result['signals']]
    view = result.setdefault('view', {})
    if not isinstance(view, dict):
        raise ValueError('显示设置应为对象。')
    view.setdefault('row_height', 64)
    _integer(view['row_height'], 36, 128)
    order = view.get('signal_order', identities)
    if (not isinstance(order, list) or any(not isinstance(x, str) for x in order)
            or len(order) != len(set(order)) or set(order) != set(identities)):
        raise ValueError('显示顺序必须包含每路信号且不能重复。')
    view['signal_order'] = list(order)
    # Reject metadata that JSON cannot round-trip, before touching persistence.
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def _merge(signal):
    items = []
    for item in signal['items']:
        if items and items[-1]['level'] == item['level']:
            items[-1]['duration_fs'] += item['duration_fs']
        else:
            items.append(deepcopy(item))
    signal['items'] = items


class SessionStore:
    """All writers, including assistant updates, go through this live store."""

    def __init__(self, directory, initial=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / 'revisions').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.head_path = self.directory / 'head.json'
        if self.head_path.exists():
            self.head = json.loads(self.head_path.read_text(encoding='utf-8'))
            self.project = _prepare(self._read_snapshot(self.head['snapshot']))
        else:
            self.project = _prepare(initial if initial is not None else make_demo())
            self.head = {'revision': 0, 'snapshot': '', 'undo': [], 'redo': [],
                         'saved_at': '', 'last_change': '', 'changed_signal_ids': []}
            self._commit(self.project, '初始时序', [], [])

    def _read_snapshot(self, name):
        path = (self.directory / name).resolve()
        if not path.is_relative_to(self.directory / 'revisions'):
            raise ValueError('版本路径超出当前工程目录。')
        return json.loads(path.read_text(encoding='utf-8'))

    def state(self):
        with self.lock:
            return {'revision': self.head['revision'], 'project': deepcopy(self.project),
                    'saved_at': self.head['saved_at'],
                    'can_undo': bool(self.head['undo']), 'can_redo': bool(self.head['redo']),
                    'last_change': self.head['last_change'],
                    'changed_signal_ids': list(self.head.get('changed_signal_ids', [])),
                    'session_name': self.directory.name,
                    'project_path': str(self.directory / self.head['snapshot']),
                    'warnings': model.validate(self.project)['warnings']}

    def _check(self, revision):
        _integer(revision)
        if revision != self.head['revision']:
            raise ConflictError(self.state())

    def _commit(self, project, label, undo=None, redo=None):
        prepared = _prepare(project)
        previous = {s['id']: s for s in self.project.get('signals', [])}
        changed = [s['id'] for s in prepared['signals'] if s != previous.get(s['id'])]
        revision = self.head['revision'] + 1
        # A unique suffix also protects a snapshot orphaned by a failed HEAD write.
        snapshot = f'revisions/r{revision:06d}-{uuid4().hex[:8]}.timing.json'
        head = {
            'revision': revision, 'snapshot': snapshot,
            'undo': (self.head['undo'] + [self.head['snapshot']])[-40:] if undo is None else undo,
            'redo': [] if redo is None else redo,
            'saved_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'last_change': str(label)[:500], 'changed_signal_ids': changed,
        }
        atomic_json(self.directory / snapshot, prepared)
        # This replace is the only commit point. Readers never see a partial project.
        atomic_json(self.head_path, head)
        self.project, self.head = prepared, head
        return self.state()

    def replace(self, revision, project, label='助手更新时序'):
        with self.lock:
            self._check(revision)
            prepared = _prepare(project)
            if prepared == self.project:
                return self.state()
            return self._commit(prepared, label)

    def edit(self, revision, operation):
        with self.lock:
            self._check(revision)
            if not isinstance(operation, dict):
                raise ValueError('编辑操作应为对象。')
            op = operation.get('op')
            if op in ('undo', 'redo'):
                source = self.head[op]
                if not source:
                    return self.state()
                target = self._read_snapshot(source[-1])
                if op == 'undo':
                    return self._commit(target, '撤销上次修改', source[:-1],
                                        (self.head['redo'] + [self.head['snapshot']])[-40:])
                return self._commit(target, '重做修改',
                                    (self.head['undo'] + [self.head['snapshot']])[-40:], source[:-1])
            draft = deepcopy(self.project)
            signal = None
            if op in ('boundary', 'paint', 'toggle', 'rename', 'delete_signal'):
                signal = next((s for s in draft['signals']
                               if s['id'] == operation.get('signal_id')), None)
                if signal is None:
                    raise ValueError('信号不存在。')
            if op in ('boundary', 'paint', 'toggle'):
                if op in ('boundary', 'toggle'):
                    if not any(i['id'] == operation.get('item_id') for i in signal['items']):
                        raise ValueError('该片段已改变，请重新选择边沿。')
                if op == 'boundary':
                    _integer(operation.get('new_end_fs'), 0, draft['period_fs'])
                if op == 'paint':
                    _integer(operation.get('start_fs'), 0, draft['period_fs'])
                    _integer(operation.get('end_fs'), 0, draft['period_fs'])
                    _integer(operation.get('level'), 0, 1)
                edit_ops.apply_edit(signal, operation, draft['period_fs'])
                _merge(signal)
                label = {'boundary': '移动边沿', 'paint': '绘制电平', 'toggle': '翻转电平'}[op]
                label = signal['name'] + ' · ' + label
            elif op == 'view':
                for key in ('row_height', 'signal_order'):
                    if key in operation:
                        draft['view'][key] = deepcopy(operation[key])
                label = '调整波形显示'
            elif op == 'settings':
                values = operation.get('values')
                if not isinstance(values, dict):
                    raise ValueError('工程设置应为对象。')
                unit = values.get('unit', draft['unit'])
                if unit not in model.UNITS:
                    raise ValueError('时间单位无效。')
                for key in ('module_name', 'vlow', 'vhigh', 'cycles', 'unit'):
                    if key in values:
                        draft[key] = values[key]
                for key, target in (('period', 'period_fs'), ('rise', 'rise_fs'), ('fall', 'fall_fs')):
                    if key in values:
                        draft[target] = model.parse_time(values[key], unit)
                # Shortening a held tail is harmless; never silently cut a real edge.
                if draft['period_fs'] < self.project['period_fs']:
                    for s in draft['signals']:
                        length = model.signal_duration(s)
                        if length > draft['period_fs']:
                            excess = length - draft['period_fs']
                            if s['items'][-1]['duration_fs'] <= excess:
                                raise ValueError(s['name'] + ' 存在超出新周期的边沿，请先调整波形。')
                            s['items'][-1]['duration_fs'] -= excess
                label = '更新工程参数'
            elif op == 'rename':
                old = signal['name']
                signal['name'] = operation.get('name')
                label = f'信号改名：{old}'
            elif op == 'add_signal':
                identity = 's_' + uuid4().hex[:12]
                level = _integer(operation.get('level', 0), 0, 1)
                draft['signals'].append({'id': identity, 'name': operation.get('name'),
                                         'items': [edit_ops.segment(level, draft['period_fs'])]})
                draft['view']['signal_order'].append(identity)
                label = '增加信号'
            elif op == 'delete_signal':
                draft['signals'] = [s for s in draft['signals'] if s['id'] != signal['id']]
                draft['view']['signal_order'].remove(signal['id'])
                label = '删除信号：' + signal['name']
            else:
                raise ValueError('未知编辑操作。')
            if draft == self.project:
                return self.state()
            return self._commit(draft, label)

    def relations(self, threshold='200p', signal_id=None, kind='all'):
        with self.lock:
            result = model.analyze_edges(self.project,
                model.parse_time(threshold, self.project['unit']), limit=200,
                signal_id=signal_id or None, relation_kind=kind)
            result['revision'] = self.head['revision']
            return result

    def va(self):
        with self.lock:
            return {'revision': self.head['revision'], 'code': model.compile_va(self.project),
                    'filename': self.project['module_name'] + '.va'}

    def export(self, revision):
        with self.lock:
            self._check(revision)
            directory = self.directory / 'exports' / f'r{revision:06d}'
            directory.mkdir(parents=True, exist_ok=True)
            name = self.project['module_name']
            va_path = directory / (name + '.va')
            # Identical-revision exports are identical by construction.
            temporary = va_path.with_name(va_path.name + '.' + uuid4().hex + '.tmp')
            with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(model.compile_va(self.project))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, va_path)
            project_path = directory / (name + '.timing.json')
            atomic_json(project_path, self.project)
            return {'path': str(va_path), 'project_path': str(project_path),
                    'filename': va_path.name, 'revision': revision}
