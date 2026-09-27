#!/usr/bin/env bash
# Double-click this file in Finder to start Naukri Job Hunter.
cd "$(dirname "$0")" || exit 1
clear
bash ./run.sh web
status=$?
echo
read -r -p "Press Enter to close this window..." _
exit $status
