from typing import Dict, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from python_runner.python_action_wrapper import ActionService
    from logging import Logger

from st2client.client import Client
from st2client.models.policy import Policy
from st2common.runners.base_action import Action


# Never try to delay these actions
NEVER_MANAGE_ACTIONS = [
    # do not stop self from running or we won't be able to resume!
    "st2gitops.delay_new_pack_executions",
    # This action should run as part of st2gitops.deploy_pack
    # so, don't block other actions used by that workflow:
    "st2gitops.get_pack_commit_hash",
    "st2gitops.manage_pack_resources",
    "st2gitops.suspend_st2_rules",
    "st2gitops.wait_or_pause_running_pack_executions",
    "st2gitops.unpause_pack_executions",
    # We DO want a policy for st2gitops.deploy_pack
    # so that no new deployments start until any current runs complete.
]

# The pack to put these dynamic policies in.
# Do not use st2gitops, or unloading will remove all the policies too.
POLICY_PACK = "__st2gitops__"
POLICY_PREFIX = "delay"


class DelayNewPackExecutions(Action):

    if TYPE_CHECKING:
        action_service: ActionService
        logger: Logger

    def __init__(self, config=None, action_service=None):
        super().__init__(config, action_service)
        self.client = Client()
        self.check_mode = False

    def run(self, from_packs: list = None, action: str = None, check_mode=False):
        self.check_mode = check_mode
        results = {"success": True, "packs": {}}

        if len(from_packs) == 1:
            packs = {from_packs[0]: self.client.packs.get_by_ref_or_id(from_packs[0])}
        else:
            packs = {p.ref: p for p in self.client.packs.get_all()}

        self.logger.debug(f"Got {len(packs)} packs")

        packs_results: Dict[str, Dict[str, Tuple[bool, str]]] = {}
        # {pack_name: {action_name: (success, policy_name)}}

        if self.check_mode:
            self.logger.info("check_mode enabled. Simulating policy changes...")

        for pack_name in from_packs:
            pack = packs.get(pack_name)
            if not pack:
                self.logger.debug(
                    f"Pack {pack_name} not found. Nothing to delay. Continuing..."
                )
                continue
            if action == "delay":
                packs_results[pack_name] = self.delay_executions(
                    pack_name=pack_name, pack=pack
                )
            elif action == "resume":
                packs_results[pack_name] = self.resume_executions(
                    pack_name=pack_name, pack=pack
                )

        results["success"] = all(
            action_result[0]  # action_result = (success, policy_ref)
            for pack_results in packs_results.values()
            for action_result in pack_results.values()
        )
        results["packs"] = packs_results
        return results

    def delay_executions(self, pack_name, pack) -> Dict[str, Tuple[bool, str]]:
        self.logger.info(f"Delaying all executions for actions in pack: {pack_name}")
        results = {}

        actions = self.client.actions.get_all(pack=pack_name)
        for action in actions:
            if action.ref in NEVER_MANAGE_ACTIONS:
                self.logger.debug(
                    f"Skipping {action.ref} as it is one of the NEVER_MANAGE_ACTIONS."
                )
                # The action MUST not manage itself or other prohibited actions
                continue
            policy_instance = Policy(
                pack=POLICY_PACK,
                name=f"{POLICY_PREFIX}.{action.ref}",
                enabled=True,
                policy_type="action.concurrency",
                parameters={
                    "action": "delay",
                    "threshold": 0,
                },
                resource_ref=action.ref,
            )
            if self.check_mode:
                policy = policy_instance
                policy.ref = f"{policy.pack}.{policy.name}"
            else:
                policy = self.client.policies.create(policy_instance)
            results[action.ref] = (True, policy.ref)
            self.logger.debug(
                f"Delayed executions for action={action.ref} "
                f"by creating action.concurrency policy={policy.ref} with threshold=0"
            )

        return results

    def resume_executions(self, pack_name, pack) -> Dict[str, Tuple[bool, str]]:
        self.logger.info(
            f"Resuming/Re-allowing executions for actions in pack: {pack_name}"
        )
        results = {}

        policies = self.client.policies.get_all(pack=POLICY_PACK)
        for policy in policies:
            if not policy.name.startswith(f"{POLICY_PREFIX}.{pack_name}"):
                continue

            if self.check_mode:
                success = True
            else:
                # policy.enabled = False
                # policy = self.client.policies.update(policy)

                success = self.client.policies.delete(policy)
            results[policy.resource_ref] = (success, policy.ref)
            self.logger.debug(
                f"Resumed/Re-allowed executions for action={policy.resource_ref} "
                f"by deleting action.concurrency policy={policy.ref}"
            )

        return results


if __name__ == "__main__":
    test_action = DelayNewPackExecutions(config={})
    res = test_action.run(from_packs=[POLICY_PACK], action="delay")
    print(res)
