# DPack

This repository contains the artifact for "Efficiently Packing Privacy Budget with DPack", published at EuroSys '25. The artifact contains three main components:
- `PrivateKube`: A fork of PrivateKube (OSDI '21) which implements the DPack scheduler in addition to the original DPF scheduler.
- `alibaba-dp-workload`: The Alibaba-DP Workload, a benchmark to evaluate secheduling algorithms for differential privacy,
- `privacypacking`: a Python simulator to specify and evaluate scheduling algorithms in various settings.

## Running experiments

In this section, we provide instructions to reproduce certain experiments from the paper, using the Alibaba-DP workload and the Python simulator.

### 0. Requirements

The simulator and the Alibaba trace need to run in a Python environment with the dependencies listed in `pyproject.toml`. We recommend [Poetry](https://python-poetry.org), which can be installed with:

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

Additionally, certain microbenchmarks need a local Gurobi license to compute the optimal allocation, which is [free for academics](https://www.gurobi.com/academia/academic-program-and-licenses/). Gurobi is not required for the Alibaba macrobenchmarks, which only uses heuristics.

Trace generation and experiments need less than 5Gb of free disk space. We recommend using a machine with at least 64Gb of RAM and 16 CPU cores.


### 1. Initialization

First, clone the current repository and go to its root directory:

```bash
git clone git@github.com:columbia/dpack.git
cd dpack
```

Then, initialize a new Python environment with the right dependencies. For instance, with Poetry:

```bash
poetry shell
poetry install
```

### 2. Preparing the Alibaba data

Fetch our code for the Alibaba-DP workload:
```bash
git submodule init && git submodule update
```

Download the original Alibaba trace. This step should take about 5 minutes:
```bash 
bash alibaba-dp-workload/download_alibaba_data.sh
```

Install the Alibaba package locally. Make sure you run this step from a Python environment with all the dependencies (see Step 1).
```bash
pip install -e alibaba-dp-workload
```

Generate the Alibaba-DP trace. This step can take one or two hours:
```bash
python alibaba-dp-workload/alibaba_privacy_workload/generate.py
```

Copy the Alibaba-DP trace to the simulator's data directory:
```bash
mkdir -p data/alibaba && cp alibaba-dp-workload/outputs/privacy_tasks_30_days.csv data/alibaba/
```

### 3. Running an experiment

The following command runs an experiment comparing the performance of DPF, DPack and FCFS on the Alibaba trace, when we vary the number of blocks. This is similar to Fig 6b of the paper. It takes about 2 hours to run. 

```bash
python experiments/plot_alibaba.py
```

You can see the raw numbers in the logs, and also a figure generated in `experiments/figures/alibaba`.
