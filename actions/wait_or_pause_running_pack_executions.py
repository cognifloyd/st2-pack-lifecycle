import time
from collections import defaultdict
from typing import Dict, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from python_runner.python_action_wrapper import ActionService
    from logging import Logger

from st2client.client import Client
from st2client.commands.action import LIVEACTION_STATUS_RUNNING
from st2common.runners.base_action import Action


# Never try to pause or wait for these actions
NEVER_MANAGE_ACTIONS = [
    # This action should run as part of st2gitops.deploy_pack
    # so, don't pause the st2gitops.deploy_pack workflow.
    "st2gitops.deploy_pack",
    # these actions get run by st2gitops.deploy_pack, including this one.
    "st2gitops.delay_new_pack_executions",
    "st2gitops.get_pack_commit_hash",
    "st2gitops.manage_pack_resources",
    "st2gitops.suspend_st2_rules",
    "st2gitops.wait_or_pause_running_pack_executions",
    "st2gitops.unpause_pack_executions",
]

WORKFLOW_RUNNERS = ["orquesta", "action-chain"]


class WaitOrPauseRunningPackExecutions(Action):

    if TYPE_CHECKING:
        action_service: ActionService
        logger: Logger

    def __init__(self, config=None, action_service=None):
        super().__init__(config, action_service)
        self.client = Client()

    def run(self, from_packs: list = None):
        results = {"success": True, "packs": {}}

        packs_with_paused = defaultdict(set)

        # First try. We'll loop back through one more time to retry if needed.
        retry_packs = []
        for pack_name in from_packs:
            (
                hit_timeout_for_simple,
                hit_timeout_for_all,
                executions_running,
                paused_workflows,
            ) = self.wait_or_pause(pack_name)
            if executions_running:
                retry_packs.append(pack_name)
            if paused_workflows:
                for execution in paused_workflows:
                    packs_with_paused[pack_name].add(execution.id)

        # Try one more time to pause anything that is still running
        succeeded_packs = []
        failed_packs = {}
        for pack_name in retry_packs:
            (
                hit_timeout_for_simple,
                hit_timeout_for_all,
                executions_running,
                paused_workflows,
            ) = self.wait_or_pause(pack_name)
            if not executions_running:
                succeeded_packs.append(pack_name)
            else:
                failed_packs[pack_name] = {
                    "hit_timeout_for_simple": hit_timeout_for_simple,
                    "hit_timeout_for_all": hit_timeout_for_all,
                    "executions_running": [
                        {
                            "id": execution.id,
                            "status": execution.status,
                            "action": execution.action["ref"],
                            "runner": execution.action["runner_type"],
                            "start_timestamp": execution.start_timestamp,
                            "user": execution.context["user"],
                        }
                        for execution in executions_running
                    ],
                }
            if paused_workflows:
                for execution in paused_workflows:
                    packs_with_paused[pack_name].add(execution.id)

        results["success"] = not failed_packs
        results["packs_with_no_running_executions"] = succeeded_packs
        results["packs_with_running_executions"] = failed_packs
        results["paused_executions_in_packs"] = dict(packs_with_paused)
        return results

    def wait_or_pause(self, pack_name, timeout_seconds=120):
        (
            hit_timeout_for_simple,
            executions_running,
            workflows_running,
        ) = self._wait_for_simple_executions(
            pack_name=pack_name, timeout_seconds=timeout_seconds / 2
        )
        paused = []
        for execution in workflows_running:
            self.logger.info(f"Pausing workflow execution={execution.id}")
            self.client.executions.pause(execution.id)
            paused.append(execution)
        hit_timeout_for_all, executions_running = self._wait_for_all_executions(
            pack_name=pack_name, timeout_seconds=timeout_seconds / 2
        )
        return hit_timeout_for_simple, hit_timeout_for_all, executions_running, paused

    def _get_running_executions(self, pack_name):
        attributes = [
            "id",
            "status",
            "start_timestamp",
            "action.ref",
            "action.pack",
            "action.name",
            "action.runner_type",
            "context.user",
        ]
        executions = self.client.executions.query(
            pack=pack_name,
            status=LIVEACTION_STATUS_RUNNING,
            include_attributes=",".join(attributes),
        )
        executions = [
            execution
            for execution in executions
            # Check pack because the API is returning more than requested (at least in 3.4.1).
            if execution.action["pack"] == pack_name
            # And skip anything in NEVER_MANAGE_ACTIONS
            and execution.action["ref"] not in NEVER_MANAGE_ACTIONS
        ]
        return executions

    def _wait_for_simple_executions(
        self, pack_name, timeout_seconds
    ) -> Tuple[bool, list, list]:
        self.logger.info(
            f"Waiting for any running executions for simple (non-workflow) actions in pack: {pack_name}"
        )

        executions = []
        workflow_executions = []

        start_time = time.time()
        end_time = start_time + timeout_seconds
        while time.time() < end_time:
            executions = self._get_running_executions(pack_name)
            if not executions:
                self.logger.info(
                    f"Done! No running executions of actions in pack: {pack_name}"
                )
                return False, [], []

            workflow_executions = [
                execution
                for execution in executions
                if execution.action["runner_type"] in WORKFLOW_RUNNERS
            ]
            if len(workflow_executions) == len(executions):
                self.logger.info(
                    f"Done! No running executions for non-workflow actions in pack: {pack_name}"
                )
                return False, executions, workflow_executions

            time.sleep(1)
        return True, executions, workflow_executions

    def _wait_for_all_executions(self, pack_name, timeout_seconds) -> Tuple[bool, list]:
        self.logger.info(
            f"Waiting for any remaining running executions to pause of finish for actions in pack: {pack_name}"
        )
        executions = []

        start_time = time.time()
        end_time = start_time + timeout_seconds
        while time.time() < end_time:
            executions = self._get_running_executions(pack_name)
            if not executions:
                self.logger.info(
                    f"Done! No running executions of actions in pack: {pack_name}"
                )
                return False, []
            time.sleep(1)
        return True, executions


if __name__ == "__main__":
    test_action = WaitOrPauseRunningPackExecutions(config={})
    res = test_action.run(from_packs=["st2gitops"])
    print(res)
