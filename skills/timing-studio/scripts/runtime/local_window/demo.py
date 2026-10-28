"""A small editable example, including a separated pair of high pulses."""
from decimal import Decimal

def make_demo():
    ns = 1_000_000
    period = 10_000 * ns

    def track(name, spans, color):
        points = {0: 0, period: 0}
        for start, end in spans:
            points[int(Decimal(str(start)) * ns)] = 1
            points[int(Decimal(str(end)) * ns)] = 0
        times = sorted(points)
        items = []
        for i, (a, b) in enumerate(zip(times, times[1:])):
            level = points[a]
            if items and items[-1]['level'] == level:
                items[-1]['duration_fs'] += b - a
            else:
                items.append({'id': f'{name}_{i}', 'type': 'segment',
                              'level': level, 'duration_fs': b - a})
        return {'id': name, 'name': name, 'color': color, 'items': items}

    project = {
        'version': 2, 'module_name': 'timing_collaboration_demo', 'unit': 'ns',
        'period_fs': period, 'rise_fs': 200_000, 'fall_fs': 200_000,
        'vlow': 0.0, 'vhigh': 1.0, 'cycles': 0,
        'signals': [
            track('reset', [(0, 100)], '#d97657'),
            track('clk', [(200 + i * 50, 220 + i * 50) for i in range(100)], '#4675cd'),
            track('phi1', [(200 + i * 50, 220 + i * 50) for i in range(100)], '#18988b'),
            track('phi2', [(220.2 + i * 50, 249.8 + i * 50) for i in range(100)], '#8d68bf'),
            track('sample', [(600, 1000), (1400, 1800)], '#bd8d28'),
            track('done', [(5220, 5320)], '#b7659a'),
        ],
        'view': {'row_height': 64,
                 'signal_order': ['reset', 'clk', 'phi1', 'phi2', 'sample', 'done']},
        'description': '10us 大周期；复位100ns；从200ns起重复100次50ns时钟；'
                       'phi1/phi2设定边沿间隔200ps；sample两段高电平可试拖合并。',
    }
    return project
