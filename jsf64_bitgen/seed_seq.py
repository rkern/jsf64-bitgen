#!/usr/bin/env python
""" PRNG seed sequence implementation for np.random.

Vendorized for demonstration purposes.

The algorithms are derived from Melissa E. O'Neill's C++11 `std::seed_seq`
implementation, as it has a lot of nice properties that we want.

https://gist.github.com/imneme/540829265469e673d045
http://www.pcg-random.org/posts/developing-a-seed_seq-alternative.html

The MIT License (MIT)

Copyright (c) 2015 Melissa E. O'Neill
Copyright (c) 2019 Robert Kern

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from itertools import cycle
import re
import secrets

import numpy as np


DECIMAL_RE = re.compile(r'[0-9]+')

DEFAULT_POOL_SIZE = 4
INIT_A = np.uint32(0x43b0d7e5)
MULT_A = np.uint32(0x931e8875)
INIT_B = np.uint32(0x8b51f9dd)
MULT_B = np.uint32(0x58f38ded)
MIX_MULT_L = np.uint32(0xca01f9dd)
MIX_MULT_R = np.uint32(0x4973f715)
XSHIFT = np.dtype(np.uint32).itemsize * 8 // 2
MASK32 = 0xFFFFFFFF


def _int_to_uint32_array(n):
    arr = []
    if n < 0:
        raise ValueError("expected non-negative integer")
    if n == 0:
        arr.append(np.uint32(n))
    while n > 0:
        arr.append(np.uint32(n & MASK32))
        n >>= 32
    return np.array(arr, dtype=np.uint32)


def coerce_to_uint32_array(x):
    """ Coerce an input to a uint32 array.

    If a `uint32` array, pass it through directly.
    If a non-negative integer, then break it up into `uint32` words, lowest
    bits first.
    If a string starting with "0x", then interpret as a hex integer, as above.
    If a string of decimal digits, interpret as a decimal integer, as above.
    If a sequence of ints or strings, interpret each element as above and
    concatenate.

    Note that the handling of `int64` or `uint64` arrays are not just
    straightforward views as `uint32` arrays. If an element is small enough to
    fit into a `uint32`, then it will only take up one `uint32` element in the
    output. This is to make sure that the interpretation of a sequence of
    integers is the same regardless of numpy's default integer type, which
    differs on different platforms.

    Parameters
    ----------
    x : int, str, sequence of int or str

    Returns
    -------
    seed_array : uint32 array

    Examples
    --------
    >>> import numpy as np
    >>> from seed_seq import coerce_to_uint32_array
    >>> coerce_to_uint32_array(12345)
    array([12345], dtype=uint32)
    >>> coerce_to_uint32_array('12345')
    array([12345], dtype=uint32)
    >>> coerce_to_uint32_array('0x12345')
    array([74565], dtype=uint32)
    >>> coerce_to_uint32_array([12345, '67890'])
    array([12345, 67890], dtype=uint32)
    >>> coerce_to_uint32_array(np.array([12345, 67890], dtype=np.uint32))
    array([12345, 67890], dtype=uint32)
    >>> coerce_to_uint32_array(np.array([12345, 67890], dtype=np.int64))
    array([12345, 67890], dtype=uint32)
    >>> coerce_to_uint32_array([12345, 0x10deadbeef, 67890, 0xdeadbeef])
    array([     12345, 3735928559,         16,      67890, 3735928559],
          dtype=uint32)
    >>> coerce_to_uint32_array(1234567890123456789012345678901234567890)
    array([3460238034, 2898026390, 3235640248, 2697535605,          3],
          dtype=uint32)
    """
    if isinstance(x, np.ndarray) and x.dtype == np.dtype(np.uint32):
        return x.copy()
    elif isinstance(x, str):
        if x.startswith('0x'):
            x = int(x, base=16)
        elif DECIMAL_RE.match(x):
            x = int(x)
        else:
            raise ValueError("unrecognized seed string")
    if isinstance(x, (int, np.integer)):
        return _int_to_uint32_array(x)
    else:
        if len(x) == 0:
            return np.array([], dtype=np.uint32)
        # Should be a sequence of interpretable-as-ints. Convert each one to
        # a uint32 array and concatenate.
        subseqs = [coerce_to_uint32_array(v) for v in x]
        return np.concatenate(subseqs)


class SeedSequence():
    def __init__(self, entropy=None, program_entropy=None, spawn_key=(),
                 pool_size=DEFAULT_POOL_SIZE):
        if pool_size < DEFAULT_POOL_SIZE:
            raise ValueError("The size of the entropy pool should be at least "
                             f"{DEFAULT_POOL_SIZE}")
        if entropy is None:
            entropy = secrets.randbits(pool_size * 32)
        self.entropy = entropy
        self.program_entropy = program_entropy
        self.spawn_key = tuple(spawn_key)
        self.pool_size = pool_size

        self.pool = np.zeros(pool_size, dtype=np.uint32)
        self.n_children_spawned = 0
        self.mix_entropy(self.get_assembled_entropy())

    def __repr__(self):
        lines = [
            f'{type(self).__name__}(',
            f'    entropy={self.entropy!r},',
        ]
        # Omit some entries if they are left as the defaults in order to
        # simplify things.
        if self.program_entropy is not None:
            lines.append(f'    program_entropy={self.program_entropy!r},')
        if self.spawn_key:
            lines.append(f'    spawn_key={self.spawn_key!r},')
        if self.pool_size != DEFAULT_POOL_SIZE:
            lines.append(f'    pool_size={self.pool_size!r},')
        lines.append(')')
        text = '\n'.join(lines)
        return text

    def mix_entropy(self, entropy_array):
        """ Mix in the given entropy.

        Parameters
        ----------
        entropy_array : 1D uint32 array
        """
        with np.errstate(over='ignore'):
            hash_const = INIT_A

            def hash(value):
                # We are modifying the multiplier as we go along.
                nonlocal hash_const

                value ^= hash_const
                hash_const *= MULT_A
                value *= hash_const
                value ^= value >> XSHIFT
                return value

            def mix(x, y):
                result = MIX_MULT_L * x - MIX_MULT_R * y
                result ^= result >> XSHIFT
                return result

            mixer = self.pool
            # Add in the entropy up to the pool size.
            for i in range(len(mixer)):
                if i < len(entropy_array):
                    mixer[i] = hash(entropy_array[i])
                else:
                    # Our pool size is bigger than our entropy, so just keep
                    # running the hash out.
                    mixer[i] = hash(np.uint32(0))

            # Mix all bits together so late bits can affect earlier bits.
            for i_src in range(len(mixer)):
                for i_dst in range(len(mixer)):
                    if i_src != i_dst:
                        mixer[i_dst] = mix(mixer[i_dst], hash(mixer[i_src]))

            # Add any remaining entropy, mixing each new entropy word with each
            # pool word.
            for i_src in range(len(mixer), len(entropy_array)):
                for i_dst in range(len(mixer)):
                    mixer[i_dst] = mix(mixer[i_dst], hash(entropy_array[i_src]))

            # Should have modified in-place.
            assert mixer is self.pool

    def get_assembled_entropy(self):
        """ Convert and assemble all entropy sources into a uniform uint32
        array.

        Returns
        -------
        entropy_array : 1D uint32 array
        """
        # Convert run-entropy, program-entropy, and the spawn key into uint32
        # arrays and concatenate them.

        # We MUST have at least some run-entropy. The others are optional.
        assert self.entropy is not None
        run_entropy = coerce_to_uint32_array(self.entropy)
        if self.program_entropy is None:
            # We *could* make `coerce_to_uint32_array(None)` handle this case,
            # but that would make it easier to misuse, a la
            # `coerce_to_uint32_array([None, 12345])`
            program_entropy = np.array([], dtype=np.uint32)
        else:
            program_entropy = coerce_to_uint32_array(self.program_entropy)
        spawn_entropy = coerce_to_uint32_array(self.spawn_key)
        entropy_array = np.concatenate([run_entropy, program_entropy,
                                        spawn_entropy])
        return entropy_array

    def generate_state(self, n_words, dtype=np.uint32):
        """ Return the requested number of words for PRNG seeding.

        Parameters
        ----------
        n_words : int
        dtype : np.uint32 or np.uint64, optional
            The size of each word. This should only be either `uint32` or
            `uint64`. Strings (`'uint32'`, `'uint64'`) are fine. Note that
            requesting `uint64` will draw twice as many bits as `uint32` for
            the same `n_words`. This is a convenience for `BitGenerator`s that
            express their states as `uint64` arrays.

        Returns
        -------
        state : uint32 or uint64 array, shape=(n_words,)
        """
        with np.errstate(over='ignore'):
            hash_const = INIT_B
            out_dtype = np.dtype(dtype)
            if out_dtype == np.dtype(np.uint32):
                pass
            elif out_dtype == np.dtype(np.uint64):
                n_words *= 2
            else:
                raise ValueError("only support uint32 or uint64")
            state = np.zeros(n_words, dtype=np.uint32)
            src_cycle = cycle(self.pool)
            for i_dst in range(n_words):
                data_val = next(src_cycle)
                data_val ^= hash_const
                hash_const *= MULT_B
                data_val *= hash_const
                data_val ^= data_val >> XSHIFT
                state[i_dst] = data_val
            if out_dtype == np.dtype(np.uint64):
                state = state.view(np.uint64)
        return state

    def spawn(self, n_children):
        """ Spawn a number of child `SeedSequence`s by extending the
        `spawn_key`.

        Parameters
        ----------
        n_children : int

        Returns
        -------
        seqs : list of `SeedSequence`s
        """
        seqs = []
        for i in range(self.n_children_spawned,
                       self.n_children_spawned + n_children):
            seqs.append(SeedSequence(
                self.entropy,
                program_entropy=self.program_entropy,
                spawn_key=self.spawn_key + (i,),
                pool_size=self.pool_size,
            ))
        return seqs


# Register with np.random.ISpawnableSeedSequence, if it's available.
if hasattr(np.random, 'ISpawnableSeedSequence'):
    np.random.ISpawnableSeedSequence.register(SeedSequence)
