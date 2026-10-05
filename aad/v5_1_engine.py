"""Task-independent v5 search, raw directional scores, training-only freeze.

The v5 run loop, source contract, archive and request recovery are reused.
Result ``score``/``mean`` are signed utilities; ``raw_score``/``raw_mean``
retain official scores. No normalization or AHC001 diagnostics are applied.
"""
import json
import math
import statistics
from pathlib import Path

from .io import digest, read_json, save_json
from .v5_engine import V5Engine
from .v5_policy import CYCLE, pool, valid_for_archive
from .v5_provider import V5Provider


class V51Provider(V5Provider):
    def __init__(self, *args, problem, **kwargs):
        if not isinstance(problem, str) or not problem.strip():
            raise ValueError('Task statement is required')
        self.problem = problem
        super().__init__(*args, **kwargs)

    def request(self, kind, task):
        # v5 merges task after its legacy problem field. Replace it explicitly.
        return super().request(kind, {**task, 'problem': self.problem})


class V51Engine(V5Engine):
    def __init__(self, root, provider, evaluate, train, config):
        config = {'repeats': 3, 'initial_valid_roots': 4, 'max_initial_attempts': 20,
                  'promotion_margin': 0.0, 'repeat_slack': 0.0, 'smoke_count': 6,
                  **config}
        for key in ('initial_valid_roots', 'max_initial_attempts', 'smoke_count'):
            if type(config[key]) is not int or config[key] < 1:
                raise ValueError(f'{key} must be a positive integer')
        if config['repeats'] != 3:
            raise ValueError('v5.1 requires three repeats and two positive directions')
        for key in ('promotion_margin', 'repeat_slack'):
            if not math.isfinite(config[key]) or config[key] < 0:
                raise ValueError(f'Invalid {key}')
        if config.get('direction') not in ('maximize', 'minimize'):
            raise ValueError('Expected maximize or minimize')
        if not math.isfinite(config.get('time_limit', 0)) or config['time_limit'] <= 0:
            raise ValueError('A positive task time_limit is required')
        if (not train or len({c.name for c in train}) != len(train)
                or len({c.fingerprint for c in train}) != len(train)):
            raise ValueError('Training cases must be nonempty and unique')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'state.json'
        self.provider, self.evaluate, self.train, self.config = provider, evaluate, train, config
        self.sign = 1 if config['direction'] == 'maximize' else -1
        protocol = {'version': '5.1', 'config': config,
                    'inputs': {c.name: c.fingerprint for c in train},
                    'train': [c.name for c in train], 'validation': []}
        signature = digest(json.dumps(protocol, sort_keys=True))
        if self.path.exists():
            self.s = read_json(self.path)
            if self.s['signature'] != signature:
                raise ValueError('Protocol changed')
        else:
            self.s = {'signature': signature, 'records': {}, 'events': [],
                      'root_attempts': 0, 'schedule_slot': 0, 'incumbent': None,
                      'pending': None, 'repair': None, 'validation_started': False,
                      'frozen': False}
        save_json(self.root / 'protocol.json', protocol)
        self.save()

    def mean(self, ident):
        return statistics.mean(r['mean'] for r in self.s['records'][ident]['repeats'])

    def measure(self, ident):
        record = self.s['records'][ident]
        if 'smoke' not in record:
            # Full official limit: ALE does not pass a short-budget argv to solvers.
            record['smoke'] = self.evaluate(self.source(ident),
                self.train[:self.config['smoke_count']], self.config['time_limit'], 'smoke')
            self.save()
        if not record['smoke']['valid']:
            return False
        if 'full' not in record:
            record['full'] = self.evaluate(self.source(ident), self.train,
                                           self.config['time_limit'], 'train-r0')
            self.save()
        if not record['full']['valid']:
            return False
        record.setdefault('repeats', [record['full']])
        self.save()
        return True

    def repeat(self, ident, cases, prefix, key):
        record = self.s['records'][ident]
        record.setdefault(key, [])
        while len(record[key]) < self.config['repeats']:
            replica = f'{prefix}-r{len(record[key])}'
            record[key].append(self.evaluate(self.source(ident), cases,
                                             self.config['time_limit'], replica))
            self.save()
        return all(r['valid'] for r in record[key])

    def timing(self, record):
        elapsed = statistics.mean(r['seconds'] for r in record['full']['cases'])
        limit = self.config['time_limit']
        return {'allocated_seconds': limit, 'mean_process_seconds': elapsed,
                'utilization': elapsed / limit, 'underutilized': elapsed < limit / 2,
                'instruction': 'Seek useful score-improving search, never padding or sleep. '
                               'Finishing early is fine when additional search is not useful.'}

    def feedback(self, ident):
        record = self.s['records'][ident]
        result = record.get('full', record['smoke'])
        failed = next((r for r in record.get('repeats', []) if not r['valid']), None)
        if failed:
            result = failed
        by_name = {c.name: c for c in self.train}
        rows = sorted(result['cases'], key=lambda r: (r['status'] == 'AC', r['score']))[:2]
        return {'train_mean': result['raw_mean'], 'direction': self.config['direction'],
                'failure_on_repeat': failed is not None,
                'examples': [{'name': r['name'], 'score': r['raw_score'],
                              'status': r['status'], 'input': by_name[r['name']].text,
                              'output': r.get('stdout', ''),
                              'message': r.get('message', '')[-6000:]} for r in rows],
                'timing': self.timing(record) if record.get('full', {}).get('valid') else None,
                'target': 'Improve mean raw score in the stated direction; all cases must be valid.'}

    def task(self):
        records, events = self.s['records'], self.s['events']
        archive = pool(records)
        if self.s['repair']:
            parent, self.s['repair'] = self.s['repair'], None
            return {'arm': 'repair', 'parent': parent,
                    'instruction': 'Repair the demonstrated error including repeat-only failure. '
                                   'Keep intended search quality. Return the full corrected program.'}
        roots = {r['lineage'] for r in records.values() if valid_for_archive(r)}
        if not archive or (len(roots) < self.config['initial_valid_roots']
                           and self.s['root_attempts'] < self.config['max_initial_attempts']):
            if self.s['root_attempts'] >= self.config['max_initial_attempts'] and not archive:
                raise RuntimeError('No legal cold start within attempt cap')
            self.s['root_attempts'] += 1
            return {'arm': 'independent', 'parent': None,
                    'instruction': 'Invent a complete optimizer from the specification without seed code. '
                                   'Design construction and useful search; prioritize algorithm quality.'}
        slot = self.s['schedule_slot']
        self.s['schedule_slot'] += 1
        arm = CYCLE[slot % len(CYCLE)]
        if arm == 'crossover' and any(e['arm'] == arm for e in events[-7:]):
            arm = 'redesign'
        if arm not in ('independent', 'redesign'):
            recent = [e for e in events[-16:] if e['arm'] == arm][-3:]
            if len(recent) == 3 and all(e.get('parent_gain', 0) <= self.config['promotion_margin']
                                        for e in recent):
                arm = 'redesign'
        if arm == 'independent':
            return {'arm': arm, 'parent': None,
                    'instruction': 'Invent a fresh optimizer from the specification only. '
                                   'Explore beyond a single greedy local optimum.'}
        parent = archive[0] if slot % 4 != 3 else archive[(slot // 4) % len(archive)]
        recent_runtime = [i for i, e in enumerate(events)
                          if e['arm'] == 'runtime' and e.get('parent') == parent]
        if self.timing(records[parent])['underutilized'] and (
                not recent_runtime or len(events) - recent_runtime[-1] >= 8):
            arm = 'runtime'
        directions = {
            'local': 'Address one measured weakness. Up to six coordinated edits across helpers '
                     'and call sites are allowed. Preserve unrelated working mechanisms.',
            'redesign': 'Escape representation and reachable-state limitations. Make a substantive '
                        'structural redesign retaining useful parent mechanisms. Return a complete program.',
            'runtime': 'Diagnose early termination and reachable-state limits. Design score-improving '
                       'continuation, not padding, sleeps or repeated identical work. Finishing early '
                       'is fine if no useful search exists; explain in comments. Return a complete program.',
            'crossover': 'Integrate a complementary mechanism from the donor. Related parts may change '
                         'together. Do not switch whole solvers, average outputs, or route known cases.'}
        task = {'arm': arm, 'parent': parent, 'instruction': directions[arm]}
        if arm == 'crossover':
            others = [k for k in archive if records[k]['lineage'] != records[parent]['lineage']]
            if others:
                scores = {r['name']: r['score'] for r in records[parent]['full']['cases']}
                donor = max(others, key=lambda k: sum(max(0, r['score'] - scores[r['name']])
                                                     for r in records[k]['full']['cases']))
                if any(r['score'] > scores[r['name']] for r in records[donor]['full']['cases']):
                    task['donor'] = donor
            if 'donor' not in task:
                task.update(arm='redesign', instruction=directions['redesign'])
        return task

    def qualify(self, ident, pending):
        record = self.s['records'][ident]
        if not self.measure(ident):
            pending['status'] = 'invalid_candidate'
        else:
            incumbent = self.s['incumbent']
            promising = (incumbent is None or pending['parent'] is None or
                         record['full']['mean'] >= self.mean(incumbent) - self.config['repeat_slack'])
            if not promising:
                pending['status'] = 'archived'
                return
            if not self.repeat(ident, self.train, 'train', 'repeats'):
                pending['status'] = 'invalid_repeat'
            else:
                gains = [] if incumbent is None else [a['mean'] - b['mean'] for a, b in zip(
                    record['repeats'], self.s['records'][incumbent]['repeats'], strict=True)]
                if gains:
                    pending['confirmation'] = {'mean_gain': statistics.mean(gains), 'repeat_gains': gains}
                if incumbent is None or (statistics.mean(gains) > self.config['promotion_margin']
                                         and sum(g > 0 for g in gains) >= 2):
                    self.s['incumbent'] = ident
                    pending['status'] = 'incumbent_promoted'
                else:
                    pending['status'] = 'archived'
                return
        if pending['arm'] != 'repair':
            self.s['repair'] = ident

    def finalize(self):
        if self.s['pending']:
            if self.s['pending'].get('status') != 'budget_exhausted':
                raise ValueError('Pending experiment')
            self.s['events'].append(dict(self.s['pending']))
            self.s['pending'] = None
        winner = self.s.get('champion') if self.s['frozen'] else self.s['incumbent']
        if not winner:
            raise ValueError('No valid repeated incumbent')
        record = self.s['records'][winner]
        if len(record.get('repeats', [])) != 3 or not valid_for_archive(record):
            raise ValueError('Champion must have three valid training repeats')
        code = self.source(winner)
        self.s.update(frozen=True, development_closed=True, champion=winner)
        self.save()
        (self.root / 'best.cpp').write_text(code, encoding='utf-8')
        if not (self.root / 'report.json').exists():
            ledger = read_json(self.root / 'llm/ledger.json') if (self.root / 'llm/ledger.json').exists() else []
            save_json(self.root / 'report.json', {
                'version': '5.1', 'champion': winner, 'direction': self.config['direction'],
                'train_mean': self.sign * self.mean(winner), 'train_count': len(self.train),
                'requests': self.provider.requests if self.provider else len(ledger),
                'private_evaluated': False, 'selection': 'training mean and two-of-three repeat gate'})
        return winner
