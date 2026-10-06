"""Tests for work, sleep, and shop purchase."""

import unittest

from entities import Player
from world import WorldState


class PlayerLifeTests(unittest.TestCase):
    def test_work_advances_time_and_changes_money_energy(self):
        world = WorldState(time=0.0, player=Player(50.0, 50.0, money=100.0, energy=100.0))

        success, _ = world.work()

        self.assertTrue(success)
        self.assertEqual(world.time, 8 * 3600)
        self.assertEqual(world.player.money, 180.0)
        self.assertEqual(world.player.energy, 70.0)

    def test_sleep_restores_energy_up_to_100(self):
        world = WorldState(player=Player(50.0, 50.0, energy=20.0))

        success, _ = world.sleep()

        self.assertTrue(success)
        self.assertEqual(world.player.energy, 100.0)
        self.assertEqual(world.time, 8 * 3600)

    def test_buy_reduces_money_and_adds_item(self):
        world = WorldState(player=Player(50.0, 50.0, money=100.0))

        success, _ = world.buy("shop_bread")

        self.assertTrue(success)
        self.assertEqual(world.player.money, 95.0)
        self.assertIn("shop_bread", world.player.inventory)

    def test_buy_fails_without_enough_money(self):
        world = WorldState(player=Player(50.0, 50.0, money=1.0))

        success, _ = world.buy("shop_medicine")

        self.assertFalse(success)
        self.assertEqual(world.player.money, 1.0)


if __name__ == "__main__":
    unittest.main()