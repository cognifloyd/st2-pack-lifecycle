from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python_runner.python_action_wrapper import ActionService
    from logging import Logger

from st2client.client import Client
from st2common.runners.base_action import Action


class UnpausePackExecutions(Action):

    if TYPE_CHECKING:
        action_service: ActionService
        logger: Logger

    def __init__(self, config=None, action_service=None):
        super().__init__(config, action_service)
        self.client = Client()

    def run(self, executions: list = None):
        results = {}
        if not executions:
            return results

        for execution_id in executions:
            results[execution_id] = self.client.executions.resume(execution_id)

        return results
