import simpy
from loguru import logger

from privacypacking.schedulers.methods import initialize_scheduler


class LastItem:
    def __init__(self):
        return


class ResourceManager:
    """
    Managing blocks and tasks arrival and schedules incoming tasks.
    """

    def __init__(self, environment, configuration):
        self.env = environment
        self.config = configuration

        # To store the incoming tasks and blocks
        self.new_tasks_queue = simpy.Store(self.env)
        self.new_blocks_queue = simpy.Store(self.env)

        # Initialize the scheduler
        self.scheduler = initialize_scheduler(self.config, self.env)
        self.blocks_initialized = self.env.event()

        # Stopping conditions
        self.block_production_terminated = self.env.event()
        self.task_production_terminated = self.env.event()
        self.simulation_terminated = self.env.event()

    def terminate_simulation(self):
        self.scheduling.interrupt()
        self.daemon_clock.interrupt()

    def start(self):
        # Start the processes
        self.env.process(self.block_consumer())
        task_consumed_event = self.env.process(self.task_consumer())

        # In the online case, start a clock to terminate the simulation
        if self.config.omegaconf.scheduler.method == "batch":
            self.block_arrival_interval = self.config.set_block_arrival_time()
            self.daemon_clock = self.env.process(self.daemon_clock())
            self.env.process(self.termination_clock())
            self.scheduling = self.env.process(
                self.scheduler.run_batch_scheduling(
                    simulation_termination_event=self.simulation_terminated,
                    period=self.config.omegaconf.scheduler.scheduling_wait_time,
                )
            )

        elif self.config.omegaconf.scheduler.method == "offline":
            self.block_production_terminated.succeed()
            self.task_production_terminated.succeed()

            yield task_consumed_event
            logger.info(
                "The scheduler has consumed all the tasks. Time to allocate them."
            )
            self.scheduler.schedule_queue()

    def termination_clock(self):
        # Wait for all the blocks to be produced before moving on
        yield self.block_production_terminated
        logger.info(
            f"Block production terminated at {self.env.now}.\n Producing tasks for the last block..."
        )

        yield self.env.timeout(self.block_arrival_interval)

        # All the blocks are here, time to stop creating online tasks
        if not self.task_production_terminated.triggered:
            self.task_production_terminated.succeed()
            logger.info(
                f"Task production terminated at {self.env.now}.\n Unlocking the remaining budget and allocating available tasks..."
            )

        # We even wait a bit longer to ensure that all tasks are allocated (in case we need multiple scheduling steps)
        yield self.env.timeout(self.config.omegaconf.scheduler.data_lifetime)
        logger.info(f"Terminating the simulation at {self.env.now}. Closing...")

    def daemon_clock(self):
        while not self.simulation_terminated.triggered:
            try:
                yield self.env.timeout(1)
                logger.info(f"Simulation Time is: {self.env.now}")
            except simpy.Interrupt as i:
                return

    def block_consumer(self):
        def consume():
            item = yield self.new_blocks_queue.get()
            if isinstance(item, LastItem):
                return
            block, generated_block_event = item
            self.scheduler.add_block(block)
            generated_block_event.succeed()

        # Consume all initial blocks
        initial_blocks_num = self.config.get_initial_blocks_num()
        for _ in range(initial_blocks_num):
            yield self.env.process(consume())
        self.blocks_initialized.succeed()
        logger.info(f"Initial blocks: {len(self.scheduler.blocks)}")

        while not self.block_production_terminated.triggered:
            yield self.env.process(consume())

        logger.info("Done producing blocks.")

    def task_consumer(self):
        def consume():
            task_message = yield self.new_tasks_queue.get()
            if isinstance(task_message, LastItem):
                return
            return self.scheduler.add_task(task_message)

        # Consume initial tasks
        initial_tasks_num = self.config.get_initial_tasks_num()
        for _ in range(initial_tasks_num):
            yield self.env.process(consume())
        logger.info("Done consuming initial tasks")

        while not self.task_production_terminated.triggered:
            yield self.env.process(consume())
        logger.info("Done consuming tasks")
        
        self.simulation_terminated.succeed()