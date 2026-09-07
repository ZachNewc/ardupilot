"""
AP_FLAKE8_CLEAN
What the ArduPilot mixer expects of each motor number.

The numbers in ``vector.json`` -- ``function`` and ``test_sequence`` -- are not free
choices. They come from the motor table the firmware builds for a given FRAME_CLASS and
FRAME_TYPE, and getting one wrong is silent: the mixer drives an output the config
thinks belongs to another arm, or drives a motor number that does not exist and simply
never moves.

Rather than trust that the config was transcribed correctly, the table is repeated here
so it can be checked. The source is ``AP_MotorsMatrix::setup_octaquad_matrix()`` in
``libraries/AP_Motors/AP_MotorsMatrix.cpp``.

Rotation direction is derived, not chosen. ArduPilot's yaw factor is
``AP_MOTORS_MATRIX_YAW_FACTOR_CCW = +1`` for a propeller turning counter-clockwise seen
from above, because such a propeller applies a clockwise reaction torque to the airframe
and clockwise-from-above is positive yaw. So the yaw factor in the firmware table states
which way each propeller has to turn, and for this airframe that resolves to every
bottom motor CCW and every top motor CW -- which is also what makes a coaxial pair cancel
its own torque.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Optional

# FRAME_CLASS / FRAME_TYPE values this module knows about.
FRAME_CLASS_OCTAQUAD = 4
FRAME_TYPE_PLUS = 0

CCW = "ccw"
CW = "cw"


class MotorSlot(NamedTuple):
    """One slot in the firmware's motor table."""

    number: int
    """Motor number. ``SERVOn_FUNCTION`` is 32 + this, so Motor1 is 33."""

    azimuth_deg: float
    """Where the firmware believes this motor sits, degrees clockwise from the nose."""

    spin: str
    """Which way the propeller must turn, seen from above."""

    test_sequence: int
    """The number MAV_CMD_DO_MOTOR_TEST expects for this motor."""

    @property
    def function(self) -> int:
        return 32 + self.number


# AP_MotorsMatrix::setup_octaquad_matrix(), MOTOR_FRAME_TYPE_PLUS. Each firmware entry is
# {angle, yaw_factor, testing_order}; the motor number is its position in that list.
# Firmware writes west as -90; normalised to 270 here to match the config's azimuths.
_OCTAQUAD_PLUS = (
    MotorSlot(1, 0, CCW, 1),
    MotorSlot(2, 270, CW, 7),
    MotorSlot(3, 180, CCW, 5),
    MotorSlot(4, 90, CW, 3),
    MotorSlot(5, 270, CCW, 8),
    MotorSlot(6, 0, CW, 2),
    MotorSlot(7, 90, CCW, 4),
    MotorSlot(8, 180, CW, 6),
)

_TABLES: Dict[tuple, tuple] = {
    (FRAME_CLASS_OCTAQUAD, FRAME_TYPE_PLUS): _OCTAQUAD_PLUS,
}


def table(frame_class: int, frame_type: int) -> Optional[Dict[int, MotorSlot]]:
    """
    The motor table for a frame, keyed by motor number, or None if unknown.

    Returning None rather than raising matters: an unrecognised frame means the checks
    that depend on this table cannot run, which is different from them failing.
    """
    slots = _TABLES.get((int(frame_class), int(frame_type)))
    if slots is None:
        return None
    return {slot.number: slot for slot in slots}


def slot_for_function(frame_class: int, frame_type: int, function: int) -> Optional[MotorSlot]:
    """The table entry a ``SERVOn_FUNCTION`` value refers to, or None."""
    slots = table(frame_class, frame_type)
    if slots is None:
        return None
    return slots.get(int(function) - 32)
