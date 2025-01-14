from cmath import isinf
from collections import defaultdict
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import scipy
from loguru import logger
from omegaconf import OmegaConf

from privacypacking.budget import Budget
from privacypacking.budget.budget import ALPHAS
from privacypacking.budget.curves import (
    GaussianCurve,
    LaplaceCurve,
    SubsampledGaussianCurve,
    SubsampledLaplaceCurve,
    SyntheticPolynomialCurve,
)
from privacypacking.utils.utils import sample_one_from_string


def build_synthetic_zoo() -> list:
    curve_zoo = []
    task_names = []

    for best_alpha in ALPHAS[5:-2]:
        for norm_epsilon_min in np.linspace(0.01, 0.5, 5):
            for norm_epsilon_right in np.linspace(0.01, 1, 5):
                norm_epsilon_left = (norm_epsilon_min + norm_epsilon_right) / 2
                if (
                    norm_epsilon_min < norm_epsilon_left
                    and norm_epsilon_min < norm_epsilon_right
                ):
                    curve_zoo.append(
                        SyntheticPolynomialCurve(
                            best_alpha=best_alpha,
                            epsilon_min=norm_epsilon_min,
                            epsilon_left=norm_epsilon_left,
                            epsilon_right=norm_epsilon_right,
                        )
                    )
                    task_names.append(
                        f"{norm_epsilon_left:.3f}-{norm_epsilon_min:.3f}-{norm_epsilon_right:.3f}-{best_alpha}"
                    )

    return list(zip(task_names, curve_zoo))


def load_zoo(tasks_path):
    curve_zoo = []
    task_names = []
    blocks_dict = defaultdict(list)
    for task_path in Path(tasks_path).glob("*.yaml"):
        task_dict = OmegaConf.load(task_path)
        name = task_path.stem
        if not "gaussian" in name:
            orders = {
                alpha: epsilon
                for alpha, epsilon in zip(task_dict.alphas, task_dict.rdp_epsilons)
            }
            curve_zoo.append(Budget(orders=orders))
            task_names.append(name)
        blocks_dict["task_name"].append(name)
        blocks_dict["n_blocks"].append(task_dict.n_blocks)

    blocks_df = pd.DataFrame(blocks_dict)
    return list(zip(task_names, curve_zoo)), blocks_df


def build_zoo() -> list:
    curve_zoo = []
    task_names = []

    for sigma in np.geomspace(0.01, 10, 100):
        curve_zoo.append(GaussianCurve(sigma=sigma))
        task_names.append(f"gaussian-{sigma:.4f}")

    for sigma in np.geomspace(0.01, 10, 10):
        for dataset_size in np.geomspace(1_000, 100_000_000, 10):
            curve_zoo.append(GaussianCurve(sigma=sigma) * np.ceil(np.log(dataset_size)))
            task_names.append(f"dpftrl-{sigma:.4f}-{np.ceil(np.log(dataset_size))}")

    for sigma in np.geomspace(0.01, 10, 100):
        curve_zoo.append(LaplaceCurve(laplace_noise=sigma))
        task_names.append(f"laplace-{sigma:.4f}")

    r = [0.05, 0.1, 0.5, 1, 2, 3, 4, 5]
    for i in r:
        for j in r:
            curve_zoo.append(LaplaceCurve(i) + GaussianCurve(j))
            task_names.append(f"l{i}g{j}")

            curve_zoo.append(LaplaceCurve(i) + LaplaceCurve(j))
            task_names.append(f"l{i}l{j}")

    for k in [1, 10, 100, 200]:
        for (q, s) in product([0.001, 0.01, 0.05, 0.1, 0.2, 0.5], [0.1, 0.5, 1, 2]):
            curve_zoo.append(SubsampledGaussianCurve(q, s, k / q))
            task_names.append(f"subsampledgaussian-q{q}_s{s}_k{k}")

            curve_zoo.append(
                SubsampledLaplaceCurve(
                    noise_multiplier=s,
                    sampling_probability=q,
                    steps=k / q,
                )
            )
            task_names.append(f"subsampledlaplace-q{q}_s{s}_k{k}")

    return list(zip(task_names, curve_zoo))


def normalize_zoo(
    names_and_curves,
    epsilon_min_avg=None,
    epsilon_min_std=None,
    range_avg=None,
    range_std=None,
    control_flatness=True,
    control_size=True,
    min_epsilon=1e-2,
    epsilon=10,
    delta=1e-7,
    **kwargs,
) -> list:

    # We'll work on the dataframe, and dump back the zoo at the end
    alphas_df, tasks_df = zoo_df(
        names_and_curves,
        min_epsilon=min_epsilon,
        epsilon=epsilon,
        delta=delta,
        clipped=False,
    )

    if control_size:
        # Compute stats, shift and scale to normalize! We start with epsilon min.
        offset = (
            alphas_df.query("alphas == best_alpha")
            .groupby("alphas")
            .agg({"normalized_epsilons": "mean"})
            .reset_index()
            .rename(
                columns={
                    "alphas": "best_alpha",
                    "normalized_epsilons": "epsilon_min_avg",
                }
            )
        )
        offset_2 = (
            alphas_df.query("alphas == best_alpha")
            .groupby("alphas")
            .agg({"normalized_epsilons": "std"})
            .reset_index()
            .rename(
                columns={
                    "alphas": "best_alpha",
                    "normalized_epsilons": "epsilon_min_std",
                }
            )
        )
        offset = offset.merge(offset_2)

        logger.debug(f"Original epsilon min avg/std: {offset}")

        alphas_df = alphas_df.merge(offset)
        rescaled = alphas_df.copy()

        # Vertical shift the whole curve depending on epsilon_min
        rescaled["normalized_epsilons"] = (
            alphas_df["normalized_epsilons"]
            + (epsilon_min_avg - alphas_df["epsilon_min"])
            + epsilon_min_std
            * (alphas_df["epsilon_min"] - alphas_df["epsilon_min_avg"])
            / alphas_df["epsilon_min_std"]
        )
    else:
        rescaled = alphas_df

    if control_flatness:
        # Collect some stats about flatness (range). We only focus on flatness in the relevant region.
        ranges = (
            rescaled.query("alphas in [4,5,6,8]")
            .groupby("task_id")["normalized_epsilons"]
            .agg(min)
            .reset_index()
            .rename(columns={"normalized_epsilons": "epsilon_range_min"})
        )
        ranges = ranges.merge(
            rescaled.query("alphas in [4,5,6,8]")
            .groupby("task_id")["normalized_epsilons"]
            .agg(max)
            .reset_index()
            .rename(columns={"normalized_epsilons": "epsilon_range_max"})
        )
        ranges = ranges.merge(
            rescaled.groupby("task_id")["normalized_epsilons"]
            .agg(min)
            .reset_index()
            .rename(columns={"normalized_epsilons": "epsilon_min"})
        )
        ranges["epsilon_range"] = (
            ranges["epsilon_range_max"] - ranges["epsilon_range_min"]
        )

        # Attach the range stats to each task
        rescaled = rescaled.drop(
            columns=["epsilon_range", "epsilon_min"]
        )  # Obsolete columns since we rescaled
        rescaled = rescaled.merge(ranges, on="task_id")

        # Aggregate the range stats to prepare the scaling
        offset_range = (
            rescaled.query("alphas == 3")
            .groupby("best_alpha")
            .agg({"epsilon_range": "mean"})
            .reset_index()
            .rename(
                columns={"alphas": "best_alpha", "epsilon_range": "epsilon_range_avg"}
            )
        )
        offset_range_2 = (
            rescaled.query("alphas == 3")
            .groupby("best_alpha")
            .agg({"epsilon_range": "std"})
            .reset_index()
            .rename(
                columns={"alphas": "best_alpha", "epsilon_range": "epsilon_range_std"}
            )
        )
        offset_range = offset_range.merge(offset_range_2)

        logger.debug(f"Original range avg/std: {offset_range}")

        rescaled = rescaled.merge(offset_range)

        # Do the scaling: bend the curve upwards while keeping epsilon_min identical
        rescaled_with_range = rescaled.copy()
        rescaled_with_range["new_range"] = (
            range_avg
            + range_std
            * (
                rescaled_with_range["epsilon_range"]
                - rescaled_with_range["epsilon_range_avg"]
            )
            / rescaled_with_range["epsilon_range_std"]
        )
        rescaled_with_range["normalized_epsilons"] = rescaled_with_range[
            "epsilon_min"
        ] + (
            rescaled_with_range["new_range"] / rescaled_with_range["epsilon_range"]
        ) * (
            rescaled_with_range["normalized_epsilons"]
            - rescaled_with_range["epsilon_min"]
        )

    else:
        rescaled_with_range = rescaled

    # Deal with errors
    rescaled_with_range.replace([np.inf, -np.inf], np.nan, inplace=True)
    rescaled_with_range = rescaled_with_range.fillna(value={"normalized_epsilons": 10})

    # Extract the tasks back into their expected form. Fill in missing alphas with 1s.
    # zoo_df will do some clipping/dropping for invalid tasks, that's not our business here
    new_names_and_curves = []
    block = Budget.from_epsilon_delta(epsilon=epsilon, delta=delta)
    for task_name in rescaled_with_range.task_name.unique():
        orders = {}
        for _, row in rescaled_with_range.query(
            f"task_name == '{task_name}'"
        ).iterrows():
            alpha = row["alphas"]
            epsilon = block.epsilon(alpha) * row["normalized_epsilons"]
            orders[alpha] = epsilon
        for alpha in ALPHAS:
            if alpha not in orders:
                orders[alpha] = 100  # Will be dropped by the schdulers anyway
        new_names_and_curves.append((task_name, Budget(orders=orders)))

    return new_names_and_curves


def zoo_df(
    zoo: list,
    min_epsilon=5e-2,
    max_epsilon=1,
    clipped=True,
    epsilon=10,
    delta=1e-7,
    best_alphas=ALPHAS,
) -> pd.DataFrame:
    block = Budget.from_epsilon_delta(epsilon=epsilon, delta=delta)

    dict_list = defaultdict(list)
    for index, name_and_curve in enumerate(zoo):
        name, curve = name_and_curve
        for alpha, epsilon in zip(curve.alphas, curve.epsilons):
            if block.epsilon(alpha) > 0:
                dict_list["alphas"].append(alpha)
                dict_list["rdp_epsilons"].append(epsilon)
                if clipped:
                    dict_list["normalized_epsilons"].append(
                        min(epsilon / block.epsilon(alpha), 1)
                    )
                else:
                    dict_list["normalized_epsilons"].append(
                        epsilon / block.epsilon(alpha)
                    )
                dict_list["task_id"].append(index)
                dict_list["task_name"].append(name)
    df = pd.DataFrame(dict_list)

    tasks = pd.DataFrame(
        df.groupby("task_id")["normalized_epsilons"].agg(min)
    ).reset_index()
    tasks = tasks.rename(columns={"normalized_epsilons": "epsilon_min"})
    tasks["epsilon_max"] = df.groupby("task_id")["normalized_epsilons"].agg(max)

    # We only consider plausible alphas, not the dominant share (it is irrelevant anyway). "Flatness".
    tasks["epsilon_range"] = (
        df.query("alphas in [4,5,6,8]")
        .groupby("task_id")["normalized_epsilons"]
        .agg(max)
    ) - (
        df.query("alphas in [4,5,6,8]")
        .groupby("task_id")["normalized_epsilons"]
        .agg(min)
    )
    tasks = tasks.query(f"epsilon_min < {max_epsilon} and epsilon_min > {min_epsilon}")

    indx = df.groupby("task_id")["normalized_epsilons"].idxmin()
    best_alpha = df.loc[indx][["task_id", "alphas"]]
    best_alpha = best_alpha.rename(columns={"alphas": "best_alpha"})
    tasks = tasks.merge(best_alpha, how="inner", on="task_id").query(
        f"best_alpha in {best_alphas}"
    )

    logger.info(tasks.best_alpha.unique())

    df = df.merge(tasks, on="task_id")

    logger.info(df.best_alpha.unique())

    def get_task_type(task_name):
        if "-" in task_name:
            return task_name.split("-")[0]
        if "g" in task_name:
            return "laplace_gaussian"
        return "laplace_laplace"

    df["task_type"] = df["task_name"].apply(get_task_type)

    return df, tasks


def alpha_variance_frequencies(
    tasks_df: pd.DataFrame, n_bins=7, sigma=0
) -> pd.DataFrame:
    def map_range_to_bin(alpha):
        return ALPHAS.index(alpha)

    df = tasks_df.copy()
    df["bin_id"] = df["best_alpha"].apply(map_range_to_bin)

    count_by_bin = dict(df.groupby("bin_id").count().task_id)
    def map_bin_to_freq(k, center=ALPHAS.index(5)):
        if sigma == 0:
            if k == center:
                return 1 / count_by_bin[k]
            else:
                return 0
        # Kind of discrete Gaussian distribution to choose the bin, then uniformly at random inside each bin
        return np.exp((k - center) ** 2 / (2 * sigma ** 2)) / count_by_bin[k]

    df["frequency"] = df["bin_id"].apply(map_bin_to_freq)
    # We normalize (we chopped off the last bins, + some error is possible)
    df["frequency"] = df["frequency"] / df["frequency"].sum()

    return df


def geometric_frequencies(tasks_df: pd.DataFrame, n_bins=20, p=0.5) -> pd.DataFrame:
    def map_range_to_bin(r):
        return int(r * n_bins)

    df = tasks_df.copy()
    df["bin_id"] = df["epsilon_range"].apply(map_range_to_bin)

    count_by_bin = list(df.groupby("bin_id").count().epsilon_range)

    def map_bin_to_freq(k):
        # Geometric distribution to choose the bin, then uniformly at random inside each bin
        return (1 - p) ** (k - 1) * p / count_by_bin[k]

    df["frequency"] = df["bin_id"].apply(map_bin_to_freq)
    # We normalize (we chopped off the last bins, + some error is possible)
    df["frequency"] = df["frequency"] / df["frequency"].sum()

    return df


def gaussian_block_distribution(block_avg, block_std, max_blocks, **kwargs):

    if block_std == 0:
        return f"{block_avg}:1"

    f = []
    for k in range(1, max_blocks + 1):
        f.append(
            scipy.stats.norm.pdf(k, block_avg, block_std)
        )
    f = np.array(f)
    f = f / sum(f)

    name_and_freq = []
    for k, freq in enumerate(f):
        name_and_freq.append(f"{k+1}:{float(freq)}")

    return ",".join(name_and_freq)


def sample_from_gaussian_block_distribution(block_avg, block_std, max_blocks, **kwargs):
    stochastic_string = gaussian_block_distribution(block_avg, block_std, max_blocks)
    return int(sample_one_from_string(stochastic_string))


def plot_curves_stats(alphas_df, tasks_path):
    figs = {}

    title = "_RDP curves"
    fig = px.line(
        alphas_df.sort_values("alphas"),
        x="alphas",
        y="normalized_epsilons",
        color="task_name",
        log_y=True,
        log_x=True,
        title=title,
    )
    fig.update_layout(showlegend=False)
    figs[title] = fig

    title = "_RDP curves by best alpha"
    fig = px.line(
        alphas_df.sort_values("alphas"),
        x="alphas",
        y="normalized_epsilons",
        color="task_name",
        log_y=True,
        log_x=True,
        height=1200,
        facet_row="best_alpha",
        title=title,
    )
    fig.update_layout(showlegend=False)
    figs[title] = fig

    title = "_Best eps by best alpha"
    figs[title] = px.scatter(
        alphas_df.query("alphas == best_alpha"),
        x="alphas",
        y="epsilon_min",
        color="task_type",
        log_y=True,
        log_x=True,
        title=title,
    )

    title = "_Dominant share by best alpha"
    figs[title] = px.scatter(
        alphas_df.query("alphas == best_alpha"),
        x="alphas",
        y="epsilon_max",
        color="task_type",
        log_y=True,
        log_x=True,
        title=title,
    )

    title = "_Range by best alpha"
    figs[title] = px.scatter(
        alphas_df.query("alphas == best_alpha"),
        x="alphas",
        y="epsilon_range",
        color="task_type",
        log_y=True,
        log_x=True,
        title=title,
    )

    if "n_blocks" in alphas_df.columns:
        title = "_Blocks by dominant share, for each best alpha"
        figs[title] = px.scatter(
            alphas_df.query("alphas == best_alpha"),
            x="epsilon_max",
            y="n_blocks",
            color="task_type",
            log_y=False,
            log_x=True,
            facet_row="best_alpha",
            height=1200,
            title=title,
        )

    for title, fig in figs.items():
        fig_path = tasks_path.joinpath(f"{title}.png")
        fig.write_image(fig_path)
