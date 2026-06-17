# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

"""Label mapping from ATR / LIP taxonomy to FASHN's 18-class taxonomy.

The alternative human-parsing models (basso4/humanparsing) output ATR or LIP
labels.  This module maps those raw IDs to the IDs expected by fashn-vton.
"""

from typing import Dict

# ATR dataset label IDs (from basso4/humanparsing parsing_atr.onnx)
# 0 background, 1 hat, 2 hair, 3 sunglasses, 4 upper_clothes, 5 skirt,
# 6 pants, 7 dress, 8 belt, 9 left_shoe, 10 right_shoe, 11 bag,
# 12 scarf, 13 face, 14 left_arm, 15 right_arm, 16 left_leg, 17 right_leg
_ATR_TO_FASHN: Dict[int, int] = {
    0: 0,   # background -> background
    1: 9,   # hat -> hat
    2: 2,   # hair -> hair
    3: 11,  # sunglasses -> glasses
    4: 3,   # upper_clothes -> top
    5: 5,   # skirt -> skirt
    6: 6,   # pants -> pants
    7: 4,   # dress -> dress
    8: 7,   # belt -> belt
    9: 15,  # left_shoe -> feet
    10: 15, # right_shoe -> feet
    11: 8,  # bag -> bag
    12: 10, # scarf -> scarf
    13: 1,  # face -> face
    14: 12, # left_arm -> arms
    15: 12, # right_arm -> arms
    16: 14, # left_leg -> legs
    17: 14, # right_leg -> legs
}

# NumPy array form for vectorised remapping (built on first import)
_ATR_LOOKUP = None


def remap_atr_to_fashn(seg_atr: "np.ndarray") -> "np.ndarray":
    """Remap an ATR segmentation array to FASHN 18-class IDs.

    Args:
        seg_atr: Numpy array of shape (H, W) with ATR label IDs 0-17.

    Returns:
        Numpy array of shape (H, W) with FASHN label IDs 0-17.
    """
    global _ATR_LOOKUP
    import numpy as np

    if _ATR_LOOKUP is None:
        _ATR_LOOKUP = np.zeros(256, dtype=np.uint8)
        for atr_id, fashn_id in _ATR_TO_FASHN.items():
            _ATR_LOOKUP[atr_id] = fashn_id

    return _ATR_LOOKUP[seg_atr]
