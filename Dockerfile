
# syntax=docker/dockerfile:1

FROM python:3.8

WORKDIR /dpack

COPY . .

SHELL ["/bin/bash", "-c"]

ENV PATH="/root/.local/bin:$PATH"
ENV LOGURU_LEVEL=ERROR

RUN set -xe
RUN apt update && apt install nano
RUN curl -sSL https://install.python-poetry.org | python - --git https://github.com/python-poetry/poetry.git@master

RUN poetry config installer.max-workers 10
RUN poetry config virtualenvs.create false
RUN poetry install --no-interaction --no-ansi -vvv

RUN chmod 777 ./packaging/create_benchmarks.sh

# Create Alibaba-DP trace
RUN ./packaging/create_benchmarks.sh
ENTRYPOINT ["/bin/bash"]
