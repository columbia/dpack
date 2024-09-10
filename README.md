# DPack

This repository contains the artifact for "Efficiently Packing Privacy Budget with DPack", published at EuroSys '25. The artifact contains three main components:
- `PrivateKube`: A fork of PrivateKube (OSDI '21) which implements the DPack scheduler in addition to the original DPF scheduler.
- `alibaba-dp-workload`: The Alibaba-DP Workload, a benchmark to evaluate scheduling algorithms for differential privacy,
- `privacypacking`: a Python simulator to specify and evaluate scheduling algorithms in various settings.

## Running experiments

In this section, we provide instructions to reproduce certain experiments from the paper, using the Alibaba-DP workload and the Python simulator.

## 1. Requirements

Make sure you have a working installation of [`docker`](https://docs.docker.com/engine/install/ubuntu/).

Additionally, certain microbenchmarks need a local Gurobi license to compute the optimal allocation, which is [free for academics](https://www.gurobi.com/academia/academic-program-and-licenses/). Gurobi is not required for the Alibaba macrobenchmarks, which only uses heuristics.

Trace generation and experiments need less than 5Gb of free disk space. We recommend using a machine with at least 64Gb of RAM and 16 CPU cores.

## 2. Install DPack
### Download the code

Clone this repository on your machine:
```bash
git clone https://github.com/columbia/dpack.git
```

Enter the repository:
```bash
cd dpack
```

Fetch our code for the Alibaba-DP workload:
```bash
git submodule update --init --recursive
```

### Build the DPack docker
Build the docker image for DPack. This will automatically install all dependencies required for DPack as well as the Alibaba DP trace used in the evaluation of the paper.

```bash
sudo docker build --network host --shm-size=204.89gb -t dpack -f Dockerfile .
```

### 3. Running an experiment

The following command runs an experiment comparing the performance of DPF, DPack and FCFS on the Alibaba trace, when we vary the number of blocks. This is similar to Fig 6b of the paper. It takes about 2 hours to run. 

```bash
sudo docker run -v $PWD/logs:/dpack/logs -v $PWD/experiments:/dpack/experiments -v $PWD/dpack/config:/dpack/dpack/config -v $PWD/temp:/tmp --network=host --name dpack --shm-size=204.89gb --rm dpack packaging/run.sh
```

You can see the raw numbers in the logs, and also a figure generated in `experiments/figures/alibaba`.
