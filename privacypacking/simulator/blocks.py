from itertools import count

from loguru import logger

from privacypacking.simulator.resourcemanager import LastItem


class Blocks:
    """
    Model block arrival.
    """

    def __init__(self, environment, resource_manager):
        self.env = environment
        self.resource_manager = resource_manager
        self.config = resource_manager.config
        self.blocks_count = count()
        self.env.process(self.block_producer())


    def block_producer(self):
        """
        Generate blocks.
        """
        # Produce initial blocks
        initial_blocks_num = self.config.get_initial_blocks_num()
        for _ in range(initial_blocks_num):
            self.env.process(self.block(next(self.blocks_count)))
        logger.info("done with initial blocks")

        for _ in range(self.config.omegaconf.blocks.max_num - initial_blocks_num):
            block_arrival_interval = self.config.set_block_arrival_time()
            block_id = next(self.blocks_count)
            self.env.process(self.block(block_id))

            yield self.env.timeout(block_arrival_interval)

        if initial_blocks_num != self.config.omegaconf.blocks.max_num:
            # Send a special message to close the channel
            self.resource_manager.new_blocks_queue.put(LastItem())

        if not self.resource_manager.block_production_terminated.triggered:
            self.resource_manager.block_production_terminated.succeed()
            
    def block(self, block_id):
        """
        Block behavior. Sets its own budget, notifies resource manager of its existence,
        waits till it gets generated
        """
        block = self.config.create_block(block_id)
        generated_block_event = self.env.event()
        yield self.resource_manager.new_blocks_queue.put((block, generated_block_event))
        yield generated_block_event
        logger.debug(f"Block: {block_id} generated at {self.env.now}")
