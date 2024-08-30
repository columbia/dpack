import os
from datetime import datetime
from functools import partial, update_wrapper
from pathlib import Path
from typing import Any, Dict, List

import ray
import yaml
from loguru import logger
from ray import tune

from ray_analysis import load_ray_experiment
from privacypacking.config import Config
from privacypacking.schedulers.utils import (
    ARGMAX_KNAPSACK,
    DOMINANT_SHARES,
    FCFS,
)
from privacypacking.simulator.simulator import Simulator
from privacypacking.utils.utils import RAY_LOGS


def wrapped_partial(func, *args, **kwargs):
    partial_func = partial(func, *args, **kwargs)
    update_wrapper(partial_func, func)
    return partial_func


def run_and_report(config: dict, replace=False) -> None:

    if replace:
        # Overwrite the frequencies after the hyperparam sampling
        config["omegaconf"]["tasks"]["frequencies_path"] = (
            config["omegaconf"]["tasks"]["tasks_path"].replace("task", "frequency")
            + ".yaml"
        )

    sim = Simulator(Config(config))
    metrics = sim.run()
    tune.report(**metrics)



def grid_online(
    scheduler_scheduling_time: List[int],
    initial_blocks: List[int],
    max_blocks: List[int],
    metric_recomputation_period: List[int],
    data_path: List[str],
    tasks_sampling: str,
    data_lifetime: List[int],
    avg_num_tasks_per_block: List[int] = [100],
):
    scheduler_metrics = [
        ARGMAX_KNAPSACK,
        FCFS,
        DOMINANT_SHARES,
    ]

    temperature = [0.01]
    n = [1_000] # Progressive unlocking
    block_selection_policy = ["LatestBlocksFirst"]
    config = {}

    config["omegaconf"] = {
        "scheduler": {
            "metric_recomputation_period": tune.grid_search(
                metric_recomputation_period
            ),
            "scheduler_timeout_seconds": 20 * 60 * 60,
            "data_lifetime": tune.grid_search(data_lifetime),
            "scheduling_wait_time": tune.grid_search(scheduler_scheduling_time),
            "method": "batch",
            "metric": tune.grid_search(scheduler_metrics),
            "n": tune.grid_search(n),
        },
        "metric": {
            "normalize_by": "available_budget",
            "temperature": tune.grid_search(temperature),
            "n_knapsack_solvers": 1,
        },
        "logs": {
            "verbose": False,
            "save": True,
        },
        "blocks": {
            "initial_num": tune.grid_search(initial_blocks),
            "max_num": tune.grid_search(max_blocks),
        },
        "tasks": {
            "sampling": tasks_sampling,
            "data_path": tune.grid_search(data_path),
            "block_selection_policy": tune.grid_search(block_selection_policy),
            "avg_num_tasks_per_block": tune.grid_search(avg_num_tasks_per_block),
        },
    }
    logger.info(f"Tune config: {config}")

    experiment_analysis = tune.run(
        run_and_report,
        config=config,
        resources_per_trial={"cpu": 1},
        local_dir=RAY_LOGS,
        resume=False,
        verbose=1,
        callbacks=[
            CustomLoggerCallback(),
            tune.logger.JsonLoggerCallback(),
        ],
        progress_reporter=ray.tune.CLIReporter(
            metric_columns=["n_allocated_tasks", "total_tasks", "realized_profit"],
            parameter_columns={
                "omegaconf/scheduler/scheduling_wait_time": "T",
                "omegaconf/scheduler/data_lifetime": "lifetime",
                "omegaconf/scheduler/metric": "metric",
            },
            max_report_frequency=60,
        ),
    )
    all_trial_paths = experiment_analysis._get_trial_paths()
    experiment_dir = Path(all_trial_paths[0]).parent
    rdf = load_ray_experiment(experiment_dir)
    return rdf


class CustomLoggerCallback(tune.logger.LoggerCallback):
    """Custom logger interface"""

    def __init__(self, metrics=["scheduler_metric"]) -> None:
        self.metrics = ["n_allocated_tasks", "realized_profit"]
        self.metrics.extend(metrics)
        super().__init__()

    def log_trial_result(self, iteration: int, trial: Any, result: Dict):
        logger.info([f"{key}: {result[key]}" for key in self.metrics])
        return

    def on_trial_complete(self, iteration: int, trials: List, trial: Any, **info):
        return
