"""Plot newly generated formal-runner JSONL; never reads historical data implicitly.

Panels are separated by scale, load, horizon and deadline profile. This is a
portable comparison plot, not a reconstruction of the manuscript's layouts.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from record_compatibility import normalize_record
from algorithm_names import SHORT_NAMES, algorithm_label
import re
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


DISPLAY = SHORT_NAMES


def load_groups(paths):
    groups = defaultdict(lambda: defaultdict(dict))
    fingerprints = {}
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = normalize_record(json.loads(line))
            key = row['key']
            context = (key['scale'], key['load_label'], key['time_slots'], row.get('deadline_profile') or 'default')
            method = key['algorithm'] + (':' + str(key['variant_id']) if key.get('variant_id') else '')
            seed = key['formal_seed']
            if seed in groups[context][method]:
                raise ValueError('Duplicate method/seed/scenario at {}:{}'.format(path, line_number))
            if row.get('error_type') or row.get('path_validity') is not True or row.get('capacity_validity') is not True:
                raise ValueError('Invalid or failed run at {}:{}; resolve explicitly before plotting'.format(path, line_number))
            meta = row.get('dwcg_metadata') or {}
            if (meta.get('hard_wall_clock_limit_reached') or meta.get('run_hard_wall_clock_limit_reached')
                    or meta.get('internal_phase_limit_reached')
                    or meta.get('termination_trigger') in ('INTERNAL_PHASE_LIMIT', 'HARD_TIME_LIMIT', 'GLOBAL_HARD_TIME_LIMIT')):
                raise ValueError('Interrupted execution requires explicit analysis before plotting at {}:{}'.format(path, line_number))
            n, accepted, runtime = row['total_flow_count'], row['accepted_flow_count'], row['total_end_to_end_runtime_seconds']
            if not n or accepted is None or not 0 <= accepted <= n or not math.isfinite(runtime) or runtime <= 0:
                raise ValueError('Invalid admission/runtime values at {}:{}'.format(path, line_number))
            fingerprint = row.get('full_instance_fingerprint')
            if not fingerprint:
                raise ValueError('Missing instance fingerprint')
            identity = (context, seed)
            if identity in fingerprints and fingerprints[identity] != fingerprint:
                raise ValueError('Unpaired instance fingerprints: {}'.format(identity))
            fingerprints[identity] = fingerprint
            groups[context][method][seed] = (100.0 * accepted / n, runtime)
    if not groups:
        raise ValueError('No input records')
    for context, methods in groups.items():
        seed_sets = {tuple(sorted(rows)) for rows in methods.values()}
        if len(seed_sets) != 1:
            raise ValueError('Incomplete paired seed sets for {}'.format(context))
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', nargs='+', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    groups = load_groups(args.input)
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Output directory must be new or empty')
    args.output.mkdir(parents=True, exist_ok=True)
    for context, methods in sorted(groups.items()):
        labels = sorted(methods)
        fig, axes = plt.subplots(1, 2, figsize=(max(9, len(labels) * 2), 4.8))
        for metric, ax in enumerate(axes):
            samples = [[methods[label][seed][metric] for seed in sorted(methods[label])] for label in labels]
            ax.boxplot(samples, labels=[algorithm_label(label, short=True) for label in labels], showmeans=True, meanline=True)
            for position, values in enumerate(samples, 1):
                ax.scatter([position] * len(values), values, alpha=.55, s=18)
            ax.tick_params(axis='x', rotation=25)
            ax.set_ylabel('Admission (%)' if metric == 0 else 'End-to-end runtime (s)')
            if metric == 0:
                ax.set_ylim(0, 102)
            else:
                ax.set_yscale('log')
            ax.grid(axis='y', alpha=.2)
        fig.suptitle('{} / {} / T={} / {} — new outputs (n={} per method)'.format(
            *context, len(methods[labels[0]])))
        fig.tight_layout()
        name = re.sub(r'[^A-Za-z0-9_.-]+', '_', '_'.join(map(str, context)))
        fig.savefig(args.output / (name + '.pdf'), bbox_inches='tight')
        fig.savefig(args.output / (name + '.png'), dpi=160, bbox_inches='tight')
        plt.close(fig)
        print(context)
        for label in labels:
            values = list(methods[label].values())
            print('  {}: n={}, mean admission={:.3f}%, median runtime={:.3f}s'.format(
                label, len(values), statistics.mean(v[0] for v in values), statistics.median(v[1] for v in values)))


if __name__ == '__main__':
    main()
