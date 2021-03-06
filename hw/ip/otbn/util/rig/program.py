# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

import random
from typing import Dict, List, Optional, TextIO, Tuple

from shared.insn_yaml import Insn


class ProgInsn:
    '''An object representing a single instruction in the generated program

    self.insn is the instruction (as defined in insns.yml).

    self.operands has an integer value for each operand. Register operands are
    represented by number, so x3 is 3, for example. Immediate operands are
    represented unsigned. So an 8-bit signed immediate with value -1 would be
    passed as 0xff.

    self.lsu_info is non-None if (and only if) the instruction is an LSU
    instruction. In this case, it's a pair (mem_type, addr). mem_type is the
    memory type (a key for Model._known_mem). addr is the target address of the
    LSU instruction (it's much easier to store it explicitly than to grovel
    around in the model to figure it out again from register values)

    '''
    def __init__(self,
                 insn: Insn,
                 operands: List[int],
                 lsu_info: Optional[Tuple[str, int]]):
        assert len(insn.operands) == len(operands)
        assert (lsu_info is None) is (insn.lsu is None)
        self.insn = insn
        self.operands = operands
        self.lsu_info = lsu_info

    def to_json(self) -> object:
        '''Serialize to an object that can be written as JSON'''
        return (self.insn.mnemonic, self.operands)


class OpenSection:
    '''A section of instructions that are currently being added to'''
    def __init__(self, insns_left: int, insns: List[ProgInsn]):
        assert insns_left > 0
        self.insns_left = insns_left
        self.insns = insns

    def add_insns(self, insns: List[ProgInsn]) -> None:
        '''Add some instructions to the section'''
        assert self.insns_left >= len(insns)
        self.insns.extend(insns)
        self.insns_left -= len(insns)


class Program:
    '''An object representing the random program that is being generated.

    '''
    # The data for a section we're currently adding to. The tuples are
    # (sec_vma, space_left, insns) where sec_vma is the address of the start of
    # the section, space_left is the number of instructions that can be added
    # to the section before we run out of space and insns is a list of
    # instructions for the section.
    _SecData = Tuple[int, int, List[ProgInsn]]

    def __init__(self, imem_size: int) -> None:
        assert imem_size & 3 == 0
        self.imem_size = imem_size

        # A map from base address (VMA) to a list of instructions. Each
        # instruction is 4 bytes long, so a "section" of N instructions has
        # size 4N bytes.
        self._sections = {}  # type: Dict[int, List[ProgInsn]]

        # The current section's address and data, if there is one.
        self._cur_section = None  # type: Optional[Tuple[int, OpenSection]]

    def open_section(self, addr: int) -> None:
        '''Start a new section at addr'''
        assert addr & 3 == 0
        assert addr <= self.imem_size

        # Close any existing section
        self.close_section()

        assert self._cur_section is None

        # This linear search is a bit naff, but I doubt it will have a
        # significant performance impact.
        next_above = self.imem_size
        prev_sec_base = None  # type: Optional[int]
        for section_base in self._sections.keys():
            if addr <= section_base:
                if section_base < next_above:
                    next_above = section_base
            else:
                if prev_sec_base is None or prev_sec_base < section_base:
                    prev_sec_base = section_base

        # At this point, next_above is the base of the first section
        # immediately after addr (or the top of memory if there isn't one).
        # prev_sec_base is None if addr is below all existing sections or is
        # the address of the highest section that starts below addr.
        assert addr < next_above
        insns_left = (next_above - addr) // 4

        if prev_sec_base is not None:
            # If there is a previous section, check there is no overlap.
            prev_sec = self._sections[prev_sec_base]
            prev_sec_top = prev_sec_base + 4 * len(prev_sec)
            assert prev_sec_top <= addr

            # If prev_sec_top *equals* addr, we can merge the sections (neater
            # than generating two adjacent sections).
            if prev_sec_top == addr:
                del self._sections[prev_sec_base]
                self._cur_section = (prev_sec_base, OpenSection(insns_left, prev_sec))
                return

        # If we get here then there either was no previous section, or it
        # didn't butt up against our address. Open a new one.
        self._cur_section = (addr, OpenSection(insns_left, []))

    def close_section(self) -> None:
        '''Finalize any current section'''
        if self._cur_section is None:
            return

        sec_addr, open_section = self._cur_section

        # The "insns_left" tracking in OpenSection should ensure that this
        # section doesn't collide with anything else in self._sections. As a
        # quick sanity check, we make sure the base address isn't duplicated
        # (of course, that's not a full check, but it can't hurt).
        assert sec_addr not in self._sections
        self._sections[sec_addr] = open_section.insns

        self._cur_section = None

    def get_cur_section(self) -> Optional[OpenSection]:
        '''Returns the current section if there is one'''
        return self._cur_section[1] if self._cur_section is not None else None

    def add_insns(self, addr: int, insns: List[ProgInsn]) -> None:
        '''Add a sequence of instructions, starting at addr'''
        self.open_section(addr)
        assert self._cur_section is not None
        self._cur_section[1].add_insns(insns)

    def dump_asm(self, out_file: TextIO) -> None:
        '''Write an assembly representation of the program to out_file'''
        # Close any existing section, so that we can iterate over all the
        # instructions by iterating over self._sections.
        self.close_section()
        for idx, (addr, insns) in enumerate(sorted(self._sections.items())):
            out_file.write('{}/* Section {} ({} instructions) */\n'
                           .format('\n' if idx else '', idx, len(insns)))
            out_file.write('.offset {:#x}\n'.format(addr))
            for pi in insns:
                insn = pi.insn
                # We should never try to generate an instruction without syntax
                # (ensuring this is the job of the snippet generators)
                assert insn.syntax is not None

                # Build a dictionary from operand name to value from
                # pi.operands, which is a list of operand values in the same
                # order as insn.operands.
                op_vals = {}
                assert len(pi.operands) == len(insn.operands)
                for operand, op_val in zip(insn.operands, pi.operands):
                    op_vals[operand.name] = op_val

                rendered_ops = insn.syntax.render_vals(op_vals,
                                                       insn.name_to_operand)
                if insn.glued_ops and rendered_ops:
                    mnem = insn.mnemonic + rendered_ops[0]
                    rendered_ops = rendered_ops[1:]
                else:
                    mnem = insn.mnemonic

                out_file.write('{:14}{}\n'.format(mnem, rendered_ops))

    def pick_branch_targets(self,
                            min_len: int,
                            count: int,
                            tgt_min: Optional[int],
                            tgt_max: Optional[int]) -> Optional[List[int]]:
        '''Pick count random targets for a branch destination

        There is guaranteed to be at least space for min_len instructions at
        each target, but the weighting tries to favour places with some space
        for instruction sequences.

        If tgt_min is not None, the address returned will be at least tgt_min.
        Similarly for tgt_max.

        If we can't find space for the desired branch targets, returns None.

        '''

        # To pick the targets, we start by choosing a "gap" between existing
        # sections in which they should land. To do *that*, we start by making
        # a list of all the gaps between sections in ascending order of base
        # address.
        section_list = list(self._sections.items())
        if self._cur_section is not None:
            cur_base, cur_open_section = self._cur_section
            section_list.append((cur_base, cur_open_section.insns))
        section_list.sort()

        gap_vma = 0
        gap_list = []
        for section_base, section_insns in section_list:
            assert gap_vma <= section_base

            # We only count the gap if it isn't completely below tgt_min and
            # isn't completely above tgt_max.
            if (((tgt_min is None or tgt_min < section_base) and
                 (tgt_max is None or gap_vma <= tgt_max))):
                # The minimum address we can pick is gap_vma, but we might need
                # to bump it up if tgt_min is specified.
                gap_lo = (max(gap_vma, tgt_min)
                          if tgt_min is not None else gap_vma)

                # The maximum address we can pick needs space for min_len
                # instructions before we get to section_base *and* must be at
                # most tgt_max if that is specified.
                gap_hi = section_base - 4 * min_len
                if tgt_max is not None:
                    gap_hi = min(gap_hi, tgt_max)

                # If we have anything to use, add it!
                if gap_lo <= gap_hi:
                    gap_list.append((gap_lo, gap_hi - gap_lo + 1))

            gap_vma = section_base + 4 * len(section_insns)

        # Deal with any final gap above all known sections in the same way as
        # the internal gaps.
        gap_lo = (max(gap_vma, tgt_min)
                  if tgt_min is not None else gap_vma)
        gap_hi = self.imem_size - 4 * min_len
        if tgt_max is not None:
            gap_hi = min(gap_hi, tgt_max)
        if gap_lo <= gap_hi:
            gap_list.append((gap_lo, gap_hi - gap_lo + 1))

        ret = []
        for _ in range(count):

            # gap_list is an ordered list of pairs (addr, len), meaning "there
            # is a range of addresses that we can pick from, starting at
            # address addr and with length len bytes". If there are no gaps
            # left wide enough, gap_list will be empty and we should give up.
            if not gap_list:
                return None

            # Calculate weightings for the gaps. We weight by the extra length,
            # raised to some power (2.0, for now).
            gap_weight_pow = 2.0
            gap_weights = []
            for _, gap_len in gap_list:
                extra_len = gap_len - 4 * min_len
                gap_weights.append(1 + extra_len ** gap_weight_pow)

            idx = random.choices(range(len(gap_list)), weights=gap_weights)[0]

            # Now we have to decide what part of the gap to use. We choose the
            # offset in instructions from the start of the gap. Set
            # max_insn_off to the maximum allowed value.
            gap_vma, gap_len = gap_list[idx]
            max_insn_off = gap_len // 4 - min_len

            # To try to avoid splitting gaps too much, we want to make it more
            # likely that we'll pick stuff "at the edges". Rather than doing
            # clever maths, we split the range into 3 parts:
            #
            #    | low |     middle      | high |
            #
            # where low and high are each 10% of the total range (leaving the
            # other 80% in middle).
            #
            # Pick 0 <= D <= 1 and assign the weight D/2 to each of low and
            # high and 1-D to the middle. Larger values of D mean we favour the
            # edges more.
            D = 0.5
            endpts = [(0, max_insn_off // 10),
                      (max_insn_off // 10, max_insn_off * 9 // 10),
                      (max_insn_off * 9 // 10, max_insn_off)]
            min_insn_off, max_insn_off = \
                random.choices(endpts, weights=[D / 2, 1 - D, D / 2])[0]

            assert min_insn_off <= max_insn_off

            # Now that we've picked a region, we choose an offset uniformly
            # from the range
            rng_len = max_insn_off - min_insn_off
            insn_off = (min_insn_off + int(0.5 + random.random() * rng_len))
            assert min_insn_off <= insn_off <= max_insn_off

            assert 4 * insn_off <= gap_len
            tgt = gap_vma + 4 * insn_off
            ret.append(tgt)

            # The last thing we need to do is update the gap list.
            new_gap_list = []
            for gap_lo, gap_len in gap_list:
                gap_top = gap_lo + gap_len

                # Does this gap give us a gap to the left of the range [tgt,
                # tgt + 4 * min_len]?
                left_top = min(gap_top, tgt - 4 * min_len)
                if gap_lo < left_top:
                    new_gap_list.append((gap_lo, left_top - gap_lo))

                # And how about to the right?
                right_lo = max(gap_lo, tgt + 4 * min_len)
                if right_lo < gap_top:
                    new_gap_list.append((right_lo, gap_top - right_lo))

            gap_list = new_gap_list

        assert len(ret) == count
        return ret

    def pick_branch_target(self,
                           min_len: int,
                           tgt_min: Optional[int],
                           tgt_max: Optional[int]) -> Optional[int]:
        '''Pick a single random target for a branch destination

        A simple wrapper around the more general pick_branch_targets
        function.

        '''
        tgts = self.pick_branch_targets(min_len, 1, tgt_min, tgt_max)
        if tgts is None:
            return None

        assert len(tgts) == 1
        return tgts[0]

    def get_insn_space_at(self, addr: int) -> int:
        '''Return how many instructions there is space for, starting at addr'''
        space = self.imem_size - addr
        if space <= 0:
            return 0

        for sec_start, sec_insns in self._sections.items():
            sec_end = sec_start + 4 * len(sec_insns)
            if addr < sec_end:
                space = min(space, sec_start - addr)
                if space <= 0:
                    return 0

        if self._cur_section is not None:
            sec_start, open_section = self._cur_section
            sec_end = sec_start + 4 * len(open_section.insns)
            if addr < sec_end:
                space = min(space, sec_start - addr)

        return max(0, space // 4)
