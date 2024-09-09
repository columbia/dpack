bash alibaba-dp-workload/download_alibaba_data.sh
mv cluster-trace-gpu-v2020 alibaba-dp-workload
pip install -e alibaba-dp-workload
python alibaba-dp-workload/alibaba_privacy_workload/generate.py
mkdir -p data/alibaba && cp alibaba-dp-workload/outputs/privacy_tasks_30_days.csv data/alibaba/