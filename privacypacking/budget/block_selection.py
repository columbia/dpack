import random
from typing import Dict, List, Type

import numpy as np

from privacypacking.budget import Block


class BlockSelectionException(Exception):
    pass


class NotEnoughBlocks(BlockSelectionException):
    pass


class BlockSelectionPolicy:
    @staticmethod
    def from_str(policy_name: str) -> Type["BlockSelectionPolicy"]:
        if "Zeta" in policy_name:
            alpha = float(policy_name.split("_")[1])
            return Zeta(alpha)
        if policy_name in globals():
            return globals()[policy_name]
        else:
            raise BlockSelectionException(f"Unknown policy name: {policy_name}")

    @staticmethod
    def select_blocks(blocks: Dict[int, Block], task_blocks_num: int) -> List[int]:
        pass


class RandomBlocks(BlockSelectionPolicy):
    @staticmethod
    def select_blocks(blocks, task_blocks_num):
        n_blocks = len(blocks)
        blocks_num = range(n_blocks)
        if task_blocks_num > n_blocks:
            raise NotEnoughBlocks(
                f"Requested {task_blocks_num} random blocks but there are only {n_blocks} blocks available."
            )
        return random.sample(blocks_num, task_blocks_num)


class LatestBlocksFirst(BlockSelectionPolicy):
    @staticmethod
    def select_blocks(blocks, task_blocks_num):
        n_blocks = len(blocks)
        if task_blocks_num > n_blocks:
            raise NotEnoughBlocks(
                f"Requested {task_blocks_num} blocks but there are only {n_blocks} blocks available."
            )
        return reversed(range(n_blocks - task_blocks_num, n_blocks))


class BiasedRandomBlocks(BlockSelectionPolicy):
    @staticmethod
    def select_blocks(blocks, task_blocks_num):
        n_blocks = len(blocks)
        blocks_num = range(n_blocks)
        if task_blocks_num > n_blocks:
            raise NotEnoughBlocks(
                f"Requested {task_blocks_num} random blocks but there are only {n_blocks} blocks available."
            )

        selected_blocks = []
        selection = np.random.choice(
            [0, 1],
            1,
            p=[
                0.7,
                0.3,
            ],
        )[0]

        if selection == 0:
            # Biased - prefer even block_ids
            even_block_ids = [i for i in blocks_num if i % 2 == 0]
            odd_block_ids = [i for i in blocks_num if i % 2 != 0]

            diff = task_blocks_num - len(even_block_ids)
            if diff > 0:
                selected_blocks = even_block_ids + random.sample(odd_block_ids, diff)
            else:
                selected_blocks = random.sample(even_block_ids, task_blocks_num)
        else:
            # Unbiased - choose-randomly
            selected_blocks = random.sample(blocks_num, task_blocks_num)
        return selected_blocks


class ContiguousBlocksRandomOffset(BlockSelectionPolicy):
    @staticmethod
    def select_blocks(blocks, task_blocks_num):
        n_blocks = len(blocks)
        if task_blocks_num > n_blocks:
            raise NotEnoughBlocks(
                f"Requested {task_blocks_num}  blocks but there are only {n_blocks} blocks available."
            )
        offset = random.randint(0, n_blocks - task_blocks_num)

        return range(offset, offset + task_blocks_num)


class Zeta(BlockSelectionPolicy):
    def __init__(self, s: float) -> None:
        self.s = s

    def select_blocks(self, blocks, task_blocks_num):
        n_blocks = len(blocks)
        if task_blocks_num > n_blocks:
            raise NotEnoughBlocks(
                f"Requested {task_blocks_num} blocks but there are only {n_blocks} blocks available."
            )
        density = np.array([(k + 1) ** (-self.s) for k in range(n_blocks)])
        density /= sum(density)

        return np.random.choice(
            n_blocks, task_blocks_num, replace=False, p=density
        ).tolist()
