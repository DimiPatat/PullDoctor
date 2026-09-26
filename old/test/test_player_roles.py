"""
test_player_roles.py

Covers the fix for the reported bug: Blood Death Knight and Vengeance
Demon Hunter (both TANK specs) showing up classified as healers/dps
instead of tanks. Confirms known tank/healer specs are ALWAYS
classified correctly, even in the exact scenario that causes the real
bug: WCL's own per-pull "tanks"/"healers"/"dps" bucketing is behavior-
based, not identity-based, so a tank spec can get auto-sorted into the
"dps" bucket for one specific pull.
"""
import unittest

import player_roles


class TestKnownTankSpecsAlwaysClassifyAsTank(unittest.TestCase):
    def test_blood_dk_in_tanks_bucket(self):
        raw = {"tanks": [{"id": 1, "name": "Tankryte", "icon": "DeathKnight-Blood"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "tank")

    def test_vengeance_dh_in_tanks_bucket(self):
        raw = {"tanks": [{"id": 2, "name": "Illidarw", "icon": "DemonHunter-Vengeance"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[2].role, "tank")

    def test_blood_dk_misclassified_into_dps_bucket_still_reads_as_tank(self):
        raw = {"dps": [{"id": 1, "name": "Tankryte", "icon": "DeathKnight-Blood"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "tank")
        self.assertNotEqual(roles[1].role, "healer")
        self.assertNotEqual(roles[1].role, "ranged")

    def test_vengeance_dh_misclassified_into_dps_bucket_still_reads_as_tank(self):
        raw = {"dps": [{"id": 2, "name": "Illidarw", "icon": "DemonHunter-Vengeance"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[2].role, "tank")

    def test_all_six_known_tank_specs_classify_as_tank_from_dps_bucket(self):
        tank_specs = [
            "Warrior-Protection", "Paladin-Protection", "DeathKnight-Blood",
            "Monk-Brewmaster", "Druid-Guardian", "DemonHunter-Vengeance",
        ]
        for index, spec in enumerate(tank_specs, start=1):
            raw = {"dps": [{"id": index, "name": f"Player{index}", "icon": spec}]}
            roles = player_roles.parse_player_roles(raw)
            self.assertEqual(roles[index].role, "tank", f"{spec} should classify as tank")


class TestKnownHealerSpecsAlwaysClassifyAsHealer(unittest.TestCase):
    def test_resto_druid_in_healers_bucket(self):
        raw = {"healers": [{"id": 1, "name": "Drdaroou", "icon": "Druid-Restoration"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "healer")

    def test_holy_paladin_misclassified_into_dps_bucket_still_reads_as_healer(self):
        raw = {"dps": [{"id": 3, "name": "Stárkk", "icon": "Paladin-Holy"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[3].role, "healer")

    def test_all_seven_known_healer_specs_classify_as_healer_from_dps_bucket(self):
        healer_specs = [
            "Paladin-Holy", "Priest-Holy", "Priest-Discipline", "Druid-Restoration",
            "Shaman-Restoration", "Monk-Mistweaver", "Evoker-Preservation",
        ]
        for index, spec in enumerate(healer_specs, start=1):
            raw = {"dps": [{"id": index, "name": f"Healer{index}", "icon": spec}]}
            roles = player_roles.parse_player_roles(raw)
            self.assertEqual(roles[index].role, "healer", f"{spec} should classify as healer")


class TestNoCrossContaminationBetweenTankAndHealer(unittest.TestCase):
    def test_tank_never_classified_as_healer_even_if_in_healers_bucket_by_mistake(self):
        raw = {"healers": [{"id": 1, "name": "Tankryte", "icon": "DeathKnight-Blood"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "tank")

    def test_healer_never_classified_as_tank_even_if_in_tanks_bucket_by_mistake(self):
        raw = {"tanks": [{"id": 1, "name": "Drdaroou", "icon": "Druid-Restoration"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "healer")

    def test_mixed_roster_all_correctly_separated(self):
        raw = {
            "tanks": [{"id": 1, "name": "Tankryte", "icon": "Warrior-Protection"}],
            "healers": [{"id": 3, "name": "Drdaroou", "icon": "Priest-Holy"}],
            "dps": [
                {"id": 2, "name": "Illidarw", "icon": "DemonHunter-Vengeance"},
                {"id": 5, "name": "Stárkk", "icon": "Warrior-Fury"},
                {"id": 6, "name": "Desaros", "icon": "Mage-Fire"},
            ],
        }
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "tank")
        self.assertEqual(roles[2].role, "tank")
        self.assertEqual(roles[3].role, "healer")
        self.assertEqual(roles[5].role, "melee")
        self.assertEqual(roles[6].role, "ranged")
        self.assertEqual(sum(1 for r in roles.values() if r.role == "tank"), 2)
        self.assertEqual(sum(1 for r in roles.values() if r.role == "healer"), 1)


class TestOrdinaryDpsUnaffected(unittest.TestCase):
    def test_melee_dps_still_classifies_correctly(self):
        raw = {"dps": [{"id": 1, "name": "Stárkk", "icon": "Warrior-Fury"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "melee")

    def test_ranged_dps_still_classifies_correctly(self):
        raw = {"dps": [{"id": 1, "name": "Desaros", "icon": "Mage-Fire"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "ranged")

    def test_unrecognized_spec_defaults_to_ranged(self):
        raw = {"dps": [{"id": 1, "name": "NewClass", "icon": "SomeBrandNewSpec-Whatever"}]}
        roles = player_roles.parse_player_roles(raw)
        self.assertEqual(roles[1].role, "ranged")


if __name__ == "__main__":
    unittest.main()
