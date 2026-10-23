#!/usr/bin/env bash
set +e
env LD_DEBUG=libs LD_DEBUG_OUTPUT=/work/loader "$@"
result=$?
printf '%s\n' "$result" > /work/cp2k.exit
exit "$result"
