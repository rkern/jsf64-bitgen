#!/usr/bin/env python
import abc
from itertools import cycle
import re
import secrets
import sys

import numpy as np

from jsf64_bitgen.jsf64 import JSF64
from jsf64_bitgen.seed_seq import SeedSequence as MySeedSequenceNotNumpys


def gen_interleaved_bytes(gens, n_per_gen=1024):
    output_dtype = np.uint64
    while True:
        draws = [g.integers(np.iinfo(output_dtype).max, dtype=output_dtype,
                            endpoint=True, size=n_per_gen)
                 for g in gens]
        interleaved = np.column_stack(draws).ravel()
        bytes_chunk = bytes(interleaved.data)
        yield bytes_chunk


def bitgen_interleaved_bytes(bitgens, n_per_gen=1024):
    while True:
        draws = [g.random_raw(n_per_gen)
                 for g in bitgens]
        interleaved = np.column_stack(draws).ravel()
        bytes_chunk = bytes(interleaved.data)
        yield bytes_chunk


def main():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-s', '--seed', type=int, help='The root seed.')
    parser.add_argument('-d', '--depth', type=int, default=4,
                        help='The depth of the spawn tree.')
    parser.add_argument('-p', '--ply', type=int, default=8,
                        help='The number of spawns at each level.')
    parser.add_argument('-g', '--use-generator', action='store_true',
                        help='Use Generator if available.')

    args = parser.parse_args()
    if args.use_generator and not hasattr(np.random, 'Generator'):
        raise NotImplementedError("must use numpy 1.17+ to test Generator")

    root = MySeedSequenceNotNumpys(args.seed)
    print(f"seed = {root.entropy}", file=sys.stderr)

    # Generate `ply ** depth` leaf `SeedSequences` by the `spawn()` API.
    leaves = []
    nodes = [root]
    for i in range(args.depth):
        children = []
        for node in nodes:
            children.extend(node.spawn(args.ply))
        nodes = children
    bitgens = [JSF64(ss) for ss in nodes]
    if args.use_generator:
        gens = [np.random.Generator(bg) for bg in bitgens]
        for chunk in gen_interleaved_bytes(gens):
            sys.stdout.buffer.write(chunk)
    else:
        for chunk in bitgen_interleaved_bytes(bitgens):
            sys.stdout.buffer.write(chunk)

if __name__ == '__main__':
    try:
        main()
    except (BrokenPipeError, IOError, KeyboardInterrupt):
        print('Exiting.', file=sys.stderr)
