import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from sea_explorer_bot import SeaExplorerBot, Vision, load_config


class EconomyGateTests(unittest.TestCase):
    def _bot(self):
        bot=SeaExplorerBot.__new__(SeaExplorerBot)
        bot.cfg=load_config()
        bot.stop_event=None
        bot.dry_run=False
        bot.sweeper=Mock()
        bot.adb=Mock()
        bot.vision=Mock()
        bot.home_handled=False
        bot.progress=SimpleNamespace(
            oxygen_level_estimate=230,
            runs_completed=1,
            last_bag_attempt_run=-999,
            save=Mock(),
        )
        bot._start_next_dive=Mock(return_value=True)
        bot._attempt_upgrade=Mock(return_value=True)
        bot._bag_cadence=Mock(return_value=1)
        return bot

    def test_buying_off_never_attempts_upgrade(self):
        bot=self._bot()
        bot.cfg["economy"]["buy_upgrades"]=False
        frame=np.zeros((1584,720,3),np.uint8)
        bot._handle_home(frame)
        bot._attempt_upgrade.assert_not_called()
        bot._start_next_dive.assert_called_once_with(frame)

    def test_unaffordable_prices_never_get_tapped(self):
        bot=self._bot()
        bot.cfg["economy"]["buy_upgrades"]=True
        bot.cfg["economy"]["upgrade_mode"]="smart"
        bot.vision.read_home_economy.return_value={
            "coins":50,"bag_cost":100,"oxygen_cost":161,"confidence":{}
        }
        frame=np.zeros((1584,720,3),np.uint8)
        bot._handle_home(frame)
        bot._attempt_upgrade.assert_not_called()
        bot._start_next_dive.assert_called_once_with(frame)

    def test_digit_templates_are_available(self):
        vision=Vision(load_config())
        self.assertIsNotNone(vision._digit_templates)
        self.assertEqual(vision._digit_templates.shape,(10,44,32))


if __name__=="__main__":
    unittest.main()
