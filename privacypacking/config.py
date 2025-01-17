import math
import os
import random
import uuid
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import yaml
from loguru import logger
from numpy.lib.arraysetops import isin
from omegaconf import OmegaConf

from privacypacking.budget import Block, Task
from privacypacking.budget.block_selection import BlockSelectionPolicy
from privacypacking.budget.budget import Budget
from privacypacking.budget.task import UniformTask
from privacypacking.utils.utils import DEFAULT_CONFIG_FILE, REPO_ROOT, TaskSpec


# Configuration Reading Logic
class Config:
    def __init__(self, config):

        logger.info(f"Initializing config: {config}")

        # Just a basic configuration file that is not Turing-complete...
        default_omegaconf = OmegaConf.load(DEFAULT_CONFIG_FILE)
        custom_omegaconf = OmegaConf.create(config.get("omegaconf", {}))
        self.omegaconf = OmegaConf.merge(default_omegaconf, custom_omegaconf)

        # Rest of the configuration below
        self.global_seed = self.omegaconf.global_seed
        random.seed(self.global_seed)
        np.random.seed(self.global_seed)

        # BLOCKS
        self.initial_blocks_num = self.omegaconf.blocks.initial_num
        if self.omegaconf.scheduler.method == "offline":
            self.omegaconf.blocks.max_num = self.initial_blocks_num
        self.max_blocks = self.omegaconf.blocks.max_num
        self.max_tasks = None
        self.block_arrival_interval = 1

        # TASKS
        if self.omegaconf.tasks.sampling:
            self.data_path = REPO_ROOT.joinpath("data").joinpath(
                self.omegaconf.tasks.data_path
            )
            self.tasks_path = self.data_path.joinpath(self.omegaconf.tasks.tasks_path)
            self.task_frequencies_path = self.data_path.joinpath(
                "task_frequencies"
            ).joinpath(self.omegaconf.tasks.frequencies_path)

            with open(self.task_frequencies_path, "r") as f:
                self.task_frequencies_file = yaml.safe_load(f)
            assert len(self.task_frequencies_file) > 0

            self.task_arrival_interval = (
                self.block_arrival_interval
                / self.omegaconf.tasks.avg_num_tasks_per_block
            )

        else:
            # Read one CSV that contains all tasks
            self.data_path = REPO_ROOT.joinpath("data").joinpath(
                self.omegaconf.tasks.data_path
            )
            self.tasks = pd.read_csv(self.data_path)
            self.tasks_generator = self.tasks.iterrows()
            self.max_tasks = len(self.tasks) # To stop the task generator at the end of the file
            self.sum_task_interval = self.tasks["relative_submit_time"].sum()
            self.task_arrival_interval_generator = self.tasks[
                "relative_submit_time"
            ].iteritems()

    def dump(self) -> dict:
        return {"omegaconf": OmegaConf.to_container(self.omegaconf)}

    def create_task(self, task_id: int) -> Task:

        task = None
        # Read custom task specs from files
        if self.omegaconf.tasks.sampling:
            if not hasattr(self, "task_specs"):
                self.task_specs = [
                    self.load_task_spec_from_file(f"{self.tasks_path}/{task_file}")
                    for task_file in self.task_frequencies_file.keys()
                ]
                self.task_frequencies = list(self.task_frequencies_file.values())

            task_spec_index = np.random.choice(
                len(self.task_specs),
                1,
                p=self.task_frequencies,
            )[0]

            task_spec = self.task_specs[task_spec_index]

            if self.omegaconf.tasks.block_selection_policy:
                block_selection_policy = BlockSelectionPolicy.from_str(
                    self.omegaconf.tasks.block_selection_policy
                )
            else:
                block_selection_policy = task_spec.block_selection_policy
            assert block_selection_policy is not None

            task = UniformTask(
                id=task_id,
                profit=task_spec.profit,
                block_selection_policy=block_selection_policy,
                n_blocks=task_spec.n_blocks,
                budget=task_spec.budget,
                name=task_spec.name,
            )
        # Not sampling but reading actual tasks sequentially from one file
        else:
            _, task_row = next(self.tasks_generator)
            orders = {}
            parsed_alphas = task_row["alphas"].strip("][").split(", ")
            parsed_epsilons = task_row["rdp_epsilons"].strip("][").split(", ")

            for i, alpha in enumerate(parsed_alphas):
                alpha = float(alpha)
                epsilon = float(parsed_epsilons[i])
                orders[alpha] = epsilon

            task = UniformTask(
                id=task_id,
                profit=float(task_row["profit"]),
                block_selection_policy=BlockSelectionPolicy.from_str(
                    task_row["block_selection_policy"]
                ),
                n_blocks=int(task_row["n_blocks"]),
                budget=Budget(orders),
                name=task_row["task_name"],
            )

        assert task is not None
        return task

    def create_block(self, block_id: int) -> Block:
        return Block.from_epsilon_delta(
            block_id,
            self.omegaconf.epsilon,
            self.omegaconf.delta,
            alpha_list=self.omegaconf.alphas,
        )

    def set_task_arrival_time(self):
        if self.omegaconf.tasks.sampling == "poisson":
            task_arrival_interval = partial(
                random.expovariate, 1 / self.task_arrival_interval
            )()
        elif self.omegaconf.tasks.sampling == "constant":
            task_arrival_interval = self.task_arrival_interval

        else:
            _, task_arrival_interval = next(self.task_arrival_interval_generator)
            normalized_task_interval = task_arrival_interval / self.sum_task_interval
            task_arrival_interval = (
                normalized_task_interval * self.block_arrival_interval * (self.max_blocks - self.initial_blocks_num)
            )
        return task_arrival_interval

    def set_block_arrival_time(self):
        return self.block_arrival_interval

    def get_initial_tasks_num(self) -> int:
        return self.omegaconf.tasks.initial_num

    def get_initial_blocks_num(self) -> int:
        return self.initial_blocks_num

    def load_task_spec_from_file(self, path: Path) -> TaskSpec:

        with open(path, "r") as f:
            demand_dict = yaml.safe_load(f)
            orders = {}
            for i, alpha in enumerate(demand_dict["alphas"]):
                orders[alpha] = demand_dict["rdp_epsilons"][i]
            block_selection_policy = None
            if "block_selection_policy" in demand_dict:
                block_selection_policy = BlockSelectionPolicy.from_str(
                    demand_dict["block_selection_policy"]
                )

            n_blocks = demand_dict.get("n_blocks", 1)
            profit = demand_dict.get("profit", 1)

            task_spec = TaskSpec(
                profit=profit,
                block_selection_policy=block_selection_policy,
                n_blocks=n_blocks,
                budget=Budget(orders),
                name=os.path.basename(path),
            )
        assert task_spec is not None
        return task_spec
