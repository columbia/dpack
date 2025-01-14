from typing import Any, Iterable, Union

import numpy as np
from scipy.sparse import bsr_matrix, dok_matrix
from scipy.sparse.construct import vstack

from privacypacking.budget.block_selection import BlockSelectionPolicy
from privacypacking.budget.budget import ALPHAS, Budget
from privacypacking.budget.curves import ZeroCurve
from privacypacking.utils.utils import sample_one_from_string


class Task:
    def __init__(
        self,
        id: int,
        profit: Union[float, str],
        block_selection_policy: BlockSelectionPolicy,
        n_blocks: Union[int, str],
        name: str = None,
    ):
        self.id = id
        self.profit = profit
        self.block_selection_policy = block_selection_policy
        self.n_blocks = n_blocks
        self.name = name

        # Scheduler dynamically updates the variables below
        self.budget_per_block = {}
        self.cost = 0

    def sample_n_blocks_and_profit(self):
        """
        If profit and n_blocks are stochastic, we sample their value when the task is added to the scheduler.
        Do not cache this for all the instances of a same task, unless this is intended.
        """

        if isinstance(self.n_blocks, str):
            self.n_blocks = int(sample_one_from_string(self.n_blocks))

        if isinstance(self.profit, str):
            self.profit = sample_one_from_string(self.profit)

    def get_efficiency(self, cost):
        efficiency = 0
        try:
            efficiency = self.profit / cost
        except ZeroDivisionError as err:
            print("Handling run-time error:", err)
        return efficiency

    def get_budget(self, block_id: int) -> Budget:
        """
        Args:
            block_id (int): a block id

        Returns:
            Budget: the budget of the block if demanded by the task, else ZeroCurve
        """

        if block_id in self.budget_per_block:
            return self.budget_per_block[block_id]
        else:
            return ZeroCurve()

    def set_budget_per_block(self, block_ids: Iterable[int]):
        pass

    def dump(self, budget_per_block=True):
        d = {
            "id": self.id,
            "name": self.name,
            "profit": self.profit,
            "start_time": None,
            "allocation_time": None,
            "n_blocks": len(self.budget_per_block),
            "max_block_id": max(list(self.budget_per_block.keys())),
        }
        if budget_per_block:
            d["budget_per_block"] = {
                block_id: budget.dump()
                for block_id, budget in self.budget_per_block.items()
            }
        return d

    def build_demand_matrix(self, alphas=ALPHAS, max_block_id=None):
        # Prepare a sparse matrix of the demand
        max_block_id = max_block_id or max(self.budget_per_block.keys())
        n_alphas = len(alphas)
        self.demand_matrix = np.zeros((max_block_id + 1, n_alphas))
        for block_id, budget in self.budget_per_block.items():
            for i, alpha in enumerate(alphas):
                self.demand_matrix[block_id, i] = budget.epsilon(alpha)


class UniformTask(Task):
    def __init__(
        self,
        id: int,
        profit: float,
        block_selection_policy: Any,
        n_blocks: int,
        budget: Budget,
        name: str = None,
    ):
        """
        A Task that requires (the same) `budget` for all blocks in `block_ids`
        """
        self.budget = budget
        super().__init__(id, profit, block_selection_policy, n_blocks, name=name)

    def set_budget_per_block(
        self, block_ids: Iterable[int], demands_tiebreaker: float = 0
    ):

        # Add random noise (negligible) to break ties when we compare multiple copies of the same task
        # We don't need this in real life when tasks come from a large pool or continuum of tasks
        if demands_tiebreaker:
            fraction_offset = np.random.random()
            self.budget = self.budget * (1 + demands_tiebreaker * fraction_offset)

        for block_id in block_ids:
            self.budget_per_block[block_id] = self.budget
