from collections import namedtuple
from typing import Dict, List, Tuple

import numpy as np
from opacus.accountants.analysis.rdp import get_privacy_spent

ALPHAS = [
    1.5,
    1.75,
    2,
    2.5,
    3,
    4,
    5,
    6,
    8,
    16,
    32,
    64,
]


# Default values for some datasets
DELTA_MNIST = 1e-5
DELTA_CIFAR10 = 1e-5
DELTA_IMAGENET = 1e-7
MAX_DUMP_DIGITS = 50


DPBudget = namedtuple("ConvertedDPBudget", ["epsilon", "delta", "best_alpha"])


class Budget:
    def __init__(self, orders: Dict[float, float]) -> None:
        # "Immutable" dict sorted by small alphas first
        self.__orders = {}
        for alpha in sorted(orders):
            self.__orders[alpha] = orders[alpha]

    @classmethod
    def from_epsilon_list(
        cls, epsilon_list: List[float], alpha_list: List[float] = ALPHAS
    ) -> "Budget":

        if len(alpha_list) != len(epsilon_list):
            raise ValueError("epsilon_list and alpha_list should have the same length")

        orders = {alpha: epsilon for alpha, epsilon in zip(alpha_list, epsilon_list)}

        return cls(orders)

    @classmethod
    def from_epsilon_delta(
        cls, epsilon: float, delta: float, alpha_list: List[float] = ALPHAS
    ) -> "Budget":
        """Uses the RDP->DP conversion formula to initialize the RDP curve of a block.

        If the sum of all the RDP curves of the tasks on this block is below the
        budget returned by `from_epsilon_delta(epsilon, delta)` for at least one alpha,
        then the composition of the tasks is (epsilon, delta)-DP.
        """
        orders = {}
        for alpha in alpha_list:
            orders[alpha] = max(epsilon + np.log(delta) / (alpha - 1), 0)
        return cls(orders)

    def is_positive(self) -> bool:
        for epsilon in self.epsilons:
            if epsilon >= 0:
                return True
        return False

    def is_positive_all_alphas(self) -> bool:
        for epsilon in self.epsilons:
            if epsilon < 0:
                return False
        return True

    @property
    def alphas(self) -> list:
        return list(self.__orders.keys())

    @property
    def epsilons(self) -> list:
        return list(self.__orders.values())

    def epsilon(self, alpha: float) -> float:
        return self.__orders[alpha]

    def dp_budget(self, delta: float = DELTA_MNIST) -> DPBudget:
        """
        Uses a tight conversion formula to get (epsilon, delta)-DP.
        It can be slow to compute for the first time.
        """

        if hasattr(self, "dp_budget_cached"):
            return self.dp_budget_cached

        epsilon, best_alpha = get_privacy_spent(
            orders=list(self.alphas),
            rdp=list(self.epsilons),
            delta=delta,
        )
        epsilon, best_alpha = float(epsilon), float(best_alpha)

        # Cache the result
        self.dp_budget_cached = DPBudget(
            epsilon=epsilon, delta=delta, best_alpha=best_alpha
        )

        return self.dp_budget_cached

    def add_with_threshold(self, other: "Budget", threshold: "Budget"):
        """
        Increases every budget-epsilon by "amount".
        The maximum value a budget-epsilon can take is threshold-epsilon.
        """
        return Budget(
            {
                alpha: min(
                    self.epsilon(alpha) + other.epsilon(alpha), threshold.epsilon(alpha)
                )
                for alpha in self.alphas
            }
        )

    def can_allocate(self, demand_budget: "Budget") -> bool:
        """
        There must exist at least one order in the block's budget
        that is smaller or equal to the corresponding order of the demand budget.

        Assumes that the demand_budget is positive for all alphas.
        """
        assert demand_budget.is_positive_all_alphas()
        diff = self - demand_budget
        max_order = max(diff.epsilons)
        if max_order >= 0:
            return True
        return False

    def approx_epsilon_bound(self, delta: float) -> "Budget":
        return Budget(
            {
                alpha: epsilon - np.log(delta) / (alpha - 1)
                for alpha, epsilon in zip(self.alphas, self.epsilons)
            }
        )

    def positive(self) -> "Budget":
        return Budget(
            {
                alpha: max(epsilon, 0.0)
                for alpha, epsilon in zip(self.alphas, self.epsilons)
            }
        )

    @classmethod
    def same_support(
        cls, budget1: "Budget", budget2: "Budget"
    ) -> Tuple["Budget", "Budget"]:
        """Reduces two budgets to the same support (i.e. same set of RDP orders).
        Does not modify the original budgets inplace.
        The orders the new budgets are the intersection of the two original order sets.

        Returns:
            Tuple["Budget", "Budget"]: `(budget1, budget2)` reduced to the same support.
        """

        shared_alphas = set(budget1.alphas).intersection(budget2.alphas)
        ordered_support = sorted(shared_alphas)
        orders1, orders2 = {}, {}
        for alpha in ordered_support:
            orders1[alpha] = budget1.epsilon(alpha)
            orders2[alpha] = budget2.epsilon(alpha)
        return (cls(orders1), cls(orders2))

    def __eq__(self, other):
        for alpha in self.alphas:
            if other.epsilon(alpha) != self.epsilon(alpha):
                return False
        return True

    def __sub__(self, other):
        a, b = Budget.same_support(self, other)
        return Budget(
            {alpha: a.epsilon(alpha) - b.epsilon(alpha) for alpha in a.alphas}
        )

    def __add__(self, other):
        a, b = Budget.same_support(self, other)
        return Budget(
            {alpha: a.epsilon(alpha) + b.epsilon(alpha) for alpha in a.alphas}
        )

    def normalize_by(self, other: "Budget"):
        a, b = Budget.same_support(self, other)
        return Budget(
            {
                alpha: a.epsilon(alpha) / b.epsilon(alpha)
                for alpha in a.alphas
                if b.epsilon(alpha) > 0
            }
        )

    def __mul__(self, n: float):
        return Budget({alpha: self.epsilon(alpha) * n for alpha in self.alphas})

    def __truediv__(self, n: int):
        return Budget({alpha: self.epsilon(alpha) / n for alpha in self.alphas})

    def __repr__(self) -> str:
        return "Budget({})".format(self.__orders)

    def __ge__(self, other) -> bool:
        diff = self - other
        return diff.is_positive()

    def copy(self):
        return Budget(self.__orders.copy())

    def dump(self):
        rounded_orders = {
            alpha: round(self.epsilon(alpha), MAX_DUMP_DIGITS) for alpha in self.alphas
        }
        budget_info = {"orders": rounded_orders}
        dp_budget = self.dp_budget()
        budget_info.update(
            {
                "dp_budget": {
                    "epsilon": round(dp_budget.epsilon, MAX_DUMP_DIGITS),
                    "delta": dp_budget.delta,
                    "best_alpha": dp_budget.best_alpha,
                }
            }
        )
        return budget_info
