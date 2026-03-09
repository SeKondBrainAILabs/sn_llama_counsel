#!/bin/bash
# post-build.sh — clean up intermediate build artefacts from frontend-dist
# (Adapted from llama.cpp original which cleaned ../public — we output to ../frontend-dist)
rm -rf ../frontend-dist/_app/immutable/chunks/*.map 2>/dev/null || true
