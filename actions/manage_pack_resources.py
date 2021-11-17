import pathlib

from collections import defaultdict
from typing import DefaultDict, Dict, Tuple, TYPE_CHECKING, Union
from urllib.parse import urlparse

import yaml

if TYPE_CHECKING:
    from python_runner.python_action_wrapper import ActionService

from st2client.client import Client
from st2common.config import cfg
from st2common.runners.base_action import Action


# These are known resources that we can enable/disable
# key is resource_type in pack_resources.yaml
# value is resource_type used in st2client.client.Client.managers
RESOURCE_TYPES_MAP = {
    "rules": "Rule",
    "policies": "Policy",
    "sensors": "Sensor",
    "triggers": "Trigger",
    "actions": "Action",
    "aliases": "ActionAlias",
}


class ManagePackResources(Action):

    if TYPE_CHECKING:
        action_service: ActionService

    def __init__(self, config=None, action_service=None):
        super().__init__(config, action_service)
        webui_base_url = urlparse(cfg.CONF.webui.webui_base_url)
        self.webui_base_domain: str = webui_base_url.hostname
        self.client = Client()
        self.check_mode = False

    def run(self, from_packs: list = None, check_mode=False):
        self.check_mode = check_mode

        if len(from_packs) == 1:
            packs = {from_packs[0]: self.client.packs.get_by_ref_or_id(from_packs[0])}
        else:
            packs = {p.ref: p for p in self.client.packs.get_all()}

        results = {}
        for pack_name in from_packs:
            pack = packs[pack_name]
            results[pack_name] = self.resources_in_pack(pack_name, pack)

        success = all(
            result["want_enabled"] == result["enabled_after"]
            for pack_results in results.values()
            for resource_results in pack_results.values()
            for result in resource_results.values()
        )

        return {"success": success, "packs": results}

    def resources_in_pack(
        self, pack_name, pack
    ) -> Dict[str, Dict[str, Dict[str, Union[bool, str]]]]:
        results: DefaultDict[str, Dict[str, Dict[str, Union[bool, str]]]] = defaultdict(
            dict
        )
        # {resource_type: {resource_name:
        #   {want_enabled: bool, enabled_before: bool, enabled_after: bool, error_message: str}
        # }}

        pack_path = pathlib.Path(pack.path)
        resources_file_path = pack_path / "pack_resources.yaml"
        if not resources_file_path.exists():
            return results

        with resources_file_path.open("r") as resources_file:
            all_resources = yaml.safe_load(resources_file)
        resources = all_resources.get(self.webui_base_domain, {})

        for resource_type, resource_list in resources.items():
            for resource_name in resource_list:
                client_resource_type = RESOURCE_TYPES_MAP.get(resource_type)
                if not client_resource_type:
                    # ignore unknown resource_type
                    continue

                # we support <pack>.<resource> and just <resource>
                pack_prefix = f"{pack_name}."
                pack_prefix_len = len(pack_prefix)
                name = (
                    resource_name[pack_prefix_len:]
                    if resource_name.startswith(pack_prefix)
                    else resource_name
                )

                # TODO: handle ^resource_name where ^ means enabled=False

                result = self.manage_resource(
                    resource_type=client_resource_type,
                    name=name,
                    pack=pack_name,
                    enabled=True,
                )
                results[resource_type][resource_name] = result
        return results

    def manage_resource(
        self, resource_type: str, name: str, pack: str, enabled: bool
    ) -> Dict[str, Union[bool, str]]:
        # we use self.client.managers instead of self.client.<resource type> because
        # not all resources are available as properties on the client.
        resource = self.client.managers[resource_type].get_by_name(name=name, pack=pack)
        enabled_before = getattr(resource, "enabled", False)
        result = {
            "want_enabled": enabled,
            "enabled_before": enabled_before,
            "enabled_after": None,
            "error_message": "",
        }

        if self.check_mode:
            result["enabled_after"] = enabled
            return result

        try:
            resource.enabled = result["enabled_after"] = enabled
            self.client.managers[resource_type].update(resource)
        except Exception as exc:
            result["enabled_after"] = enabled_before
            result[
                "error_message"
            ] = f"Could not update {resource_type} {pack}.{name}: {exc}"
        return result


if __name__ == "__main__":
    action = ManagePackResources(config={})
    res = action.run(from_packs=["st2gitops"], check_mode=True)
    print(res)
