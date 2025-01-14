import os
import time
from collections import defaultdict

from multiprocessing import Pool
from pathlib import Path
from typing import Dict, List, Type

import gurobipy as gp
import numpy as np
import torch
from gurobipy import GRB
from loguru import logger
from mip import BINARY, Model, maximize, xsum
from mip.constants import GUROBI
from omegaconf import DictConfig
from ray import tune
from scipy.sparse import spmatrix
from scipy.sparse.dok import dok_matrix
from tqdm import tqdm

from privacypacking.budget import ALPHAS, Block, Task
from privacypacking.schedulers.scheduler import TaskQueue


class MetricException(Exception):
    pass


class Metric:
    @staticmethod
    def from_str(metric: str, metric_config: DictConfig) -> Type["Metric"]:
        if metric in globals():
            return globals()[metric](config=metric_config)
        else:
            raise MetricException(f"Unknown metric: {metric}")

    def __init__(self, config: DictConfig) -> None:
        self.config = config
        self.clip_demands_in_relevance = self.config.clip_demands_in_relevance

    def apply(self, queue: TaskQueue, efficiency: float):
        pass

    def is_dynamic(self):
        return False


class DominantShares(Metric):
    def apply(
        self, task: Task, blocks: Dict[int, Block], tasks: List[Task] = None, clip=False
    ) -> List[float]:
        # Returns a multidimensional efficiency. We can do tie-breaking with lexicographic order.
        profit_over_cost = []
        for block_id, demand_budget in task.budget_per_block.items():
            block = blocks[block_id]
            block_initial_budget = block.initial_budget
            # Compute the demand share for each alpha of the block
            for alpha in block_initial_budget.alphas:
                # Drop RDP orders that are already negative
                if block_initial_budget.epsilon(alpha) > 0:
                    demand_fraction = demand_budget.epsilon(
                        alpha
                    ) / block_initial_budget.epsilon(alpha)
                    if clip:
                        demand_fraction = min(demand_fraction, 1)

                    profit_over_cost.append(task.profit / demand_fraction)

        # Order by highest demand fraction first
        profit_over_cost.sort()
        return profit_over_cost


class Fcfs(Metric):
    def apply(
        self, task: Task, blocks: Dict[int, Block] = None, tasks: List[Task] = None
    ) -> id:
        # The smallest id has the highest priority
        return 1 / (task.id + 1)


class FlatRelevance(Metric):
    def apply(
        self, task: Task, blocks: Dict[int, Block], tasks: List[Task] = None
    ) -> float:
        logger.info(f"Computing FlatRelevance for task {task.id}.")
        cost = 0.0
        for block_id, budget in task.budget_per_block.items():
            for alpha in budget.alphas:
                demand = budget.epsilon(alpha)
                capacity = blocks[block_id].initial_budget.epsilon(alpha)
                logger.info(
                    f"b{block_id}, alpha: {alpha}, demand: {demand}, capacity: {capacity}. Current cost: {cost}"
                )
                if capacity > 0:
                    cost += demand / capacity
        task.cost = cost
        logger.info(f"Task {task.id} cost: {cost} profit: {task.profit / cost} ")
        return task.profit / cost


class DynamicFlatRelevance(Metric):
    def apply(
        self, task: Task, blocks: Dict[int, Block], tasks: List[Task] = None
    ) -> float:
        logger.info(f"Computing DynamicFlatRelevance for task {task.id}.")
        cost = 0.0
        for block_id, budget in task.budget_per_block.items():
            for alpha in blocks[block_id].initial_budget.alphas:
                demand = budget.epsilon(alpha)
                remaining_budget = blocks[block_id].budget.epsilon(alpha)
                logger.info(
                    f"b{block_id}, alpha: {alpha}, demand: {demand}, remaining_budget: {remaining_budget}. Current cost: {cost}"
                )
                if remaining_budget > 0:
                    cost += demand / remaining_budget
        task.cost = cost
        if cost == 0:
            return float("inf")
        logger.info(f"Task {task.id} cost: {cost} profit: {task.profit / cost} ")
        return task.profit / cost

    def is_dynamic(self):
        return True


class RoundRobins(Metric):
    def apply(task: Task, blocks: Dict[int, Block], tasks: List[Task] = None) -> float:
        pass


class OverflowRelevance(Metric):
    def apply(
        self, task: Task, blocks: Dict[int, Block], tasks: List[Task] = None
    ) -> float:
        overflow_b_a = {}
        for t in tasks:
            for block_id, block_demand in t.budget_per_block.items():
                if block_id not in overflow_b_a:
                    overflow_b_a[block_id] = {}
                for a in block_demand.alphas:
                    if a not in overflow_b_a[block_id]:
                        overflow_b_a[block_id][a] = -blocks[
                            block_id
                        ].initial_budget.epsilon(a)
                    overflow_b_a[block_id][a] += block_demand.epsilon(a)

        costs = {}
        for block_id_, block_demand_ in task.budget_per_block.items():
            costs[block_id_] = 0
            for alpha in block_demand_.alphas:
                demand = block_demand_.epsilon(alpha)
                overflow = overflow_b_a[block_id_][alpha]
                if overflow > 0:
                    costs[block_id_] += demand / overflow
                else:
                    costs[block_id_] = 0
                    break
        total_cost = 0
        for cost in costs.values():
            total_cost += cost
        task.cost = total_cost
        if total_cost <= 0:
            return float("inf")
        return task.profit / total_cost


class RelevanceMetric(Metric):
    def compute_relevance_matrix(
        self,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        drop_blocks_with_no_contention=True,
        truncate_available_budget=False,
    ) -> np.ndarray:
        n_blocks = len(blocks)
        n_alphas = len(ALPHAS)
        relevance = np.zeros((n_blocks, n_alphas))

        return relevance

    def apply(
        self,
        task: Task,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        relevance_matrix: dict = None,
    ) -> float:

        # Only keep the blocks that appear in the relevance matrix
        # NOTE: if blocks don't have increasing IDs, slice by block names
        # task_demands = task.demand_matrix.toarray()[: len(blocks)]
        task_demands = task.demand_matrix[: len(blocks)]

        if self.clip_demands_in_relevance:
            # NOTE: we assume each block has the same initial capacity
            block_capacity = np.array(
                [blocks[0].initial_budget.epsilon(alpha) for alpha in ALPHAS]
            )
            task_demands = np.clip(task_demands, a_min=0, a_max=block_capacity)
        cost = np.multiply(task_demands, relevance_matrix).sum()
        return task.profit / cost if cost > 0 else float("inf")

    def is_dynamic(self):
        return True


class VectorizedBatchOverflowRelevance(Metric):
    def compute_relevance_matrix(
        self,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        drop_blocks_with_no_contention=True,
        truncate_available_budget=False,
    ) -> np.ndarray:

        # Compute the negative available unlocked budget
        n_blocks = len(blocks)
        n_alphas = len(ALPHAS)
        overflow = np.zeros((n_blocks, n_alphas))
        for block_id in range(n_blocks):
            for alpha_index, alpha in enumerate(ALPHAS):
                if truncate_available_budget:
                    eps = blocks[block_id].truncated_available_unlocked_budget.epsilon(
                        alpha
                    )
                    overflow[block_id, alpha_index] = -eps
                else:
                    eps = blocks[block_id].available_unlocked_budget.epsilon(alpha)
                    if eps >= 0:
                        overflow[block_id, alpha_index] = -eps
                    else:
                        # There is no available budget, so this alpha is not relevant
                        overflow[block_id, alpha_index] = float("inf")

        # Add all the demands
        sum_demands = sum((task.demand_matrix.toarray() for task in tasks))
        overflow += sum_demands

        if drop_blocks_with_no_contention:
            # If a block has an alpha without contention, the relevance should be 0 because we can allocate everything
            for block_id in range(n_blocks):
                min_overflow = np.min(overflow[block_id])
                if min_overflow <= 0:
                    overflow[block_id] = np.empty(shape=[1, n_alphas]).fill(
                        float("inf")
                    )

        # overflow > 0 or infinity (if we drop blocks with no contention)
        relevance = np.reciprocal(overflow)
        return relevance

    def apply(
        self,
        task: Task,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        relevance_matrix: dict = None,
    ) -> float:
        cost = np.multiply(task.demand_matrix.toarray(), relevance_matrix).sum()
        return task.profit / cost if cost > 0 else float("inf")

    def is_dynamic(self):
        return True


class SoftmaxOverflow(VectorizedBatchOverflowRelevance):
    # Instead of dividing by the overflow, we take a softmax. And we normalize.
    def compute_relevance_matrix(
        self,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        drop_blocks_with_no_contention=True,
        truncate_available_budget=False,
        temperature=0.1,
    ) -> np.ndarray:

        # Compute the negative available unlocked budget
        n_blocks = len(blocks)
        n_alphas = len(ALPHAS)
        available_budget = np.zeros((n_blocks, n_alphas))
        for block_id in range(n_blocks):
            for alpha_index, alpha in enumerate(ALPHAS):
                if truncate_available_budget:
                    eps = blocks[block_id].truncated_available_unlocked_budget.epsilon(
                        alpha
                    )
                    available_budget[block_id, alpha_index] = eps
                else:
                    eps = blocks[block_id].available_unlocked_budget.epsilon(alpha)
                    if eps >= 0:
                        available_budget[block_id, alpha_index] = eps
                    else:
                        # There is no available budget, so this alpha is not relevant
                        available_budget[block_id, alpha_index] = -float("inf")

        # Add all the demands
        sum_demands = sum((task.demand_matrix.toarray() for task in tasks))
        overflow = sum_demands - available_budget

        if drop_blocks_with_no_contention:
            # If a block has an alpha without contention, the relevance should be 0 because we can allocate everything
            for block_id in range(n_blocks):
                min_overflow = np.min(overflow[block_id])
                if min_overflow <= 0:
                    overflow[block_id] = np.empty(shape=[1, n_alphas]).fill(
                        float("inf")
                    )

        # overflow > 0 or infty (if we drop blocks with no contention)
        exponential_overflow = np.exp(-temperature * overflow)
        sum_per_block = np.sum(exponential_overflow, axis=1) + 1e-15
        softmax = np.divide(
            exponential_overflow,
            np.broadcast_to(
                np.expand_dims(sum_per_block, axis=1), (n_blocks, n_alphas)
            ),
        )

        logger.info(f"Softmax: {softmax}")
        time.sleep(2)

        # The softmax returns a probability vector, but different alphas have different scales.
        relevance = np.divide(softmax, available_budget)
        return relevance


class BatchOverflowRelevance(Metric):
    def compute_overflow(
        self, blocks: Dict[int, Block], tasks: List[Task] = None
    ) -> dict:
        overflow_b_a = {}
        for t in tasks:
            for block_id, block_demand in t.budget_per_block.items():
                if block_id not in overflow_b_a:
                    overflow_b_a[block_id] = {}
                for a in block_demand.alphas:
                    if a not in overflow_b_a[block_id]:
                        # NOTE: This is the only difference with (offline) OverflowRelevance
                        # overflow_b_a[block_id][a] = -blocks[
                        #     block_id
                        # ].initial_budget.epsilon(a)
                        available_unlocked_budget = blocks[
                            block_id
                        ].available_unlocked_budget.epsilon(a)

                        if available_unlocked_budget > 0:
                            overflow_b_a[block_id][a] = -available_unlocked_budget
                            logger.debug(
                                f"b{block_id}, alpha: {a}, available unlocked budget: {blocks[block_id].available_unlocked_budget.epsilon(a)}"
                            )
                        else:
                            # Alphas is consumed at this point
                            overflow_b_a[block_id][a] = float("inf")

                    # For exhausted alphas, the overflow remains infinite
                    overflow_b_a[block_id][a] += block_demand.epsilon(a)
        return overflow_b_a

    def apply(
        self,
        task: Task,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        overflow: dict = None,
    ) -> float:
        if overflow:
            logger.info("Using precomputed overflow")
            overflow_b_a = overflow
        else:
            logger.info("Computing fresh overflow")
            overflow_b_a = BatchOverflowRelevance.compute_overflow(blocks, tasks)

        costs = {}
        for block_id_, block_demand_ in task.budget_per_block.items():
            costs[block_id_] = 0
            for alpha in block_demand_.alphas:
                demand = block_demand_.epsilon(alpha)
                overflow = overflow_b_a[block_id_][alpha]
                logger.debug(
                    f"b{block_id_}, alpha: {alpha}, demand: {demand}, overflow: {overflow}. Current cost: {costs[block_id_]}"
                )
                if overflow > 0:
                    costs[block_id_] += demand / overflow
                else:
                    # There is no contention on this block!
                    costs[block_id_] = 0
                    break
        total_cost = 0
        for cost in costs.values():
            total_cost += cost
        task.cost = total_cost
        if total_cost <= 0:
            return float("inf")

        logger.info(
            f"Task {task.id} cost: {total_cost} profit: {task.profit / total_cost} "
        )

        return task.profit / total_cost

    def is_dynamic(self):
        return True


class SoftKnapsack(RelevanceMetric):

    def solve_local_knapsack(
        self, capacity, task_ids, task_demands, task_profits
    ) -> float:
        if capacity <= 0:
            return 0

        opt = 0

        with gp.Env(empty=True) as env:
            env.setParam("OutputFlag", 0)
            env.start()
            m = gp.Model(env=env)

            m.Params.TimeLimit = self.config.gurobi_timeout
            m.Params.MIPGap = 0.01  # Optimize within 1% of optimal

            x = m.addVars(task_ids, vtype=GRB.BINARY, name="x")
            m.addConstr(x.prod(task_demands) <= capacity)
            m.setObjective(x.prod(task_profits), GRB.MAXIMIZE)
            m.optimize()

            opt = m.getObjective().getValue()
        return opt

    def solve_local_knapsack_no_profits(self, capacity, task_demands) -> float:
        if capacity <= 0:
            return 0
        opt = 0
        sum = 0
        demands = list(task_demands.values())
        demands.sort()
        for demand in demands:
            if sum + demand <= capacity:
                sum += demand
                opt += 1
        return opt

    def compute_relevance_matrix(
        self,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        drop_blocks_with_no_contention=True,
        truncate_available_budget=False,
    ) -> np.ndarray:

        local_tasks_per_block = defaultdict(list)
        for t in tasks:
            for block_id in t.budget_per_block.keys():
                local_tasks_per_block[block_id].append(t)

        # Precompute the available budget in matrix form
        n_blocks = len(blocks)

        alphas = list(blocks.values())[0].initial_budget.alphas

        n_alphas = len(alphas)
        available_budget = np.zeros((n_blocks, n_alphas))
        for block_id in range(n_blocks):
            for alpha_index, alpha in enumerate(alphas):
                if truncate_available_budget:
                    eps = blocks[block_id].truncated_available_unlocked_budget.epsilon(
                        alpha
                    )
                    available_budget[block_id, alpha_index] = eps
                else:
                    eps = blocks[block_id].available_unlocked_budget.epsilon(alpha)
                    if eps > 0:
                        available_budget[block_id, alpha_index] = eps
                    else:
                        # There is no available budget, so this alpha is not relevant
                        available_budget[block_id, alpha_index] = -float("inf")

        # Solve the knapsack problem for each (block, alpha) pair
        logger.info(f"Preparing the arguments...")
        max_profits = np.zeros((n_blocks, n_alphas))
        args = []

        if self.config.save_profit_matrix:
            min_profit_per_block = np.zeros(n_blocks)
            efficiencies_per_block_alpha = {}

        for block_id in range(n_blocks):

            if self.config.save_profit_matrix:
                current_min_profit = float("inf")
                efficiencies_per_block_alpha[block_id] = defaultdict(list)

            for alpha_index, alpha in enumerate(alphas):
                local_tasks = local_tasks_per_block[block_id]

                local_capacity = available_budget[block_id, alpha_index]
                task_ids = [t.id for t in local_tasks]
                task_demands = {
                    t.id: t.budget_per_block[block_id].epsilon(alpha)
                    for t in local_tasks
                }
                task_profits = {task.id: task.profit for task in local_tasks}

                if self.config.save_profit_matrix and task_profits:
                    current_min_profit = min(
                        current_min_profit, min(task_profits.values())
                    )
                    for task_id in task_ids:
                        efficiencies_per_block_alpha[block_id][alpha_index].append(
                            task_demands[task_id]
                            / (local_capacity * task_profits[task_id])
                        )
                args.append((local_capacity, task_ids, task_demands, task_profits))

            if self.config.save_profit_matrix:
                min_profit_per_block[block_id] = current_min_profit

        logger.info(f"Solving the knapsacks in parallel...")
        with Pool(processes=self.config.n_knapsack_solvers) as pool:
            results = pool.starmap(self.solve_local_knapsack, args)
        logger.info(f"Collecting the results...")

        i = 0
        for block_id in range(n_blocks):

            for alpha_index, alpha in enumerate(alphas):

                logger.info(f"Solving{i} {block_id} alpha: {alpha}")
                max_profits[block_id, alpha_index] = results[i]
                i += 1

        if self.config.save_profit_matrix and min_profit_per_block[0] > 0:

            log_dir = Path(tune.get_trial_dir())
            np.save(log_dir.joinpath("max_profits.npy"), max_profits)
            np.save(log_dir.joinpath("min_profit_per_block.npy"), min_profit_per_block)
            torch.save(
                efficiencies_per_block_alpha,
                log_dir.joinpath("efficiencies_per_block_alpha.pt"),
            )

            # Save only once for now
            self.config.save_profit_matrix = False

        # Compute the softmax
        if self.config.polynomial_ratio:
            # Experimental: use a ratio instead of a softmax. Don't use, not really worth it.
            max_profits = np.power(max_profits, self.config.temperature)
            sum_profits = np.sum(max_profits, axis=1)
            softmax = np.divide(
                max_profits,
                np.broadcast_to(
                    np.expand_dims(sum_profits, axis=1), (n_blocks, n_alphas)
                ),
            )

        else:
            max_profits = max_profits / self.config.temperature

            # Substracting doesn't change the softmax. No overflow.
            max_profits = max_profits - max_profits.max(axis=1, keepdims=True)
            exponential_profits = np.exp(max_profits)
            sum_per_block = np.sum(exponential_profits, axis=1)
            softmax = np.divide(
                exponential_profits,
                np.broadcast_to(
                    np.expand_dims(sum_per_block, axis=1), (n_blocks, n_alphas)
                ),
            )
            logger.info(f"softmax: {softmax}")

        # Normalize the relevance values.
        # The softmax returns a probability vector, but different alphas have different scales.
        if self.config.normalize_by == "available_budget":
            relevance = np.divide(softmax, available_budget)
        elif self.config.normalize_by == "capacity":
            capacity = np.zeros((n_blocks, n_alphas))
            for block_id in range(n_blocks):
                for alpha_index, alpha in enumerate(alphas):
                    eps = blocks[block_id].initial_budget.epsilon(alpha)
                    # Empty alphas have relevance 0
                    capacity[block_id, alpha_index] = eps if eps > 0 else float("inf")
            relevance = np.divide(softmax, capacity)
        else:
            # NOTE: this is the default. The other settings give pretty similar results in my experience.
            relevance = softmax
        logger.info(f"relevance: {relevance}")

        return relevance


class ArgmaxKnapsack(SoftKnapsack):
    def compute_relevance_matrix(
        self,
        blocks: Dict[int, Block],
        tasks: List[Task] = None,
        drop_blocks_with_no_contention=True,
        truncate_available_budget=False,
    ) -> np.ndarray:

        local_tasks_per_block = defaultdict(list)
        for t in tasks:
            for block_id in t.budget_per_block.keys():
                local_tasks_per_block[block_id].append(t)

        # Precompute the available budget in matrix form
        n_blocks = len(blocks)

        alphas = list(blocks.values())[0].initial_budget.alphas

        n_alphas = len(alphas)
        available_budget = np.zeros((n_blocks, n_alphas))
        for block_id in range(n_blocks):
            for alpha_index, alpha in enumerate(alphas):
                if truncate_available_budget:
                    eps = blocks[block_id].truncated_available_unlocked_budget.epsilon(
                        alpha
                    )
                    available_budget[block_id, alpha_index] = eps
                else:
                    eps = blocks[block_id].available_unlocked_budget.epsilon(alpha)
                    if eps > 0:
                        available_budget[block_id, alpha_index] = eps
                    else:
                        # There is no available budget, so this alpha is not relevant
                        available_budget[block_id, alpha_index] = -float("inf")

        # Solve the knapsack problem for each (block, alpha) pair
        logger.info(f"Preparing the arguments...")
        max_profits = np.zeros((n_blocks, n_alphas))
        args = []

        if self.config.save_profit_matrix:
            min_profit_per_block = np.zeros(n_blocks)
            efficiencies_per_block = []

        for block_id in range(n_blocks):
            current_min_profit = float("inf")
            for alpha_index, alpha in enumerate(alphas):
                local_tasks = local_tasks_per_block[block_id]

                local_capacity = available_budget[block_id, alpha_index]
                task_ids = [t.id for t in local_tasks]
                task_demands = {
                    t.id: t.budget_per_block[block_id].epsilon(alpha)
                    for t in local_tasks
                }
                task_profits = {task.id: task.profit for task in local_tasks}

                if self.config.save_profit_matrix and alpha_index == 0:
                    current_min_profit = min(
                        current_min_profit, min(task_profits.values())
                    )
                    efficiencies_per_block_alpha = []
                    for task_id in task_ids:
                        efficiencies_per_block_alpha.append(
                            task_demands[task_id]
                            / (local_capacity * task_profits[task_id])
                        )
                        logger.warning(efficiencies_per_block_alpha)
                    efficiencies_per_block.append(
                        np.array(efficiencies_per_block_alpha)
                    )

                args.append((local_capacity, task_demands))
            if self.config.save_profit_matrix:
                min_profit_per_block[block_id] = current_min_profit

        if self.config.n_knapsack_solvers > 1:
            logger.info(f"Solving the knapsacks in parallel...")
            with Pool(processes=self.config.n_knapsack_solvers) as pool:
                results = pool.starmap(self.solve_local_knapsack_no_profits, args)
            logger.info(f"Collecting the results...")
        else:
            logger.info(f"Solving the knapsacks one by one...")

        i = 0
        for block_id in range(n_blocks):
            for alpha_index, alpha in enumerate(alphas):
                if self.config.n_knapsack_solvers > 1:
                    # We just need to collect from the results
                    max_profits[block_id, alpha_index] = results[i]
                else:
                    logger.info(f"Solving{i} {block_id} alpha: {alpha}")
                    max_profits[
                        block_id, alpha_index
                    ] = self.solve_local_knapsack_no_profits(*args[i])

                i += 1

        logger.info(f"Max profits: {max_profits}")

        if self.config.save_profit_matrix:

            log_dir = Path(tune.get_trial_dir())
            np.save(log_dir.joinpath("max_profits.npy"), max_profits)
            np.save(log_dir.joinpath("min_profit_per_block.npy"), min_profit_per_block)
            np.save(
                log_dir.joinpath("efficiencies_per_block.npy"),
                np.array(efficiencies_per_block),
            )

            # Save only once for now
            self.config.save_profit_matrix = False

        # Take a (hard) argmax instead of a softmax. Relevance 1 for the max alpha for each block, 0 else.
        softmax = np.apply_along_axis(
            lambda row: row == max(row), 1, max_profits
        ).astype(float)

        # Normalize the relevance values.
        # The softmax returns a probability vector, but different alphas have different scales.
        if self.config.normalize_by == "available_budget":
            relevance = np.divide(softmax, available_budget)
        elif self.config.normalize_by == "capacity":
            capacity = np.zeros((n_blocks, n_alphas))
            for block_id in range(n_blocks):
                for alpha_index, alpha in enumerate(alphas):
                    eps = blocks[block_id].initial_budget.epsilon(alpha)
                    # Empty alphas have relevance 0
                    capacity[block_id, alpha_index] = eps if eps > 0 else float("inf")
            relevance = np.divide(softmax, capacity)
        else:
            # NOTE: this is the default. The other settings give pretty similar results in my experience.
            relevance = softmax
        logger.info(f"relevance: {relevance}")

        return relevance
