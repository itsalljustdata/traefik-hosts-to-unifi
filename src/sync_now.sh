#!/bin/bash
if [ -z "$1" ]; then
    theAction="sync"
else
    theAction="$1"
fi
thisDir=$(dirname "$0")
"$thisDir/entrypoint.sh" --action=${theAction}