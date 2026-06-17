#!/bin/sh

# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

set -e

python virtual_tryon/catalog/init_db.py

exec uvicorn virtual_tryon.app:app --host 0.0.0.0 --port 8088
