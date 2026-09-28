"""Bone-name maps between the CS2 agent rig and the BL2 player rigs (body + first-person arms).

``WEIGHT_MAP``: CS2 bone -> BL2 bone that receives its skin weights.  A CS2 bone that is not
listed, or whose BL2 bone is absent from the target RefSkeleton, feeds the nearest ancestor that
resolves (fingers on the body mesh end up on the hands; twist bones on their limb).

``FIT``: CS2 bone -> (BL2 bone whose rest position pins this bone's head, CS2 child whose
direction is aligned, BL2 child giving the target direction).  A ``None`` child means position
only (rotation follows the parent's delta).  Entries whose BL2 bone is absent from the target
skeleton are skipped, so the same table serves the 27-bone body and the 47-bone arms.
"""
from __future__ import annotations

WEIGHT_MAP: dict[str, str] = {
    "root_motion": "Root", "pelvis": "Hips", "spine_0": "Spine1", "spine_1": "Spine2",
    "spine_2": "Spine3", "spine_3": "Spine3", "neck_0": "Neck", "head_0": "Head",
    "clavicle_L": "L_Clav", "arm_upper_L": "L_Upperarm", "arm_lower_L": "L_Forearm", "hand_L": "L_Hand",
    "clavicle_R": "R_Clav", "arm_upper_R": "R_Upperarm", "arm_lower_R": "R_Forearm", "hand_R": "R_Hand",
    "arm_lower_L_TWIST": "L_Forearm_Twist", "arm_lower_L_TWIST1": "L_Forearm_Twist",
    "arm_lower_R_TWIST": "R_Forearm_Twist", "arm_lower_R_TWIST1": "R_Forearm_Twist",
    "leg_upper_L": "L_Thigh", "leg_lower_L": "L_Shin", "ankle_L": "L_Foot", "ball_L": "L_Toe",
    "leg_upper_R": "R_Thigh", "leg_lower_R": "R_Shin", "ankle_R": "R_Foot", "ball_R": "R_Toe",
    # Deadlock hero rigs (same family as the CS2 agent rig, a few different names / extras)
    "head": "Head", "neck_0_TWIST": "Neck", "skirt_offset": "Hips",
    "scapula_0_L": "L_Clav", "scapula_0_R": "R_Clav",
}

FIT: dict[str, tuple[str, str | None, str | None]] = {
    "pelvis":      ("Hips",       "spine_0",  "Spine1"),
    "spine_0":     ("Spine1",     "spine_1",  "Spine2"),
    "spine_1":     ("Spine2",     "spine_3",  "Spine3"),
    "spine_3":     ("Spine3",     "neck_0",   "Neck"),
    "neck_0":      ("Neck",       "head_0",   "Head"),
    "head_0":      ("Head",       "@frame", None),   # full frame: Krieg's head sits ~20 deg forward, his neck bone 51
    "head":        ("Head",       "@frame", None),   # Deadlock name for the same bone
    "clavicle_L":  ("L_Clav",     "arm_upper_L", "L_Upperarm"),
    "arm_upper_L": ("L_Upperarm", "arm_lower_L", "L_Forearm"),
    "arm_lower_L": ("L_Forearm",  "hand_L",   "L_Hand"),
    "hand_L":      ("L_Hand",     None, None),
    "clavicle_R":  ("R_Clav",     "arm_upper_R", "R_Upperarm"),
    "arm_upper_R": ("R_Upperarm", "arm_lower_R", "R_Forearm"),
    "arm_lower_R": ("R_Forearm",  "hand_R",   "R_Hand"),
    "hand_R":      ("R_Hand",     None, None),
    "leg_upper_L": ("L_Thigh",    "leg_lower_L", "L_Shin"),
    "leg_lower_L": ("L_Shin",     "ankle_L",  "L_Foot"),
    "ankle_L":     ("L_Foot",     "ball_L",   "L_Toe"),
    "ball_L":      ("L_Toe",      None, None),
    "leg_upper_R": ("R_Thigh",    "leg_lower_R", "R_Shin"),
    "leg_lower_R": ("R_Shin",     "ankle_R",  "R_Foot"),
    "ankle_R":     ("R_Foot",     "ball_R",   "R_Toe"),
    "ball_R":      ("R_Toe",      None, None),
}

#: joints whose FIT entry aligns direction only: the head of the bone rigid-follows its parent
#: instead of being pinned to the BL2 joint.  Fingers: BL2 first-person hands are ~50 % longer
#: than a Deadlock/CS2 hand, and pinning every knuckle stretched the fingers into claws.
NO_PIN: set[str] = set()

# Fingers: CS2 finger_<name>_<seg>_<side> -> BL2 <side>_finger<N><seg-suffix>
# BL2: finger0 = thumb, 1 = index, 2 = middle, 3 = ring, 4 = pinky; segments N, N1, N2.
_FINGERS = {"thumb": 0, "index": 1, "middle": 2, "ring": 3, "pinky": 4}
for _side in ("L", "R"):
    for _cs2, _n in _FINGERS.items():
        _bl2 = [f"{_side}_finger{_n}", f"{_side}_finger{_n}1", f"{_side}_finger{_n}2"]
        _cs = [f"finger_{_cs2}_{seg}_{_side}" for seg in range(3)]
        for seg in range(3):
            WEIGHT_MAP[_cs[seg]] = _bl2[seg]
            nxt = (_cs[seg + 1], _bl2[seg + 1]) if seg < 2 else (None, None)
            FIT[_cs[seg]] = (_bl2[seg], nxt[0], nxt[1])
            NO_PIN.add(_cs[seg])
        WEIGHT_MAP[f"finger_{_cs2}_meta_{_side}"] = f"{_side}_Hand"
