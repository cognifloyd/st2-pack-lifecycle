# this is based on an action by @emptywee

from __future__ import (absolute_import, division, print_function)
from st2client.client import Client
from st2common.runners.base_action import Action
from st2client.models import KeyValuePair
import json

__all__ = ["SuspendSt2Rules"]

DATASTORE_KEY = 'st2gitops_rules_suspended'
DATASTORE_KEY_TTL = 1 * 86400  # 1 day


class SuspendSt2Rules(Action):

    class MissingRuleException(Exception):
        """Rule raised when attempting to modify a rule that does not exist."""

    def run(self, from_packs=None, action=None):
        client = Client()
        if action == 'suspend':
            res = self.suspend_and_save(client, from_packs)
        elif action == 'resume':
            res = self.reinstate_rules(client)
        else:
            self.logger.error('Unknown action: {}'.format(action))
            res = False

        return res

    def reinstate_rules(self, client):
        try:
            suspended_in_past = client.keys.get_by_name(name=DATASTORE_KEY)
        except Exception as exc:
            self.logger.exception("Failed to access datastore "
                                  "to load key {}: {}".format(DATASTORE_KEY, exc))
            return False
        if not suspended_in_past:
            self.logger.error('Cannot reinstate rules, key {} is not set '
                              'in the datastore'.format(DATASTORE_KEY))
            return False
        rules_state = json.loads(suspended_in_past.value)
        for rule in rules_state:
            self.logger.debug("Re-setting state of rule {}.{} to {}".format(rule['pack'],
                                                                            rule['name'],
                                                                            rule['enabled']))
            try:
                res = self.manage_rule(client, rule['pack'], rule['name'], rule['enabled'])
            except self.MissingRuleException as e:
                self.logger.warning("Ignore missing rule {}.{}: {}".format(rule['pack'], rule['name'], e))
                continue

            if isinstance(res, str):
                # error happened
                self.logger.error("Error re-setting state "
                                  "for rule {}.{} to {}: {}".format(rule['pack'], rule['name'],
                                                                    rule['enabled'], res))
                return False
            else:
                self.logger.info("Set rule {}.{} -> {}".format(rule['pack'], rule['name'],
                                                               rule['enabled']))
        # and delete the datastore key once we've reset the state of the rules
        try:
            client.keys.delete(suspended_in_past)
            self.logger.info("Removed datastore key {}".format(DATASTORE_KEY))
        except Exception as exc:
            self.logger.exception("Failed to remove datastore key {}: {}".format(DATASTORE_KEY,
                                                                                 exc))
            return False

        return True

    def suspend_and_save(self, client, from_packs):
        rules_state = []
        try:
            for pack in from_packs:
                rules = client.rules.get_all(pack=pack)
                for rule in rules:
                    if rule.pack in from_packs:
                        self.logger.debug("Saving state {} of rule {}.{}".format(rule.enabled,
                                                                                 rule.pack,
                                                                                 rule.name))
                        rules_state.append({
                            'name': rule.name,
                            'pack': rule.pack,
                            'enabled': rule.enabled
                        })
        except Exception as exc:
            self.logger.exception("Failed to get rules from stackstorm: {}".format(exc))
            return False

        try:
            suspended_in_past = client.keys.get_by_name(name=DATASTORE_KEY)
        except Exception as exc:
            self.logger.exception("Failed to check datastore for "
                                  "existing key {}: {}".format(DATASTORE_KEY, exc))
            return False

        if suspended_in_past:
            self.logger.warning('{} datastore key exists, not overwriting'.format(DATASTORE_KEY))
        else:
            client.keys.update(KeyValuePair(name=DATASTORE_KEY, ttl=DATASTORE_KEY_TTL,
                                            value=json.dumps(rules_state)))

        for rule in rules_state:
            try:
                res = self.manage_rule(client, rule['pack'], rule['name'], False)
            except self.MissingRuleException as e:
                res = str(e)
            if isinstance(res, str):
                # error happened
                self.logger.error("Error disabling rule {}.{}: {}".format(rule['pack'],
                                                                          rule['name'], res))
                return False
            else:
                self.logger.info("Disabled rule {}.{}".format(rule['pack'], rule['name']))

        return True

    def manage_rule(self, client, pack, name, enabled):
        rule_name = '{}.{}'.format(pack, name)
        failure_reason = None
        rule = None
        try:
            rule = client.rules.get_by_name(name=name, pack=pack)
            if not rule:
                failure_reason = 'rule not found'
        except Exception as exc:
            failure_reason = exc

        if failure_reason:
            raise self.MissingRuleException('Could not get rule {}: {}'.format(rule_name, failure_reason))

        try:
            rule_enabled = rule.enabled
        except AttributeError:
            # this should not happen, but better be safe than sorry
            self.logger.debug("Hmm, rule {}.{} doesn't have attribute 'enabled', "
                              "assuming it's off then.".format(name, pack))
            rule_enabled = False

        if rule_enabled == enabled:
            # already enabled/disabled, so just return true and formatted results
            return rule

        try:
            rule.enabled = enabled

            client.rules.update(rule)
        except Exception as exc:
            return 'Could not update rule {}: {}'.format(rule_name, exc)


if __name__ == '__main__':
    act = SuspendSt2Rules(config={})
    result = act.run(from_packs=['st2autoremediation'], action='resume')
    print(result)
