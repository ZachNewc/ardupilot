"""
AP_FLAKE8_CLEAN
The config's motor numbers have to agree with the frame the firmware builds.

Every failure mode here is silent on the vehicle. A motor number that belongs to a
different position spins the wrong arm; one that does not exist on the frame never spins
at all and reports nothing. Checking it against the firmware's own table is the only way
to catch a transcription error before a propeller does it for us.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard"))

from server import config as vconfig      # noqa: E402
from server import frames                # noqa: E402


class TestFrameTable(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = vconfig.load()
        frame = (self.cfg.raw.get("vehicle") or {}).get("frame") or {}
        self.frame_class = frame.get("frame_class")
        self.frame_type = frame.get("frame_type")
        self.slots = frames.table(self.frame_class, self.frame_type)

    def test_the_configured_frame_is_one_we_have_a_table_for(self) -> None:
        self.assertIsNotNone(
            self.slots,
            f"no motor table for FRAME_CLASS {self.frame_class} / FRAME_TYPE "
            f"{self.frame_type}, so none of the motor numbers can be checked",
        )

    def test_every_motor_function_exists_on_this_frame(self) -> None:
        for arm in self.cfg.arms:
            for name, motor in arm.motors.items():
                if motor.function is None:
                    continue
                with self.subTest(arm=arm.id, motor=name):
                    self.assertIsNotNone(
                        frames.slot_for_function(
                            self.frame_class, self.frame_type, motor.function
                        ),
                        f"Motor{motor.function - 32} is not on this frame; it would "
                        "never be driven",
                    )

    def test_test_sequence_matches_the_frames_test_order(self) -> None:
        """A wrong test order spins a different propeller than the one named."""
        for arm in self.cfg.arms:
            for name, motor in arm.motors.items():
                if motor.function is None or motor.test_sequence is None:
                    continue
                slot = frames.slot_for_function(
                    self.frame_class, self.frame_type, motor.function
                )
                with self.subTest(arm=arm.id, motor=name):
                    self.assertEqual(motor.test_sequence, slot.test_sequence)

    def test_each_motor_sits_where_its_arm_does(self) -> None:
        """
        The firmware places each motor number at an azimuth, which fixes its roll and
        pitch authority. A number borrowed from another position leans the wrong way.
        """
        for arm in self.cfg.arms:
            for name, motor in arm.motors.items():
                if motor.function is None:
                    continue
                slot = frames.slot_for_function(
                    self.frame_class, self.frame_type, motor.function
                )
                with self.subTest(arm=arm.id, motor=name):
                    self.assertEqual(
                        slot.azimuth_deg % 360,
                        arm.azimuth_deg % 360,
                        f"Motor{slot.number} is a {slot.azimuth_deg}deg motor but "
                        f"{arm.id} is at {arm.azimuth_deg}deg",
                    )

    def test_each_coaxial_pair_counter_rotates(self) -> None:
        """
        The whole point of the coaxial pair is that its torques cancel. If the frame
        asked both motors on an arm to turn the same way, the airframe would inherit
        their combined torque and the design would not work.
        """
        for arm in self.cfg.arms:
            spins = {}
            for name, motor in arm.motors.items():
                if motor.function is None:
                    continue
                slot = frames.slot_for_function(
                    self.frame_class, self.frame_type, motor.function
                )
                spins[name] = slot.spin
            if len(spins) == 2:
                with self.subTest(arm=arm.id):
                    self.assertEqual(
                        len(set(spins.values())), 2,
                        f"{arm.id} has both motors turning {list(spins.values())[0]}",
                    )

    def test_no_two_motors_share_a_number(self) -> None:
        used = {}
        for arm in self.cfg.arms:
            for name, motor in arm.motors.items():
                if motor.function is None:
                    continue
                where = f"{arm.id}.{name}"
                self.assertNotIn(
                    motor.function, used,
                    f"{where} and {used.get(motor.function)} are both "
                    f"Motor{motor.function - 32}",
                )
                used[motor.function] = where


if __name__ == "__main__":
    unittest.main()
