from pathlib import Path
import os

import plotly.express as px

from ray_runner import grid_online


def plot_alibaba():
    """
    Typical runtime: ~15 min per group of [Dpack, DPF, FCFS], most of the time spent in DPack
    """
    fig_dir = Path(__file__).parent.joinpath("figures")

    rdf = grid_online(
        scheduler_scheduling_time=[10],
        metric_recomputation_period=[50],
        initial_blocks=[100],
        max_blocks=[100 + i for i in [30, 60, 90, 120, 180]],
        data_path=["alibaba/privacy_tasks_30_days.csv"],
        tasks_sampling="",
        data_lifetime=[30],
    )
    
    csv_path = fig_dir.joinpath("alibaba/alibaba.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rdf.to_csv(csv_path, index=False)

    fig = px.line(
        rdf.sort_values("max_blocks"),
        x="max_blocks",
        y="n_allocated_tasks",
        color="scheduler_metric",
        width=800,
        height=600,
        title="Alibaba",
    )

    fig_path = fig_dir.joinpath("alibaba/alibaba.png")
    fig.write_image(fig_path)


if __name__ == "__main__":
    os.environ["LOGURU_LEVEL"] = "WARNING"
    os.environ["TUNE_DISABLE_AUTO_CALLBACK_LOGGERS"] = "1"
    plot_alibaba()
