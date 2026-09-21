#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

"""Patch fashn_vton's DWPose to use MIGraphX (ROCm) instead of only CUDA.

Locates the target file by path instead of `import fashn_vton`, since that
package's __init__.py imports a module not yet copied into the image at this build step.
"""

import glob
import pathlib
import site

candidates = []
for sp in [*site.getsitepackages(), site.getusersitepackages()]:
    candidates.extend(glob.glob(f"{sp}/fashn_vton/dwpose/wholebody.py"))
assert candidates, (
    "DWPose ROCm patch: could not find fashn_vton/dwpose/wholebody.py in "
    "any site-packages directory — was fashn_vton installed?"
)
path = pathlib.Path(candidates[0])

OLD = '''        if device.startswith("cuda"):
            device_id = int(device.split(":")[-1])
            provider_options = [{"device_id": str(device_id)}]
            providers = ["CUDAExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
            provider_options = None'''

NEW = '''        available = ort.get_available_providers()
        if device.startswith("cuda") and "CUDAExecutionProvider" in available:
            device_id = int(device.split(":")[-1])
            provider_options = [{"device_id": str(device_id)}]
            providers = ["CUDAExecutionProvider"]
        elif device.startswith("cuda") and "MIGraphXExecutionProvider" in available:
            device_id = int(device.split(":")[-1])
            provider_options = [{"device_id": str(device_id)}]
            providers = ["MIGraphXExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
            provider_options = None'''

src = path.read_text()
assert OLD in src, (
    f"DWPose ROCm patch: provider-selection block not found in {path} — "
    "upstream fashn_vton source has changed shape, update this patch."
)
path.write_text(src.replace(OLD, NEW))
print(f"DWPose ROCm execution-provider patch applied to {path}")
