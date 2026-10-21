#!/bin/zsh
if [ -d "/Applications/MATLAB_R2026a.app" ]; then
    MATLAB="/Applications/MATLAB_R2026a.app"
elif [ -d "$HOME/Applications/MATLAB_R2026a.app" ]; then
    MATLAB="$HOME/Applications/MATLAB_R2026a.app"
else
    echo "MATLAB_R2026a.app not found!"
    exit 1
fi
cd "$(dirname "$0")"
exec "$MATLAB/bin/matlab" -r "cd('$(pwd)'); Final_Code"
