"""Tests for the v2 spatial, temporal, and persistence foundation."""

import tempfile
import unittest
from pathlib import Path

from entities import Item, NPC, Player
from persistence import load_world, save_world
from world import WorldState


class WorldStateTests(unittest.TestCase):
    def test_player_can_move_to_fractional_coordinates(self):
        world = WorldState(player=Player(10.0, 10.0))

        success, _ = world.move(12.5, 77.25)

        self.assertTrue(success)
        self.assertEqual(world.player.x, 12.5)
        self.assertEqual(world.player.y, 77.25)

    def test_out_of_bounds_move_is_rejected(self):
        world = WorldState(player=Player(10.0, 10.0))

        success, _ = world.move(100.01, 50.0)

        self.assertFalse(success)
        self.assertEqual((world.player.x, world.player.y), (10.0, 10.0))

        success, _ = world.move(50.0, -0.01)

        self.assertFalse(success)
        self.assertEqual((world.player.x, world.player.y), (10.0, 10.0))

    def test_pick_and_drop_item(self):
        item = Item(id="item_1", type="medkit", x=12.0, y=10.0)
        world = WorldState(
            player=Player(10.0, 10.0),
            items=[item],
        )

        picked, _ = world.pick("item_1")

        self.assertTrue(picked)
        self.assertIn("item_1", world.player.inventory)
        self.assertEqual(item.holder, "player")

        dropped, _ = world.drop("item_1", 30.5, 40.25)

        self.assertTrue(dropped)
        self.assertNotIn("item_1", world.player.inventory)
        self.assertIsNone(item.holder)
        self.assertEqual((item.x, item.y), (30.5, 40.25))

    def test_wait_advances_numeric_time(self):
        world = WorldState(time=5.5)

        success, _ = world.wait(10)

        self.assertTrue(success)
        self.assertEqual(world.time, 15.5)

    def test_distance_between_entities_and_coordinates(self):
        world = WorldState()
        first = Player(0.0, 0.0)
        second = NPC(id="npc_1", name="Mira", x=3.0, y=4.0)

        self.assertEqual(world.distance_between(first, second), 5.0)
        self.assertEqual(world.distance_between((0.0, 0.0), (6.0, 8.0)), 10.0)

    def test_save_and_load_preserves_world_state(self):
        world = WorldState(
            time=123.5,
            player=Player(10.25, 20.5, inventory=["item_1"]),
            npcs=[NPC(id="npc_1", name="Mira", x=30.75, y=40.25)],
            items=[
                Item(id="item_1", type="medkit", x=10.25, y=20.5, holder="player"),
                Item(id="item_2", type="food", x=60.0, y=70.0),
            ],
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(loaded.to_dict(), world.to_dict())


if __name__ == "__main__":
    unittest.main()