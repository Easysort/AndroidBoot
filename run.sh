#!/bin/bash

show_usage() {
  echo "Usage: $0 -a|--applications <app> \n <app> can be: container, entrance"; exit 1
}

[ "$1" = "-a" ] || [ "$1" = "--applications" ] && APP="$2" || show_usage

if [ -z "$APP" ]; then
    show_usage
fi

export PYTHONPATH="."
# verify everything is correctly setup
pytest applications/common/verify.py

case "$APP" in
  container) SCRIPT_PATH="$HOME/AndroidBoot/applications/container/tmux-starter.sh" ;;
  entrance) SCRIPT_PATH="$HOME/AndroidBoot/applications/entrance/tmux-starter.sh" ;;
  test) termux-camera-photo -c 0 $HOME/AndroidBoot/test.jpg; echo "Image available at $HOME/AndroidBoot/test.jpg"; exit $? ;;
  *) echo "Unknown application: $APP"; show_usage ;;
esac

termux-job-scheduler --job-id 700 --script "$SCRIPT_PATH" --period-ms 900000 --persisted true --network any --battery-not-low false